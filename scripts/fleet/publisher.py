"""Publish user-facing ZIPs and the matched APK in one normal GitHub Release."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote
import zipfile


def manager_build(release):
    match = re.fullmatch(r"FinaSU_(.+)_(\d+)-release\.apk", release["manager_name"])
    if not match or match[1] != release["tag"]:
        raise ValueError("Manager filename does not identify the selected FinaSU release")
    return match[2]


def asset_names(targets, release):
    """Use upstream names, retaining the exact suffix when a version collides."""
    build = manager_build(release)
    enabled = [t for t in targets if t["enabled"]]
    axis = lambda t: f"{t['android']}-{t['series']}-{t['sublevel']}"
    counts = Counter(axis(t) for t in enabled)
    names = {}
    for target in enabled:
        version = axis(target)
        if counts[version] > 1:
            prefix = f"{target['series']}.{target['sublevel']}-{target['android']}-"
            if not target["release"].startswith(prefix):
                raise ValueError("Unexpected exact kernel version")
            version += "-" + target["release"][len(prefix):]
        if not re.fullmatch(r"[A-Za-z0-9._-]+", version):
            raise ValueError("Unsupported kernel filename")
        names[target["id"]] = f"FinaSU-{build}-{version}-AnyKernel3.zip"
    if len(set(names.values())) != len(names):
        raise ValueError("Distinct kernel profiles would overwrite the same release asset")
    return names


def next_tag(tags, susfs_version):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", susfs_version):
        raise ValueError("Unsupported SUSFS release version")
    numbers = [int(m[1]) for tag in tags
               if (m := re.fullmatch(re.escape(susfs_version) + r"-r(\d+)", tag))]
    return f"{susfs_version}-r{max(numbers, default=0) + 1}"


def matching_targets(catalog, policy, release, state, key_for):
    return [t for t in catalog if t["enabled"]
            and state.get("targets", {}).get(t["id"], {}).get("status") == "success"
            and state["targets"][t["id"]].get("key") == key_for(t, policy, release)]


def prepare_assets(targets, names, release, state, packages, manager, destination):
    """Select only validated kernels and the exact manager; keep evidence in Actions."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    assets = {}
    build = manager_build(release)
    for target in targets:
        public_name = names[target["id"]]
        legacy = f"FinaSU-{build}-{target['id']}-AnyKernel3.zip"
        candidates = [p for p in Path(packages).rglob("*-AnyKernel3.zip")
                      if p.name in (legacy, public_name)]
        if not candidates:
            continue  # A retry may reuse an already published package below.
        hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in candidates}
        if len(hashes) != 1:
            raise ValueError(f"Conflicting packages for {target['id']}")
        record = state["targets"][target["id"]]["result"]
        if (record["status"] != "success" or record["release"] != target["release"]
                or record["finasu_commit"] != release["commit"]):
            raise ValueError(f"Validation receipt mismatch for {target['id']}")
        with zipfile.ZipFile(candidates[0]) as archive:
            if archive.testzip() is not None:
                raise ValueError("Corrupt kernel archive")
            image = archive.read("Image")
            if (hashlib.sha256(image).hexdigest() != record["image_sha256"]
                    or target["release"].encode() not in image):
                raise ValueError(f"Kernel Image mismatch for {target['id']}")
        path = destination / public_name
        shutil.copyfile(candidates[0], path)
        assets[public_name] = {"path": path, "sha256": hashes.pop()}
    apks = list(Path(manager).rglob(release["manager_name"]))
    if not apks:
        raise ValueError("The matching official manager APK is required for every release")
    hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in apks}
    if len(hashes) != 1 or release.get("manager_digest") != "sha256:" + next(iter(hashes)):
        raise ValueError("Manager APK digest differs from the selected official release")
    with zipfile.ZipFile(apks[0]) as archive:
        if archive.testzip() is not None or "AndroidManifest.xml" not in archive.namelist():
            raise ValueError("Invalid manager APK")
    path = destination / release["manager_name"]
    shutil.copyfile(apks[0], path)
    assets[path.name] = {"path": path, "sha256": hashes.pop()}
    return assets


def release_body(repository, tag, release, policy, targets, names, publication_key):
    base = f"https://github.com/{repository}/releases/download/{tag}/"
    build = manager_build(release)
    lines = ["## GKI Kernel Release", "",
             f"**FinaSU {build} ({release['tag']})** + **SUSFS {policy['options']['susfs_version']}**", "",
             "### 构建配置", "", "| 项目 | 内容 |", "|:---|:---|",
             f"| FinaSU | 内建 · release/{release['tag']} |",
             f"| SUSFS | {policy['options']['susfs_version']} · SUSFS Inline Hooks |",
             "| LTO | ThinLTO |", "| ZRAM / LZ4KD | 开启 |", "| BBG 基带保护 | 开启 |",
             "| 网络增强 | BBRv3 / IPSet / Qdisc / CIFS |",
             "| Re-Kernel / KPM / Droidspaces | 未开启 |", "",
             "### 下载", "",
             f"管理器：[**{release['manager_name']}**]({base + quote(release['manager_name'])})", "",
             "按手机的**完整内核版本**选择下表对应的 ZIP。", "",
             "| 完整内核版本 | AnyKernel3 刷入包 |", "|:---|:---|"]
    for target in sorted(targets, key=lambda t: tuple(int(v) for v in t['series'].split('.'))
                         + (int(t['sublevel']), t['release'])):
        name = names[target["id"]]
        lines.append(f"| `{target['release']}` | [{name}]({base + quote(name)}) |")
    lines += ["", f"共 {len(targets)} 个内核 ZIP + 1 个官方管理器 APK。",
              "同小版本存在不同构建时，文件名保留精确版本后缀。",
              "已通过编译、配置和 ABI 检查；尚未进行刷机验收。", "",
              f"详细构建记录：[GitHub Actions](https://github.com/{repository}/actions/workflows/finasu-fleet.yml)。", "",
              f"<!-- finasu-publication:{publication_key} -->", ""]
    return "\n".join(lines)


def publish(repository, head, catalog, policy, release, state, packages, manager, api, key_for):
    targets = matching_targets(catalog, policy, release, state, key_for)
    if not targets:
        raise ValueError("No validated kernels match this release and build recipe")
    names = asset_names(catalog, release)
    publication_key = hashlib.sha256(json.dumps({"release": release, "policy": policy},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    marker = f"<!-- finasu-publication:{publication_key} -->"
    releases = [r for page in api("api", "--paginate", "--slurp",
        f"repos/{repository}/releases?per_page=100") for r in page]
    matches = [r for r in releases if marker in (r.get("body") or "")]
    if len(matches) > 1:
        raise ValueError("Multiple releases have the same publication identity")
    existing = matches[0] if matches else None
    if existing:
        tag = existing["tag_name"]
    else:
        tags = [t["name"] for page in api("api", "--paginate", "--slurp",
            f"repos/{repository}/tags?per_page=100") for t in page]
        tag = next_tag(tags, policy["options"]["susfs_version"])
    expected = {names[t["id"]] for t in targets} | {release["manager_name"]}
    title = f"GKI Kernel: FinaSU {manager_build(release)} + SUSFS {policy['options']['susfs_version']}"
    body = release_body(repository, tag, release, policy, targets, names, publication_key)
    with tempfile.TemporaryDirectory(prefix="finasu-release-") as temp:
        local = prepare_assets(targets, names, release, state, packages, manager, temp)
        remote = {a["name"]: a for a in (existing or {}).get("assets", [])}
        digests = {name: asset["sha256"] for name, asset in local.items()}
        for target in targets:
            name = names[target["id"]]
            if name in local:
                continue
            sha = state["targets"][target["id"]].get("package_sha256")
            if not sha or remote.get(name, {}).get("digest") != "sha256:" + sha:
                raise ValueError(f"Validated package missing locally and from release: {name}")
            digests[name] = sha
        if existing is None:
            existing = api("api", f"repos/{repository}/releases", "--method", "POST", payload={
                "tag_name": tag, "target_commitish": head, "name": title, "body": body,
                "draft": True, "prerelease": False})
        for name, asset in local.items():
            if remote.get(name, {}).get("digest") == "sha256:" + asset["sha256"]:
                continue
            subprocess.run(["gh", "release", "upload", tag, "-R", repository,
                "--clobber", str(asset["path"])], check=True)
        current = api("api", f"repos/{repository}/releases/{existing['id']}")
        uploaded = {a["name"]: a for a in current["assets"]}
        for name in expected:
            if uploaded.get(name, {}).get("digest") != "sha256:" + digests[name]:
                raise ValueError(f"Published asset digest mismatch: {name}")
        # Only clean a release owned by this publication, after all required files exist.
        for name, asset in uploaded.items():
            if name not in expected:
                api("api", f"repos/{repository}/releases/assets/{asset['id']}", "--method", "DELETE")
        current = api("api", f"repos/{repository}/releases/{existing['id']}", "--method", "PATCH",
            payload={"name": title, "body": body, "draft": False, "prerelease": False, "make_latest": "true"})
        if current["draft"] or current["prerelease"] or {a["name"] for a in current["assets"]} != expected:
            raise ValueError("Published release does not match the public asset list")
    for target in targets:
        record = state["targets"][target["id"]]
        record.update(published_tag=tag, asset_name=names[target["id"]],
                      package_sha256=digests[names[target["id"]]])
    state.setdefault("publications", {})[publication_key] = {"tag": tag, "release_id": current["id"]}
    return {"tag": tag, "url": current["html_url"], "assets": sorted(expected), "targets": len(targets)}
