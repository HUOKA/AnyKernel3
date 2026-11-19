import copy
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts/fleet'))


def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts/fleet'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


controller=load('controller');verify=load('verify')
exporter=load('export_wsl')


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.target={'id':'a','enabled':True,'release':'6.1.138-test','source':{'commit':'a'*40},'config_sha256':'config','symvers_sha256':'abi','page_size':4096}
        self.policy={'max_attempts':3,'retry_hours':6}
        self.release={'commit':'b'*40,'tag':'v1'}
        self.now=datetime(2026,11,19,tzinfo=timezone.utc)

    def plan(self,state=None,**kwargs):
        return controller.select_targets([self.target],self.policy,self.release,state or {},now=self.now,**kwargs)[0]

    def state(self,status,attempts=1,hours=7):
        return {'targets':{'a':{'key':controller.target_key(self.target,self.policy,self.release),'status':status,
                                'attempts':attempts,'updated_at':(self.now-timedelta(hours=hours)).isoformat()}}}

    def test_new_release_and_recipe_trigger(self):
        state=self.state('success')
        self.assertEqual(self.plan(state),[])
        self.release['commit']='c'*40
        self.assertEqual(len(self.plan(state)),1)

    def test_retry_backoff_and_exhaustion(self):
        self.assertEqual(self.plan(self.state('failure',hours=1)),[])
        self.assertEqual(len(self.plan(self.state('failure',hours=7))),1)
        self.assertEqual(self.plan(self.state('failure',attempts=3)),[])
        self.assertEqual(len(self.plan(self.state('failure',attempts=3),force=True)),1)

    def test_missing_source_never_built_even_forced(self):
        self.target['enabled']=False
        self.assertEqual(self.plan(force=True),[])

    def test_unknown_selection_fails(self):
        with self.assertRaises(ValueError):self.plan(only='missing')

    def test_config_or_manager_change_invalidates_success(self):
        state=self.state('success')
        self.target['config_sha256']='changed'
        self.assertEqual(len(self.plan(state)),1)


class ManifestTests(unittest.TestCase):
    def test_targets_hashes_and_no_private_paths(self):
        import hashlib
        targets=json.loads((ROOT/'fleet/targets.json').read_text())['targets']
        self.assertEqual(len({t['id'] for t in targets}),len(targets))
        for t in targets:
            if not t['enabled']:continue
            for path,key in [('config','config_sha256'),('symvers','symvers_sha256')]:
                self.assertEqual(hashlib.sha256((ROOT/t[path]).read_bytes()).hexdigest(),t[key])
            self.assertNotIn('/home/',(ROOT/t['config']).read_text())
            self.assertRegex(t['source']['commit'],r'^[a-f0-9]{40}$')
            for patch in t['source'].get('patches',[]):
                self.assertEqual(hashlib.sha256((ROOT/patch['path']).read_bytes()).hexdigest(),patch['sha256'])
            reference=t['source'].get('abi_reference')
            if reference:
                self.assertEqual(reference['release'],t['release'])
                self.assertEqual(reference['sha256'],t['symvers_sha256'])
                self.assertEqual(reference['symbols'],len(verify.symvers(ROOT/t['symvers'])))

    def test_inventory_export_preserves_reviewed_stock_baseline(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'fleet/abi').mkdir(parents=True)
            (root/'data/android14').mkdir(parents=True)
            (root/'prepared').mkdir()
            release='6.1.124-android14-11-gd53339245194-ab13005711'
            stock=b'0x12345678\tstock_export\tvmlinux\tEXPORT_SYMBOL\n'
            (root/'fleet/abi/target.symvers').write_bytes(stock)
            (root/'prepared/.config').write_text('CONFIG_ARM64=y\n')
            (root/'prepared/Module.symvers').write_text('0x99999999 newer_export vmlinux EXPORT_SYMBOL\n')
            (root/'data/android14/6.1.json').write_text(json.dumps({'entries':[{'kernel':'6.1.124','date':'2025-02'}]}))
            reference={'release':release,'path':'fleet/abi/target.symvers','sha256':hashlib.sha256(stock).hexdigest()}
            (root/'fleet/source-overrides.json').write_text(json.dumps({'target':{'abi_reference':reference}}))
            (root/'index.json').write_text(json.dumps({'entries':[{'id':'target','kernel_release':release,
                'page_size':4096,'kernel_out':str(root/'prepared')}]}))
            target=exporter.export(root/'index.json',root)[0]
            self.assertEqual((root/target['symvers']).read_bytes(),stock)
            self.assertEqual(target['symvers_sha256'],reference['sha256'])

    def test_inline_required_independently_from_susfs(self):
        config='CONFIG_KSU=y\nCONFIG_KSU_SUSFS=y\nCONFIG_KSU_HOOK_TRACEPOINT=y\n'
        missing,unexpected=verify.verify_config(config)
        self.assertIn('KSU_HOOK_SUSFS_INLINE',missing)
        self.assertIn('KSU_HOOK_TRACEPOINT',unexpected)


if __name__=='__main__':unittest.main()
