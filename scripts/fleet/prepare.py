#!/usr/bin/env python3
"""Bind the existing workflow to one recorded complete-kernel recipe."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def checkout(path, url, commit):
    if not (path / ".git").exists():
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", str(path)], check=True)
    current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True)
    if current.returncode or current.stdout.strip() != commit:
        subprocess.run(["git", "fetch", "--depth", "1", url, commit], cwd=path, check=True)
        subprocess.run(["git", "checkout", "--detach", "--force", commit], cwd=path, check=True)
    assert run("git", "rev-parse", "HEAD", cwd=path) == commit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    args = parser.parse_args()
    target = next(t for t in json.loads((ROOT / "fleet/targets.json").read_text())["targets"] if t["id"] == args.target)
    assert target["enabled"]
    policy = json.loads((ROOT / "fleet/policy.json").read_text())
    kernel_root = Path(os.environ["KERNEL_ROOT"])
    source = kernel_root / "common"
    checkout(source, target["source"]["url"], target["source"]["commit"])
    makefile = (source / "Makefile").read_text()
    actual = ".".join(re.search(rf"^{k} = (\d+)$", makefile, re.M)[1] for k in ["VERSION", "PATCHLEVEL", "SUBLEVEL"])
    assert actual == f"{target['series']}.{target['sublevel']}", actual
    for patch in target['source'].get('patches', []):
        file = ROOT / patch['path']
        assert hashlib.sha256(file.read_bytes()).hexdigest() == patch['sha256']
        subprocess.run(['git', 'apply', '--check', str(file)], cwd=source, check=True)
        subprocess.run(['git', 'apply', str(file)], cwd=source, check=True)
    vendor = target["source"].get("vendor")
    if vendor:
        modules = kernel_root / "vendor-source"
        checkout(modules, vendor["url"], vendor["commit"])
        link = kernel_root.parent / "vendor"
        if link.is_symlink():
            link.unlink()
        assert not link.exists(), "Unexpected vendor directory"
        link.symlink_to(modules / "vendor", target_is_directory=True)
        # Resolve checked-in relative links in the OEM source at its native depth.
        for name in run("git", "ls-files", cwd=source).splitlines():
            file = source / name
            if file.is_symlink() and not file.exists():
                raise ValueError(f"Unresolved vendor source link: {name} -> {file.readlink()}")
    config = ROOT / target["config"]
    assert hashlib.sha256(config.read_bytes()).hexdigest() == target["config_sha256"]
    shutil.copyfile(config, Path(os.environ["DEFCONFIG"]))
    for name, dependency in policy["dependencies"].items():
        checkout(ROOT / name, dependency["url"], dependency["commit"])
    axis = target["android"] + "-" + target["series"]
    checkout(Path(os.environ["SUSFS4KSU"]), "https://gitlab.com/simonpunk/susfs4ksu.git", policy["susfs"][axis])
    susfs_header = Path(os.environ["SUSFS4KSU"]) / "kernel_patches/include/linux/susfs.h"
    assert '#define SUSFS_VERSION "v2.4.0"' in susfs_header.read_text()
    bbg = kernel_root / "Baseband-guard"
    checkout(bbg, "https://github.com/vc-teahouse/Baseband-guard.git", "028ff7fd509e2104596da7cc1ef8e6ce2a0c9082")
    with open(os.environ["GITHUB_ENV"], "a") as stream:
        stream.write(f"FLEET_BBG_SETUP={bbg / 'setup.sh'}\n")
        stream.write("FLEET_BBG_COMMIT=028ff7fd509e2104596da7cc1ef8e6ce2a0c9082\n")
        stream.write(f"CONFIG={target['id']}\n")
    (ROOT / "fleet-target.json").write_text(json.dumps(target, indent=2) + "\n")
    print(f"Pinned {target['id']} to {target['source']['commit']}; {actual}; SUSFS v2.4.0")


if __name__ == "__main__":
    main()
