#!/usr/bin/env python3
"""Resolve one immutable FinaSU release and schedule exact kernel recipes."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def gh(*args, payload=None, optional=False):
    command = ["gh", *args]
    if payload is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        if optional and "404" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def target_key(target, policy, release):
    return digest({"target": target, "policy": policy, "release": release})


def select_targets(targets, policy, release, state, *, only="", force=False, now=None):
    now = now or datetime.now(timezone.utc)
    requested = set(only.split(",")) if only else set()
    unknown = requested - {t["id"] for t in targets}
    if unknown:
        raise ValueError("Unknown targets: " + ", ".join(sorted(unknown)))
    selected, notes = [], []
    seen = set()
    for target in targets:
        if requested and target["id"] not in requested:
            continue
        if not target["enabled"]:
            notes.append({"id": target["id"], "status": "source-pending", "reason": target["source"].get("reason", "")})
            continue
        # Release text and exact config participate in deduplication, not account ids.
        key = target_key(target, policy, release)
        prior = state.get("targets", {}).get(target["id"], {})
        if not force and prior.get("key") == key:
            if prior.get("status") == "success":
                notes.append({"id": target["id"], "status": "already-built"})
                continue
            attempts = prior.get("attempts", 0)
            last = datetime.fromisoformat(prior["updated_at"])
            if attempts >= policy["max_attempts"] or now < last + timedelta(hours=policy["retry_hours"]):
                notes.append({"id": target["id"], "status": "retry-wait", "attempts": attempts})
                continue
        identity = digest({k: target.get(k) for k in ["release", "source", "config_sha256", "symvers_sha256", "page_size"]})
        if identity in seen:
            notes.append({"id": target["id"], "status": "duplicate-recipe"})
            continue
        seen.add(identity)
        selected.append({**target, "build_key": key})
    return selected, notes


def resolve_release(repository):
    release = gh("api", f"repos/{repository}/releases/latest")
    if release["draft"] or release["prerelease"]:
        raise ValueError("Expected a published stable release")
    tag = release["tag_name"]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", tag):
        raise ValueError("Unsupported release tag")
    commit = gh("api", f"repos/{repository}/commits/{tag}")["sha"]
    apks = [a for a in release["assets"] if a["name"].endswith(".apk") and "release" in a["name"]]
    if len(apks) != 1:
        raise ValueError("Release must identify exactly one release manager APK")
    asset = apks[0]
    return {"repository": repository, "tag": tag, "commit": commit,
            "release_id": release["id"], "manager_asset_id": asset["id"],
            "manager_name": asset["name"], "manager_url": asset["browser_download_url"],
            "manager_digest": asset.get("digest"), "published_at": release["published_at"]}


def read_state(repo):
    obj = gh("api", f"repos/{repo}/contents/state.json?ref=finasu-build-state", optional=True)
    if obj is None:
        return {"schema": 1, "targets": {}}, None
    import base64
    return json.loads(base64.b64decode(obj["content"])), obj["sha"]


def write_state(repo, state):
    import base64
    branch = gh("api", f"repos/{repo}/git/ref/heads/finasu-build-state", optional=True)
    if branch is None:
        sha = gh("api", f"repos/{repo}/commits/main")["sha"]
        gh("api", f"repos/{repo}/git/refs", "--method", "POST",
           payload={"ref": "refs/heads/finasu-build-state", "sha": sha})
    _, sha = read_state(repo)
    data = {"message": "chore(fleet): record FinaSU build results", "branch": "finasu-build-state",
            "content": base64.b64encode((json.dumps(state, indent=2) + "\n").encode()).decode()}
    if sha:
        data["sha"] = sha
    gh("api", f"repos/{repo}/contents/state.json", "--method", "PUT", payload=data)


def plan(args):
    policy = load_json(ROOT / "fleet/policy.json")
    targets = load_json(ROOT / "fleet/targets.json")["targets"]
    release = resolve_release(policy["upstream_repository"])
    state, _ = read_state(args.repository)
    selected, notes = select_targets(targets, policy, release, state, only=args.only, force=args.force)
    plan_data = {"schema": 1, "release": release, "policy": policy, "targets": selected, "notes": notes,
                 "catalog_fingerprint": digest({"targets": targets, "policy": policy, "release": release})}
    Path("fleet-plan.json").write_text(json.dumps(plan_data, indent=2) + "\n")
    matrix = {"include": [{"id": t["id"], "android": t["android"], "series": t["series"],
                            "sublevel": t["sublevel"], "patch_level": t["patch_level"],
                            "key": t["build_key"]} for t in selected]}
    output = {"has_work": str(bool(selected)).lower(), "matrix": json.dumps(matrix, separators=(",", ":")),
              "ksu_commit": release["commit"]}
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            for key, value in output.items():
                stream.write(f"{key}={value}\n")
    summary = f"FinaSU {release['tag']} ({release['commit'][:12]}): {len(selected)} targets queued.\n"
    summary += "\n".join(f"- {n['id']}: {n['status']} {n.get('reason', '')}" for n in notes)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary + "\n")


def finish(args):
    plan_data = load_json("fleet-plan.json")
    state, _ = read_state(args.repository)
    now = datetime.now(timezone.utc).isoformat()
    results = {}
    for file in Path(args.results).rglob("result.json"):
        result = load_json(file)
        results[result["target"]] = result
    lines = [f"FinaSU {plan_data['release']['tag']} build results", "", "| Target | Result |", "|---|---|"]
    successful = []
    for target in plan_data["targets"]:
        result = results.get(target["id"], {"status": "failure", "reason": "No successful result receipt"})
        prior = state["targets"].get(target["id"], {})
        attempts = prior.get("attempts", 0) + 1 if prior.get("key") == target["build_key"] else 1
        record = {"key": target["build_key"], "status": result["status"], "updated_at": now,
                  "attempts": attempts, "run_id": os.environ.get("GITHUB_RUN_ID"),
                  "release": plan_data["release"], "result": result}
        state["targets"][target["id"]] = record
        if result["status"] == "success":
            successful.append(target)
        lines.append(f"| {target['id']} | {result['status']} |")
    if successful:
        tag = "finasu-" + plan_data["release"]["tag"] + "-" + plan_data["catalog_fingerprint"][:8]
        if gh("api", f"repos/{args.repository}/releases/tags/{tag}", optional=True) is None:
            notes = Path("release-notes.md")
            notes.write_text("FinaSU + SUSFS kernels built for registered kernel profiles.\n\n"
                             "Build and static configuration validation completed for uploaded packages. Device boot acceptance is separate.\n\n"
                             + "\n".join(lines) + "\n")
            subprocess.run(["gh", "release", "create", tag, "-R", args.repository, "--target", os.environ["GITHUB_SHA"],
                            "--title", f"FinaSU {plan_data['release']['tag']} exact kernels", "--prerelease",
                            "--notes-file", str(notes)], check=True)
        assets = []
        for target in successful:
            assets += [str(p) for p in Path(args.packages).rglob(f"*{target['id']}*") if p.is_file()]
        assets += [str(p) for p in Path(args.manager).rglob("*.apk")]
        if assets:
            subprocess.run(["gh", "release", "upload", tag, "-R", args.repository, "--clobber", *assets], check=True)
        for target in successful:
            state["targets"][target["id"]]["published_tag"] = tag
    state["updated_at"] = now
    write_state(args.repository, state)
    summary = "\n".join(lines) + "\n"
    Path("fleet-summary.md").write_text(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary)
    print(summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--repository", required=True)
    p.add_argument("--only", default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=plan)
    p = sub.add_parser("finish")
    p.add_argument("--repository", required=True)
    p.add_argument("--results", default="fleet-results")
    p.add_argument("--packages", default="fleet-packages")
    p.add_argument("--manager", default="fleet-manager")
    p.set_defaults(func=finish)
    args = parser.parse_args()
    args.func(args)
