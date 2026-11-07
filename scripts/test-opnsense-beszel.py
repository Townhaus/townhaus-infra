#!/usr/bin/env python3
"""Run the real role offline in a temporary filesystem with a fake rc service.

Only platform paths, ownership, download URL/checksum, account lookups, and service
execution are adapted. No SSH, op account, network, root, FreeBSD, or Docker needed.
This tests Ansible orchestration and rendered shell behavior, not FreeBSD's rc/daemon.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/opnsense_beszel_agent"
TOKEN = "offline-only-token-'$(touch SHOULD_NOT_EXIST)'"
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA fixture"

MOCK_SERVICE = '''#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule
import json
from pathlib import Path
m = AnsibleModule(argument_spec=dict(name=dict(required=True), state=dict(required=True)), supports_check_mode=True)
state = Path(STATE_FILE)
running = state.exists()
desired = m.params['state'] != 'stopped'
changed = running != desired or m.params['state'] == 'restarted'
if changed and not m.check_mode:
    with open(CALL_FILE, 'a') as stream:
        stream.write(json.dumps(m.params) + '\\n')
    if desired:
        state.write_text('running')
    elif running:
        state.unlink()
m.exit_json(changed=changed)
'''


class OPNsenseBeszelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="opnsense-beszel-test-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Path(self.temporary.name)
        self.fs = self.fixture / "appliance"
        self.fs.mkdir()
        self.role = self.fixture / "roles/opnsense_beszel_agent"
        shutil.copytree(ROLE, self.role)
        self.mapping = {
            path: str(self.fs / path.lstrip('/'))
            for path in ["/usr/local", "/var/cache", "/var/db", "/var/run", "/var/log", "/etc/rc.conf.d", "/etc/rc.subr", "/etc/opnsense-release"]
        }
        (self.fs / "var/log").mkdir(parents=True)
        self.ca = self.fixture / "ca.crt"
        # A throwaway key never leaves this 0700 test directory or enters Git/logs.
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(self.fixture / "ca.key"), "-out", str(self.ca),
            "-subj", "/CN=Offline Fixture", "-days", "1",
        ], capture_output=True, check=True)
        self.archive = self.fixture / "agent.tar.gz"
        self.checksum = self.make_archive(b"offline-agent-v0.21.0\n")
        for file in self.role.rglob("*"):
            if not file.is_file():
                continue
            source = file.read_text()
            for original, replacement in self.mapping.items():
                source = source.replace(original, replacement)
            if file.suffix == ".yml" and file.parent.name in {"tasks", "handlers"}:
                tasks = yaml.safe_load(source)
                for task in tasks:
                    # Test file permissions, but never chown controller files.
                    for module in ["ansible.builtin.file", "ansible.builtin.copy", "ansible.builtin.template", "ansible.builtin.get_url"]:
                        if module in task:
                            task[module].pop("owner", None)
                            task[module].pop("group", None)
                    if "ansible.builtin.get_url" in task:
                        task["ansible.builtin.get_url"]["url"] = self.archive.as_uri()
                    if "ansible.builtin.getent" in task:
                        task.pop("ansible.builtin.getent")
                        task["ansible.builtin.assert"] = {"that": [True]}
                    if "ansible.builtin.service" in task:
                        args = task.pop("ansible.builtin.service")
                        args.pop("use")
                        task["townhaus_test_service"] = args
                source = yaml.safe_dump(tasks, sort_keys=False)
            file.write_text(source)
        self.marker = self.fs / "usr/local/sbin/opnsense-version"
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text("OPNsense fixture\n")
        self.bin = self.fixture / "bin"
        self.bin.mkdir()
        self.op_calls = self.fixture / "op-calls"
        op = self.bin / "op"
        op.write_text(
            f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
            "assert sys.argv[1:] == ['read', 'op://Homelab/OPNsense Beszel/token']\n"
            f"with open({str(self.op_calls)!r}, 'a') as f: f.write('read\\n')\n"
            f"print({TOKEN!r})\n"
        )
        op.chmod(0o755)
        library = self.fixture / "library"
        library.mkdir()
        self.calls = self.fixture / "service-calls"
        self.state = self.fixture / "running"
        (library / "townhaus_test_service.py").write_text(
            MOCK_SERVICE.replace("STATE_FILE", repr(str(self.state))).replace("CALL_FILE", repr(str(self.calls)))
        )
        self.config = self.fixture / "ansible.cfg"
        self.config.write_text(
            f"[defaults]\nroles_path={self.fixture}/roles\nlibrary={library}\n"
            f"local_tmp={self.fixture}/local\nremote_tmp={self.fixture}/remote\n"
            "retry_files_enabled=False\n"
        )
        self.vars = {
            "ansible_python_interpreter": sys.executable,
            "ansible_shell_executable": "/bin/sh",
            "ansible_facts": {"system": "FreeBSD", "architecture": "amd64", "kernel": "14.3-RELEASE"},
            "opnsense_beszel_agent_hub_key": PUBLIC_KEY,
            "opnsense_beszel_agent_token_ref": "op://Homelab/OPNsense Beszel/token",
            "opnsense_beszel_agent_ca_file": str(self.ca),
            "opnsense_beszel_agent_release_sha256": {"0.21.0": self.checksum},
        }

    def make_archive(self, binary):
        with tarfile.open(self.archive, "w:gz") as archive:
            entry = tarfile.TarInfo("beszel-agent")
            entry.mode = 0o755
            entry.size = len(binary)
            archive.addfile(entry, io.BytesIO(binary))
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def run_role(self, variables=None, tasks=None, check=False):
        play = {
            "name": "Offline OPNsense role fixture",
            "hosts": "localhost", "connection": "local", "gather_facts": False,
            "environment": {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"]},
            "vars": {**self.vars, **(variables or {})},
        }
        if tasks is None:
            play["roles"] = ["opnsense_beszel_agent"]
        else:
            play["tasks"] = tasks
        path = self.fixture / "play.json"
        path.write_text(json.dumps([play]))
        result = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", str(path), "--diff"] + (["--check"] if check else []),
            env={**os.environ, "ANSIBLE_CONFIG": str(self.config), "ANSIBLE_NOCOLOR": "1"},
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        return result

    def success(self, result, unchanged=False):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if unchanged:
            self.assertRegex(result.stdout, r"changed=0\s")

    def service_calls(self):
        return [json.loads(line)["state"] for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_inventory_transport_overrides_shared_linux_defaults(self):
        # Exercise actual inventory precedence, which the role-only fixture
        # cannot test because it supplies transport vars directly in the play.
        inventory = self.fixture / "inventory.ini"
        inventory.write_text((ROOT / "ansible/inventory.opnsense.ini.example").read_text())
        group_vars = self.fixture / "group_vars"
        shutil.copytree(ROOT / "ansible/group_vars/all", group_vars / "all")
        shutil.copy(ROOT / "ansible/group_vars/opnsense.yml", group_vars / "opnsense.yml")
        result = subprocess.run(
            ["ansible-inventory", "-i", str(inventory), "--host", "opnsense_firewall"],
            env={**os.environ, "ANSIBLE_CONFIG": str(self.config)},
            capture_output=True, text=True, check=True, timeout=30,
        )
        values = json.loads(result.stdout)
        self.assertEqual(values["ansible_shell_executable"], "/bin/sh")
        self.assertEqual(values["ansible_shell_type"], "sh")
        self.assertEqual(values["ansible_python_interpreter"], "/usr/local/bin/python3")
        self.assertEqual(values["ansible_remote_tmp"], "/tmp/.ansible-{{ ansible_user }}/tmp")

    def test_install_repeat_update_and_configuration_restart(self):
        self.success(self.run_role())
        binary = self.fs / "usr/local/sbin/beszel-agent"
        self.assertEqual(binary.read_text(), "offline-agent-v0.21.0\n")
        self.assertEqual(self.service_calls(), ["restarted"])
        env = self.fs / "usr/local/etc/beszel-agent/env"
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
        self.success(self.run_role(), unchanged=True)
        self.assertEqual(self.service_calls(), ["restarted"])
        self.success(self.run_role(check=True))
        self.assertEqual(self.service_calls(), ["restarted"])
        self.success(self.run_role({"opnsense_beszel_agent_nics": "re0"}))
        self.assertEqual(self.service_calls(), ["restarted", "restarted"])
        new_hash = self.make_archive(b"offline-agent-next-version\n")
        update = {"opnsense_beszel_agent_version": "0.21.1", "opnsense_beszel_agent_release_sha256": {"0.21.1": new_hash}}
        self.success(self.run_role(update))
        self.assertEqual(binary.read_text(), "offline-agent-next-version\n")
        self.success(self.run_role(update), unchanged=True)
        self.assertEqual(self.service_calls(), ["restarted"] * 3)

    def test_disable_repeat_and_reenable_without_fetching_token(self):
        self.success(self.run_role())
        before = self.op_calls.read_text()
        disabled = {"opnsense_beszel_agent_enabled": False, "opnsense_beszel_agent_token_ref": "", "opnsense_beszel_agent_ca_file": ""}
        self.success(self.run_role(disabled))
        self.assertFalse(self.state.exists())
        self.assertFalse((self.fs / "usr/local/etc/beszel-agent/env").exists())
        self.assertFalse((self.fs / "usr/local/etc/rc.syshook.d/start/99-beszel-agent").exists())
        self.assertIn('beszel_agent_enable="NO"', (self.fs / "etc/rc.conf.d/beszel_agent").read_text())
        self.success(self.run_role(disabled), unchanged=True)
        self.assertEqual(self.op_calls.read_text(), before)
        self.assertEqual(self.service_calls(), ["restarted", "stopped"])
        self.success(self.run_role())
        self.assertTrue(self.state.exists())

    def test_disabling_never_installed_agent_is_unchanged(self):
        self.success(self.run_role({"opnsense_beszel_agent_enabled": False}), unchanged=True)
        self.assertFalse(self.op_calls.exists())
        self.assertFalse(self.calls.exists())

    def test_platform_guards_fail_before_installation(self):
        for system, arch, kernel in [("Linux", "x86_64", "6.1"), ("FreeBSD", "arm64", "14.3"), ("FreeBSD", "amd64", "13.5")]:
            with self.subTest(system=system, arch=arch, kernel=kernel):
                result = self.run_role({"ansible_facts": {"system": system, "architecture": arch, "kernel": kernel}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("requires OPNsense", result.stdout)
                self.assertFalse(self.op_calls.exists())
        self.marker.unlink()
        result = self.run_role()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No OPNsense identification", result.stdout)
        self.assertFalse(self.calls.exists())
        # Upstream's test -f follows links; packaged identification paths may be links.
        marker_target = self.fixture / "opnsense-identification"
        marker_target.write_text("OPNsense fixture\n")
        self.marker.symlink_to(marker_target)
        self.success(self.run_role({"opnsense_beszel_agent_enabled": False}), unchanged=True)

    def test_bad_checksum_never_installs_or_starts_binary(self):
        result = self.run_role({"opnsense_beszel_agent_release_sha256": {"0.21.0": "0" * 64}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stdout)
        self.assertFalse((self.fs / "usr/local/sbin/beszel-agent").exists())
        self.assertFalse(self.calls.exists())

    def test_unreviewed_version_and_insecure_url_fail_before_token(self):
        for variables in [{"opnsense_beszel_agent_version": "99.0.0"}, {"opnsense_beszel_agent_hub_url": "http://192.168.2.151:8090"}]:
            result = self.run_role(variables)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Supply a reviewed version", result.stdout)
            self.assertFalse(self.op_calls.exists())

    def test_linux_role_guard_preserves_supported_platforms(self):
        guard = yaml.safe_load((ROOT / "ansible/roles/beszel_agent/tasks/main.yml").read_text())[0]
        for system, arch, allowed in [("Linux", "x86_64", True), ("Linux", "aarch64", True), ("FreeBSD", "amd64", False), ("Linux", "mips", False)]:
            result = self.run_role({"ansible_facts": {"system": system, "architecture": arch}}, tasks=[guard])
            self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)

    def test_rendered_env_rc_and_boot_behavior(self):
        self.success(self.run_role())
        env = self.fs / "usr/local/etc/beszel-agent/env"
        for file in [env, self.fs / "usr/local/etc/rc.d/beszel-agent", self.fs / "usr/local/etc/rc.syshook.d/start/99-beszel-agent"]:
            subprocess.run(["/bin/sh", "-n", str(file)], check=True)
        # Shell evaluation must preserve a token containing quotes and substitutions.
        command = 'set -a; . "$1"; exec "$2" -c "import os,json; print(json.dumps(dict(os.environ)))"'
        result = subprocess.run(["/bin/sh", "-c", command, "fixture", str(env), sys.executable], capture_output=True, text=True, check=True)
        values = json.loads(result.stdout)
        self.assertEqual(values["TOKEN"], TOKEN)
        self.assertEqual(values["DISABLE_SSH"], "true")
        self.assertEqual(values["LISTEN"], "127.0.0.1:45876")
        self.assertEqual(values["NICS"], "re0,ue0")
        self.assertEqual(values["FILESYSTEM"], "/")
        self.assertEqual(values["PACKAGE_UPDATES_INTERVAL"], "0")
        self.assertFalse((ROOT / "SHOULD_NOT_EXIST").exists())

        # Execute the real hook against a fake service, including a repeated boot
        # event and a retained hook whose rc flag has subsequently been disabled.
        service = self.fs / "usr/sbin/service"
        service.parent.mkdir(parents=True)
        hook_calls = self.fixture / "hook-calls"
        hook_state = self.fixture / "hook-running"
        flag = self.fs / "etc/rc.conf.d/beszel_agent"
        service.write_text(
            f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
            f"with open({str(hook_calls)!r}, 'a') as f: f.write(sys.argv[2]+'\\n')\n"
            f"state=Path({str(hook_state)!r})\n"
            f"if sys.argv[2]=='enabled': sys.exit(0 if '\"YES\"' in Path({str(flag)!r}).read_text() else 1)\n"
            "if sys.argv[2]=='status': sys.exit(0 if state.exists() else 1)\n"
            "assert sys.argv[2]=='start'\nstate.touch()\n"
        )
        service.chmod(0o755)
        hook = self.fs / "usr/local/etc/rc.syshook.d/start/99-beszel-agent"
        hook.write_text(hook.read_text().replace("/usr/sbin/service", str(service)))
        for _ in range(2):
            subprocess.run(["/bin/sh", str(hook)], check=True)
        self.assertEqual(hook_calls.read_text().splitlines(), ["enabled", "status", "start", "enabled", "status"])
        flag.write_text('beszel_agent_enable="NO"\n')
        subprocess.run(["/bin/sh", str(hook)], check=True)
        self.assertEqual(hook_calls.read_text().splitlines()[-1], "enabled")

        # A stub rc.subr invokes the real prestart and captures the daemon command.
        # It deliberately does not emulate FreeBSD's process/signal handling.
        rcsubr = self.fs / "etc/rc.subr"
        rcsubr.parent.mkdir(parents=True, exist_ok=True)
        rcsubr.write_text(
            'load_rc_config() { beszel_agent_user=daemon; beszel_agent_flags=stale; beszel_agent_env_file=stale; }\n'
            'run_rc_command() { [ -z "$beszel_agent_user$beszel_agent_flags$beszel_agent_env_file" ] || exit 1; '
            '"$start_precmd" || exit 1; printf "%s\\n" "$command_args"; }\n'
        )
        rc = self.fs / "usr/local/etc/rc.d/beszel-agent"
        install = self.bin / "install"
        install.write_text('#!/bin/sh\nfor last do :; done\nmkdir -p "$last"\nchmod 0700 "$last"\n')
        install.chmod(0o755)
        rc.write_text(rc.read_text().replace("/usr/bin/install", str(install)))
        result = subprocess.run(["/bin/sh", str(rc), "start"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-P " + str(self.fs / "var/run/beszel_agent.pid"), result.stdout)
        self.assertIn("-r -R 5", result.stdout)
        self.assertIn("-u daemon", result.stdout)
        self.assertNotIn(TOKEN, result.stdout)
        self.assertEqual((self.fs / "var/log/beszel_agent.log").stat().st_mode & 0o777, 0o600)
        env.unlink()
        result = subprocess.run(["/bin/sh", str(rc), "start"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
