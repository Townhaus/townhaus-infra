#!/usr/bin/env python3
"""Offline backup tests; no credentials or network connections are used."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location("network_backup", Path(__file__).with_name("network-backup.py"))
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)

XML = b"<?xml version='1.0'?><opnsense><system/><interfaces/><revision><time>123</time></revision></opnsense>"


class Response(io.BytesIO):
    def __init__(self, data, content_type="application/octet-stream", length=None):
        super().__init__(data)
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data) if length is None else length)}


class DownloadClient:
    def __init__(self, data, content_type="application/octet-stream", length=None):
        self.data, self.content_type, self.length = data, content_type, length

    def request(self, path, payload=None):
        return Response(self.data, self.content_type, self.length)


class BackupTests(unittest.TestCase):
    def test_xml_saved_privately_with_matching_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = backup.download(DownloadClient(XML), "/backup", root, "config.xml", "opnsense")
            self.assertEqual((root / "config.xml").read_bytes(), XML)
            self.assertEqual(result["sha256"], hashlib.sha256(XML).hexdigest())
            self.assertEqual((root / "config.xml").stat().st_mode & 0o777, 0o600)

    def test_bad_downloads_leave_no_completed_or_partial_files(self):
        cases = [
            (b"<html>login</html>", "text/html", None, "opnsense"),
            (b'{"error":"denied"}', "application/json", None, "unifi"),
            (b"&lt;opnsense&gt;escaped&lt;/opnsense&gt;", "application/octet-stream", None, "opnsense"),
            (XML, "application/octet-stream", len(XML) + 1, "opnsense"),
            (b"", "application/octet-stream", None, "unifi"),
            (b"short", "application/octet-stream", None, "unifi"),
        ]
        for data, content_type, length, kind in cases:
            with self.subTest(kind=kind, data=data[:20]), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(backup.BackupError):
                    backup.download(DownloadClient(data, content_type, length), "/backup", Path(directory), "test.backup", kind)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unifi_binary_preserved_without_claiming_restore_validation(self):
        data = b"UNIFI-test-archive\x00" * 100
        with tempfile.TemporaryDirectory() as directory:
            result = backup.download(DownloadClient(data), "/backup", Path(directory), "test.unifi", "unifi")
            self.assertEqual(result["bytes"], len(data))

    def test_unifi_network_url_rejects_external_origin(self):
        for url in ("https://attacker.invalid/file", "//attacker.invalid/file", "/proxy/network/dl/backup/../secret"):
            data = json.dumps({"meta": {"rc": "ok"}, "data": [{"url": url}]}).encode()
            with self.subTest(url=url), self.assertRaises(backup.BackupError):
                backup.network_endpoint(DownloadClient(data), "default")

    def test_partial_failure_preserves_success_and_returns_failure(self):
        class FakeClient:
            def __init__(self, url, *args, **kwargs):
                self.unifi = "unifi" in url
                self.cookies = []

            def login(self):
                pass

            def request(self, path, payload=None):
                if self.unifi:
                    raise urllib.error.HTTPError("https://unifi.invalid", 403, "denied", {}, None)
                return Response(XML)

        with tempfile.TemporaryDirectory() as directory, patch.object(backup, "Client", FakeClient), patch.dict(
            backup.os.environ, {"OPNSENSE_API_KEY": "test", "OPNSENSE_API_SECRET": "test"}
        ), patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            result = backup.main(["--output-dir", directory, "--opnsense-transport", "urllib"])
            self.assertEqual(result, 1)
            run = next(Path(directory).iterdir())
            manifest = json.loads((run / "manifest.json").read_text())
            self.assertEqual(manifest["files"]["opnsense"]["status"], "downloaded")
            self.assertEqual(manifest["files"]["unifi_system"]["status"], "failed")
            self.assertFalse(manifest["restore_tested"])
            self.assertTrue((run / "opnsense-config.xml").exists())
            self.assertFalse((run / "unifi-system.unifi").exists())
            self.assertEqual((run / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_curl_keeps_authentication_out_of_process_arguments(self):
        captured = {}

        def fake_run(argv, **kwargs):
            captured.update(argv=argv, config=kwargs["input"])
            Path(argv[argv.index("--output") + 1]).write_bytes(XML)
            Path(argv[argv.index("--dump-header") + 1]).write_bytes(
                b"HTTP/2 200\r\nContent-Type: application/octet-stream\r\n\r\n"
            )
            return backup.subprocess.CompletedProcess(argv, 0, "200", "")

        with tempfile.TemporaryDirectory() as directory, patch.object(backup.subprocess, "run", fake_run):
            client = backup.CurlClient("https://192.168.2.1", insecure=True, headers={"Authorization": "Basic TEST_SECRET"})
            backup.download(client, "/api/core/backup/download/this", Path(directory), "config.xml", "opnsense")
            self.assertNotIn("TEST_SECRET", " ".join(captured["argv"]))
            self.assertIn("Basic TEST_SECRET", captured["config"])
            self.assertNotIn("--location", captured["argv"])


if __name__ == "__main__":
    unittest.main()
