#!/usr/bin/env python3
"""Validate full-kernel artifacts against the selected recipe, not driver modules."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def symvers(path):
    return {fields[1]:fields[0] for line in Path(path).read_text().splitlines() if len(fields:=line.split())>=2}


def verify_config(config):
    required = ['ARM64_4K_PAGES','KSU','KSU_SUSFS','KSU_HOOK_SUSFS_INLINE','LTO_CLANG_THIN',
                'ZRAM','CRYPTO_LZ4KD','BBG','TCP_CONG_BBR3','IP_SET','NET_SCH_FQ','CIFS']
    missing=[key for key in required if not re.search(rf'^CONFIG_{key}=y$',config,re.M)]
    unexpected=[key for key in ['KSU_HOOK_TRACEPOINT','KPM','REKERNEL','NOMOUNT']
                if re.search(rf'^CONFIG_{key}=[ym]$',config,re.M)]
    return missing,unexpected


def main():
    target=json.loads((ROOT/'fleet-target.json').read_text())
    evidence=ROOT/'fleet-evidence'
    evidence.mkdir(exist_ok=True)
    config=(evidence/'kernel.config').read_text()
    missing,unexpected=verify_config(config)
    release=(evidence/'kernel.release').read_text().strip()
    expected=symvers(ROOT/target['symvers']); actual=symvers(evidence/'Module.symvers')
    # A previous root implementation's private exports are not device kernel ABI.
    ignore=lambda s: s.startswith(('ksu_','susfs_','ksud_','bbg_'))
    changed=[s for s in expected.keys() & actual.keys() if expected[s]!=actual[s] and not ignore(s)]
    absent=[s for s in expected.keys()-actual.keys() if not ignore(s)]
    kernel=Path(os.environ['KERNEL_ROOT'])
    image=(kernel/'fleet-dist/Image').read_bytes()
    commit=lambda path:subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
    source=commit(kernel/'common')
    ksu=commit(kernel/'KernelSU')
    susfs=commit(Path(os.environ['SUSFS4KSU']))
    policy=json.loads((ROOT/'fleet/policy.json').read_text())
    problems=[]
    if missing or unexpected:problems.append('Configuration mismatch')
    if release!=target['release'] or target['release'].encode() not in image:problems.append('Kernel release mismatch')
    if source!=target['source']['commit']:problems.append('Kernel source mismatch')
    if ksu!=os.environ['FLEET_KSU_COMMIT']:problems.append('FinaSU commit mismatch')
    if susfs!=policy['susfs'][target['android']+'-'+target['series']]:problems.append('SUSFS commit mismatch')
    if changed or absent:problems.append('Target ABI mismatch')
    result={'target':target['id'],'status':'failure' if problems else 'success','problems':problems,
            'release':release,'kernel_source':source,'finasu_commit':ksu,'susfs_commit':susfs,
            'missing_config':missing,'unexpected_config':unexpected,
            'abi':{'reference_symbols':len(expected),'candidate_symbols':len(actual),
                   'changed':sorted(changed),'missing':sorted(absent)},
            'image_sha256':hashlib.sha256(image).hexdigest(),'image_bytes':len(image),
            'runtime_acceptance':'not performed'}
    (evidence/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='abi'},indent=2))
    print(f'ABI: {len(expected)} reference exports, {len(changed)} changed, {len(absent)} missing')
    return 1 if problems else 0


if __name__=='__main__':
    sys.exit(main())
