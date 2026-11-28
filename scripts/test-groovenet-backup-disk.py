#!/usr/bin/env python3
"""Exercise the real role's read-only safety guards with fake block devices."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
UUID = '5b4a169a-a2b9-4f21-81a9-1e2e815d4c39'


class BackupDiskGuards(unittest.TestCase):
    def run_role(self, scenario, *, enabled=True, uuid=UUID):
        with tempfile.TemporaryDirectory(prefix='groovenet-disk-test-') as directory:
            temporary = Path(directory)
            binaries = temporary / 'bin'
            binaries.mkdir()
            app = temporary / 'app'
            app.mkdir()
            (app / '.env').write_text('# existing deployment\n')
            fake = f'''#!{sys.executable}
import os
import sys
scenario = os.environ['GROOVENET_DISK_TEST']
if os.path.basename(sys.argv[0]) == 'stat':
    print('1\\n1' if scenario == 'root-device' else '2\\n1')
elif '/srv/storage' in sys.argv:
    if scenario == 'unmounted':
        sys.exit(1)
    print('wrong-uuid ext4' if scenario == 'wrong-disk' else '{UUID} ext4')
elif scenario == 'unrelated-bind':
    print('{UUID} /immich')
elif scenario == 'existing-bind':
    print('{UUID} /groovenet-backup')
else:
    sys.exit(1)
'''
            for name in ('findmnt', 'stat'):
                (binaries / name).write_text(fake)
                (binaries / name).chmod(0o755)
            play = [{
                'name': 'Exercise backup disk guards',
                'hosts': 'localhost',
                'gather_facts': False,
                'environment': {
                    'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
                    'GROOVENET_DISK_TEST': scenario,
                },
                'vars': {
                    'ansible_become': False,
                    'ansible_python_interpreter': sys.executable,
                    'groovenet_backup_disk_enabled': enabled,
                    'groovenet_backup_disk_uuid': uuid,
                    'groovenet_backup_disk_restic_password_ref': 'op://Test/Local/password',
                    'groovenet_app_dir': str(app),
                },
                'roles': ['groovenet_backup_disk'],
            }]
            playbook = temporary / 'play.json'
            playbook.write_text(json.dumps(play))
            environment = dict(os.environ)
            environment.update({
                'ANSIBLE_LOCAL_TEMP': str(temporary / 'local'),
                'ANSIBLE_ROLES_PATH': str(ROOT / 'ansible/roles'),
                'ANSIBLE_NOCOLOR': '1',
            })
            result = subprocess.run(
                [shutil.which('ansible-playbook'), '-i', 'localhost,', '-c', 'local',
                 str(playbook), '--check', '--diff', '--tags', 'preflight'],
                cwd=ROOT, env=environment, text=True, capture_output=True, timeout=90,
            )
            return result.returncode, result.stdout + result.stderr

    def test_disabled_does_not_inspect_disks(self):
        code, output = self.run_role('unmounted', enabled=False)
        self.assertEqual(code, 0, output)
        self.assertIn('ok=0', output)
        self.assertIn('changed=0', output)

    def test_missing_uuid_is_rejected_before_disk_inspection(self):
        code, output = self.run_role('unmounted', uuid='')
        self.assertNotEqual(code, 0, output)
        self.assertNotIn('Inspect the existing storage mount', output)

    def test_unsafe_disks_fail_before_secret_resolution_or_writes(self):
        cases = {
            'unmounted': 'Inspect the existing storage mount',
            'wrong-disk': 'Refuse an absent or unexpected storage disk',
            'root-device': 'Require storage to be on a separate device',
            'unrelated-bind': 'Refuse to replace an unrelated mount',
        }
        for scenario, task in cases.items():
            with self.subTest(scenario=scenario):
                code, output = self.run_role(scenario)
                self.assertNotEqual(code, 0, output)
                self.assertIn(task, output)
                self.assertNotIn('Read the existing deployment environment ownership', output)
                self.assertNotIn('Resolve the local repository password', output)

    def test_expected_disk_with_new_or_existing_bind_passes(self):
        for scenario in ('new-bind', 'existing-bind'):
            with self.subTest(scenario=scenario):
                code, output = self.run_role(scenario)
                self.assertEqual(code, 0, output)
                self.assertIn('changed=0', output)
                self.assertNotIn('Resolve the local repository password', output)


if __name__ == '__main__':
    unittest.main()
