#!/usr/bin/env python3
"""Export kernel-only records; never export accounts, device transports or packages."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def export(index_path, root=ROOT):
    index = json.loads(Path(index_path).read_text())
    overrides = json.loads((root / "fleet/source-overrides.json").read_text())
    targets = []
    for entry in index["entries"]:
        key = entry["id"]
        if not re.fullmatch(r"[a-z0-9-]+", key):
            raise ValueError(f"Invalid target id: {key}")
        release = entry["kernel_release"]
        match = re.fullmatch(r"(\d+\.\d+)\.(\d+)-(android\d+)-(\d+)-[A-Za-z0-9._-]+", release)
        if not match:
            raise ValueError(f"Unrecognized release: {release}")
        series, sub, android, kmi = match.groups()
        source = overrides.get(key, {"enabled": False, "relation": "unresolved", "reason": "Source mapping has not been reviewed."})
        version_data = json.loads((root / f"data/{android}/{series}.json").read_text())
        matches = [v["date"] for v in version_data["entries"] if v["kernel"] == f"{series}.{sub}"]
        if not matches:
            raise ValueError(f"No build manifest for {key}: {series}.{sub}")
        target = {
            "id": key, "enabled": source.get("enabled", True),
            "release": release, "android": android, "series": series,
            "sublevel": sub, "patch_level": matches[-1], "kmi_generation": int(kmi),
            "page_size": entry["page_size"], "source": source,
        }
        if target["enabled"]:
            prepared = Path(entry["kernel_out"])
            config = (prepared / ".config").read_text()
            # Remove the previous root implementation; new integration is explicit.
            config = "\n".join(line for line in config.splitlines()
                               if not re.match(r"(?:# )?CONFIG_(?:KSU|KPM|DFM|REKERNEL|NOMOUNT)(?:_|=|\s)", line)) + "\n"
            config = re.sub(r'^CONFIG_SYSTEM_TRUSTED_KEYS=.*$', 'CONFIG_SYSTEM_TRUSTED_KEYS=""', config, flags=re.M)
            config = re.sub(r'^CONFIG_MODULE_SIG_KEY=.*$', 'CONFIG_MODULE_SIG_KEY="certs/signing_key.pem"', config, flags=re.M)
            config = re.sub(r'^CONFIG_UNUSED_KSYMS_WHITELIST=.*$', 'CONFIG_UNUSED_KSYMS_WHITELIST="abi_symbollist.raw"', config, flags=re.M)
            config_path = root / f"fleet/configs/{key}.config"
            abi_path = root / f"fleet/abi/{key}.symvers"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            abi_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(config)
            # Module-build caches may reuse a compatible newer symbol table.
            # A reviewed full-kernel reference takes precedence over that cache.
            reference = source.get("abi_reference")
            if reference:
                assert reference["release"] == release
                abi = (root / reference["path"]).read_bytes()
                assert hashlib.sha256(abi).hexdigest() == reference["sha256"]
            else:
                abi = (prepared / "Module.symvers").read_bytes()
            abi_path.write_bytes(abi)
            target.update(config=str(config_path.relative_to(root)),
                          config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                          symvers=str(abi_path.relative_to(root)), symvers_sha256=hashlib.sha256(abi).hexdigest())
        targets.append(target)
    destination = root / "fleet/targets.json"
    destination.write_text(json.dumps({"schema": 1, "targets": targets}, indent=2) + "\n")
    return targets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path)
    args = parser.parse_args()
    for target in export(args.index):
        print(target["id"], target["release"], "enabled" if target["enabled"] else "source-pending")
