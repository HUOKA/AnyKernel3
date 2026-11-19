import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/fleet'))
import publisher


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = {'tag': 'v1.6.4', 'commit': 'a' * 40,
                        'manager_name': 'FinaSU_v1.6.4_33211-release.apk'}
        self.policy = {'options': {'susfs_version': 'v2.4.0'}}
        self.catalog = [self.target('a', '138'), self.target('b', '141')]
        self.state = {'targets': {}}
        with zipfile.ZipFile(self.root / self.release['manager_name'], 'w') as z:
            z.writestr('AndroidManifest.xml', 'fixture')
        self.release['manager_digest'] = 'sha256:' + hashlib.sha256(
            (self.root / self.release['manager_name']).read_bytes()).hexdigest()

    def target(self, name, sublevel):
        return {'id': name, 'enabled': True, 'android': 'android14', 'series': '6.1',
                'sublevel': sublevel, 'release': f'6.1.{sublevel}-android14-11-g{name * 12}-ab123'}

    def save_package(self, target, folder):
        folder.mkdir(exist_ok=True)
        image = b'Linux version ' + target['release'].encode() + b' kernel'
        with zipfile.ZipFile(folder / f"FinaSU-33211-{target['id']}-AnyKernel3.zip", 'w') as z:
            z.writestr('Image', image)
        for suffix in ['-SHA256SUMS.txt', '-kernel.config', '-validation.json']:
            (folder / (target['id'] + suffix)).write_text('artifact evidence')
        self.state['targets'][target['id']] = {'status': 'success', 'key': target['id'],
            'result': {'status': 'success', 'release': target['release'],
                       'finasu_commit': self.release['commit'],
                       'image_sha256': hashlib.sha256(image).hexdigest()}}

    def test_upstream_names_disambiguate_same_version(self):
        a, b = self.target('a', '118'), self.target('b', '118')
        names = publisher.asset_names([a, b], self.release)
        self.assertNotEqual(names['a'], names['b'])
        self.assertIn('android14-6.1-118-11-gaaaaaaaaaaaa-ab123-', names['a'])
        single = publisher.asset_names([a], self.release)
        self.assertEqual(single['a'], 'FinaSU-33211-android14-6.1-118-AnyKernel3.zip')

    def test_tag_increment_is_numeric_and_ignores_legacy_hash_tags(self):
        self.assertEqual(publisher.next_tag(['finasu-v1.6.4-abcd', 'v2.4.0-r9', 'v2.4.0-r10'],
                                           'v2.4.0'), 'v2.4.0-r11')

    def test_public_assets_exclude_evidence_and_require_apk(self):
        self.save_package(self.catalog[0], self.root / 'packages')
        names = publisher.asset_names(self.catalog, self.release)
        files = publisher.prepare_assets([self.catalog[0]], names, self.release, self.state,
                                         self.root / 'packages', self.root, self.root / 'publish')
        self.assertEqual(set(files), {names['a'], self.release['manager_name']})
        with self.assertRaisesRegex(ValueError, 'APK is required'):
            publisher.prepare_assets([self.catalog[0]], names, self.release, self.state,
                self.root / 'packages', self.root / 'absent', self.root / 'missing-apk')

    def test_retry_updates_one_normal_release_and_keeps_prior_downloads(self):
        remote = []
        uploads = []

        def api(*args, payload=None):
            endpoint = next(a for a in args if a.startswith('repos/'))
            method = args[args.index('--method') + 1] if '--method' in args else 'GET'
            if 'releases?per_page' in endpoint:
                return [copy.deepcopy(remote)]
            if 'tags?per_page' in endpoint:
                return [[]]
            if endpoint.endswith('/releases') and method == 'POST':
                remote.append({**payload, 'id': 1, 'assets': [], 'html_url': 'https://github.com/test/repo/releases/tag/v2.4.0-r1'})
                return copy.deepcopy(remote[0])
            if endpoint.endswith('/releases/1'):
                if method == 'PATCH':
                    remote[0].update(payload)
                return copy.deepcopy(remote[0])
            raise AssertionError((args, payload))

        def upload(command, **kwargs):
            file = Path(command[-1])
            uploads.append(file.name)
            remote[0]['assets'].append({'id': len(uploads), 'name': file.name,
                'digest': 'sha256:' + hashlib.sha256(file.read_bytes()).hexdigest()})

        key = lambda target, policy, release: target['id']
        first, retry = self.root / 'first', self.root / 'retry'
        with patch.object(publisher.subprocess, 'run', side_effect=upload):
            self.save_package(self.catalog[0], first)
            one = publisher.publish('test/repo', 'c' * 40, self.catalog, self.policy, self.release,
                self.state, first, self.root, api, key)
            self.save_package(self.catalog[1], retry)
            two = publisher.publish('test/repo', 'c' * 40, self.catalog, self.policy, self.release,
                self.state, retry, self.root, api, key)
            self.assertEqual(one['tag'], two['tag'])
            self.assertEqual(two['targets'], 2)
            self.assertEqual(len(remote), 1)
            self.assertFalse(remote[0]['draft'])
            self.assertFalse(remote[0]['prerelease'])
            self.assertEqual(remote[0]['make_latest'], 'true')
            self.assertEqual(len(remote[0]['assets']), 3)
            for target in self.catalog:
                self.assertIn(target['release'], remote[0]['body'])
            count = len(uploads)
            publisher.publish('test/repo', 'c' * 40, self.catalog, self.policy, self.release,
                self.state, retry, self.root, api, key)
            self.assertEqual(len(uploads), count)


if __name__ == '__main__':
    unittest.main()
