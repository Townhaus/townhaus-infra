#!/usr/bin/env python3
"""Exercise the real Ansible player-preservation tasks with a fake Docker CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GroovenetDeployTests(unittest.TestCase):
    def run_deploy(self, credentials=False, overlay=False, services=(), directory=False):
        with tempfile.TemporaryDirectory(prefix="groovenet-deploy-test-") as temporary:
            fixture = Path(temporary)
            app = fixture / "stack"
            app.mkdir()
            if credentials:
                if directory:
                    (app / ".env.players").mkdir()
                else:
                    (app / ".env.players").write_text("host-managed-secret\n")
            if overlay:
                (app / "docker-compose.players.yml").write_text("host-managed-overlay\n")
            binary = fixture / "bin"
            binary.mkdir()
            log = fixture / "docker-calls.jsonl"
            docker = binary / "docker"
            docker.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                f"with open({str(log)!r}, 'a') as stream: stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if sys.argv[1:2] == ['ps']:\n"
                '    assert sys.argv[sys.argv.index("--format") + 1] == ' + repr('{{.Label "com.docker.compose.service"}}') + "\n"
                f"    print('\\n'.join({list(services)!r}))\n"
                "elif sys.argv[-2:] != ['config', '--quiet']:\n"
                "    raise SystemExit('Unexpected Docker mutation in isolated test')\n"
            )
            docker.chmod(0o755)
            playbook = ROOT / "ansible/deploy-groovenet.yml"
            task_file = ROOT / "ansible/tasks/groovenet-player-config.yml"
            expected = bool(credentials and overlay and not directory)
            play = [{
                "name": "Test deployment preservation locally",
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "environment": {"PATH": str(binary) + os.pathsep + os.environ["PATH"]},
                "vars": {
                    "ansible_python_interpreter": sys.executable,
                    "groovenet_app_dir": str(app),
                    "groovenet_project_name": "fixture-project",
                    "source_deploy": "{{ (lookup('file', " + json.dumps(str(playbook)) + ") | from_yaml)[0] }}",
                    "groovenet_compose_default": "{{ source_deploy.vars.groovenet_compose_default }}",
                },
                "tasks": [
                    {"name": "Use actual player-preservation tasks", "ansible.builtin.include_tasks": str(task_file)},
                    {"name": "Verify selected deployment model", "ansible.builtin.assert": {"that": [
                        "groovenet_players_enabled == " + str(expected).lower(),
                        "('.env.players' in groovenet_compose_base) == " + str(expected).lower(),
                        "('docker-compose.players.yml' in groovenet_compose_base) == " + str(expected).lower(),
                        "('--profile' in groovenet_compose_base) == " + str(expected).lower(),
                        "'--no-build' in ((source_deploy.tasks | selectattr('name', 'equalto', 'Start the full stack') | first)['ansible.builtin.command']['argv'])",
                    ]}},
                    {"name": "Validate safe Compose invocation", "ansible.builtin.command": {
                        "argv": "{{ groovenet_compose_base + ['config', '--quiet'] }}",
                    }, "changed_when": False},
                ],
            }]
            config = fixture / "ansible.cfg"
            config.write_text(f"[defaults]\nlocal_tmp={fixture}/local\nremote_tmp={fixture}/remote\nretry_files_enabled=False\n")
            test_play = fixture / "play.json"
            test_play.write_text(json.dumps(play))
            result = subprocess.run(
                ["ansible-playbook", "-i", "localhost,", str(test_play)],
                env={**os.environ, "ANSIBLE_CONFIG": str(config), "ANSIBLE_NOCOLOR": "1"},
                capture_output=True, text=True, check=False, timeout=60,
            )
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            if credentials and not directory:
                self.assertEqual((app / ".env.players").read_text(), "host-managed-secret\n")
            if overlay:
                self.assertEqual((app / "docker-compose.players.yml").read_text(), "host-managed-overlay\n")
            self.assertNotIn("host-managed-secret", result.stdout + result.stderr)
            return result, calls

    def test_preserves_players_and_their_host_managed_files(self):
        result, calls = self.run_deploy(True, True, ["app", "player-clock", "player-worker", "player-engine-a"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(".env.players", calls[-1])
        self.assertIn("docker-compose.players.yml", calls[-1])
        self.assertIn("players", calls[-1])

    def test_hosts_without_players_remain_opted_out(self):
        result, calls = self.run_deploy(services=["app", "db"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(".env.players", calls[-1])
        self.assertNotIn("--profile", calls[-1])

    def test_incomplete_settings_fail_before_compose_or_restart(self):
        for credentials, overlay, services, directory in [
            (True, False, [], False),
            (False, True, [], False),
            (False, False, ["player-worker"], False),
            (False, False, ["player-clock"], False),
            (False, False, ["player-engine-c"], False),
            (True, True, [], True),
        ]:
            with self.subTest(credentials=credentials, overlay=overlay, services=services, directory=directory):
                result, calls = self.run_deploy(credentials, overlay, services, directory)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Server-player deployment is incomplete", result.stdout)
                self.assertEqual(len(calls), 1, result.stdout + result.stderr)
                self.assertEqual(calls[0][0], "ps")


if __name__ == "__main__":
    unittest.main(verbosity=2)
