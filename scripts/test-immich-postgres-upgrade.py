#!/usr/bin/env python3
"""Exercise cutover and recovery with fake Docker, never a live database."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'ghcr.io/immich-app/postgres:16-vectorchord0.4.3-pgvectors0.2.0@sha256:test'
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args=sys.argv[1:]
name=pathlib.Path(sys.argv[0]).name
root=pathlib.Path(os.environ['TEST_ROOT'])
mode=os.environ['TEST_MODE']
with (root/'calls').open('a') as f: f.write(json.dumps([name]+args)+'\n')
if name == 'flock' or name == 'sleep': sys.exit(0)
if name == 'systemctl': sys.exit(0 if mode=='backup-active' else 3)
if name == 'du': print('100\tdata'); sys.exit(0)
if name == 'df': print('Avail\n1000000000000'); sys.exit(0)
if args[0] == 'inspect':
    if args[1] != '-f':
        print(json.dumps([{'Mounts':[{'Source':str(root/'data'),'Destination':'/var/lib/postgresql/data','Type':'bind'}], 'Config':{'Env':['POSTGRES_USER=postgres','POSTGRES_DB=immich','POSTGRES_PASSWORD=fake'], 'Labels':{'com.docker.compose.project':'townhaus-caddy'}}}]))
    elif 'Health.Status' in args[2]: print('unhealthy' if mode=='unhealthy' else 'healthy')
    elif 'Config.Image' in args[2]: print('ghcr.io/immich-app/postgres:14-old')
    else: print('true')
elif args[0] == 'run':
    mount=args[args.index('--mount')+1]
    path=pathlib.Path(mount.split('src=')[1].split(',')[0])
    (path/'PG_VERSION').write_text('16\n')
elif args[0] == 'exec':
    if 'pg_dump' in args:
        if mode=='dump-fail': sys.exit(1)
        print("SELECT pg_catalog.set_config('search_path', '', false);")
    elif 'psql' in args:
        if 'SHOW server_version_num;' in args: print('160000')
        elif '-Atc' in args: print('0')
        elif '-c' not in args:
            sql=sys.stdin.read()
            if '\\gexec' in sql:
                print('asset|3' if mode=='counts-fail' and args[2].startswith('immich-pg16-') else 'asset|2')
                print('user|1')
            else:
                assert "'public, pg_catalog', true" in sql
                if mode=='restore-fail': sys.exit(1)
elif args[0] == 'compose':
    if 'config' in args:
        print(json.dumps({'services':{'immich-database':{'volumes':[{'source':str(root/'data'),'target':'/var/lib/postgresql/data','type':'bind'}]}}}))
    if mode=='cutover-fail' and (root/'data'/'PG_VERSION').read_text().strip()=='16': sys.exit(1)
'''


class MigrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ansible-playbook'), 'Ansible is not installed')
    def test_stack_deployment_guard_rejects_14_and_accepts_16(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = (ROOT / 'ansible/deploy.yml').read_text()
            tasks = source.split('  pre_tasks:\n', 1)[1].split('    - name: Require an Immich DB password reference', 1)[0]
            tasks = tasks.replace('become: true', 'become: false')
            play = ('- hosts: localhost\n  connection: local\n  gather_facts: false\n'
                    '  vars:\n    townhaus_caddy_env:\n'
                    f'      IMMICH_DB_DATA_LOCATION: {json.dumps(str(root))}\n  tasks:\n' + tasks)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', prefix='.immich-guard-', dir=ROOT / 'ansible') as fixture:
                fixture.write(play)
                fixture.flush()
                env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(root / 'local'), ANSIBLE_REMOTE_TEMP=str(root / 'remote'))
                for major in ('14', '16'):
                    (root / 'PG_VERSION').write_text(major + '\n')
                    result = subprocess.run(['ansible-playbook', '-i', 'localhost,', fixture.name], env=env, capture_output=True, text=True, timeout=30)
                    if major == '14':
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('PostgreSQL major versions differ', result.stdout + result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def run_migration(self, mode):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / 'data').mkdir()
        (root / 'data/PG_VERSION').write_text('14\n')
        (root / 'docker-compose.yml').write_text('services:\n  immich-database:\n    image: ghcr.io/immich-app/postgres:14-old\n    container_name: immich_postgres\n')
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        for name in ('docker', 'systemctl', 'flock', 'du', 'df', 'sleep'):
            tool = bin_dir / name
            tool.write_text(FAKE)
            tool.chmod(0o755)
        env = dict(os.environ, TEST_ROOT=str(root), TEST_MODE=mode, PATH=f'{bin_dir}:{os.environ["PATH"]}')
        result = subprocess.run(['bash', str(ROOT / 'ansible/files/upgrade-immich-postgres.sh'), str(root), IMAGE, str(root / 'state')], env=env, text=True, capture_output=True, timeout=30)
        return root, result

    def test_success_preserves_original_cluster(self):
        root, result = self.run_migration('success')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / 'data/PG_VERSION').read_text().strip(), '16')
        self.assertEqual(next(root.glob('data.pg14-backup-*/PG_VERSION')).read_text().strip(), '14')
        self.assertTrue(list(root.glob('state/*/COMPLETE')))
        self.assertIn('container_name: immich_postgres\n', (root / 'docker-compose.yml').read_text())
        self.assertFalse(list(root.glob('state/*/database.env')))

    def test_failed_dump_restore_and_counts_keep_original(self):
        for mode in ('dump-fail', 'restore-fail', 'counts-fail'):
            with self.subTest(mode=mode):
                root, result = self.run_migration(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((root / 'data/PG_VERSION').read_text().strip(), '14')
                self.assertFalse(list(root.glob('data.pg14-backup-*')))
                self.assertIn(':14-old', (root / 'docker-compose.yml').read_text())
                self.assertFalse(list(root.glob('state/*/database.env')))

    def test_failed_cutover_restores_original_directory_and_compose(self):
        root, result = self.run_migration('cutover-fail')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((root / 'data/PG_VERSION').read_text().strip(), '14')
        self.assertIn(':14-old', (root / 'docker-compose.yml').read_text())
        self.assertTrue(list(root.glob('data.pg16-failed-*/PG_VERSION')))

    def test_health_failure_does_not_discard_new_writes(self):
        root, result = self.run_migration('unhealthy')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((root / 'data/PG_VERSION').read_text().strip(), '16')
        self.assertTrue(list(root.glob('data.pg14-backup-*/PG_VERSION')))
        self.assertIn('automatic rollback was skipped', result.stderr)
        self.assertFalse(list(root.glob('state/*/COMPLETE')))

    def test_running_backup_aborts_before_application_shutdown(self):
        root, result = self.run_migration('backup-active')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((root / 'data/PG_VERSION').read_text().strip(), '14')
        calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
        self.assertFalse(any(call[:2] == ['docker', 'stop'] for call in calls))


if __name__ == '__main__':
    unittest.main()
