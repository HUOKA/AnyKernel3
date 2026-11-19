#!/usr/bin/env python3
"""Fetch the manager belonging to the exact release selected by the planner."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    release = json.loads(Path('fleet-plan.json').read_text())['release']
    folder=Path('fleet-manager'); folder.mkdir(exist_ok=True)
    apk=folder/release['manager_name']
    subprocess.run(['curl','--fail','--location','--retry','3','--max-time','300',
                    release['manager_url'],'--output',str(apk)],check=True)
    digest=hashlib.sha256(apk.read_bytes()).hexdigest()
    if release.get('manager_digest'):
        assert release['manager_digest']=='sha256:'+digest
    with zipfile.ZipFile(apk) as z:
        assert z.testzip() is None
        assert 'AndroidManifest.xml' in z.namelist()
    spec=importlib.util.spec_from_file_location('cert',ROOT/'scripts/finasu_compat/extract_manager_cert.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    certs=set(module.extract_certs(apk.read_bytes()))
    assert len(certs)==1,'Expected one signing certificate'
    cert=certs.pop();size=f'0x{len(cert):04x}';cert_hash=hashlib.sha256(cert).hexdigest()
    with open(os.environ['GITHUB_OUTPUT'],'a') as out:
        out.write(f'cert_size={size}\ncert_hash={cert_hash}\n')
    (folder/'manager-receipt.json').write_text(json.dumps({'release':release,'sha256':digest,
                                                        'certificate_size':size,'certificate_sha256':cert_hash},indent=2)+'\n')
    print(release['manager_name'],digest)


if __name__=='__main__':
    main()
