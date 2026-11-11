#!/usr/bin/env python3
"""Download private, timestamped OPNsense and UniFi backups using local APIs."""

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
from email.parser import BytesParser
import http.cookiejar
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parent.parent
MAX_BYTES = 1024 * 1024 * 1024


class BackupError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, url, ca_file=None, insecure=False, headers=None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.netloc or parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
            raise BackupError("Expected an HTTPS origin without credentials or paths")
        self.url = url.rstrip("/")
        context = ssl.create_default_context(cafile=ca_file)
        if insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self.cookies = http.cookiejar.CookieJar()
        self.headers = dict(headers or {})
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(self.cookies),
            NoRedirect(),
        )

    def request(self, path, payload=None):
        if not path.startswith("/") or path.startswith("//") or urllib.parse.urlsplit(path).netloc:
            raise BackupError("Refusing a backup URL outside the authenticated origin")
        headers = dict(self.headers)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=body, headers=headers)
        return self.opener.open(request, timeout=300)

    def login(self):
        username = required_env("UNIFI_USERNAME")
        password = required_env("UNIFI_PASSWORD")
        with self.request("/api/auth/login", {"username": username, "password": password}) as response:
            # Do not persist the response, which contains account/session details.
            data = json.load(response)
            if not isinstance(data, dict) or data.get("error"):
                raise BackupError("UniFi login was rejected")
            token = response.headers.get("X-CSRF-Token")
        if token:
            self.headers["X-CSRF-Token"] = token

    def logout(self):
        try:
            with self.request("/api/auth/logout", {}) as response:
                response.read(1024)
        except (OSError, ValueError):
            pass


class CurlClient:
    """GET-only curl transport for hosts Python cannot reach on this Mac.

    Pass authentication through stdin configuration, never process arguments.
    """

    def __init__(self, url, ca_file=None, insecure=False, headers=None):
        # Reuse origin validation without making a network request.
        self.settings = Client(url, ca_file, insecure, headers)
        self.ca_file, self.insecure = ca_file, insecure

    @contextmanager
    def request(self, path):
        if not path.startswith("/") or path.startswith("//"):
            raise BackupError("Refusing a backup URL outside the authenticated origin")
        config = ["url = " + json.dumps(self.settings.url + path)]
        for key, value in self.settings.headers.items():
            config.append("header = " + json.dumps(f"{key}: {value}"))
        if self.ca_file:
            config.append("cacert = " + json.dumps(str(self.ca_file)))
        if self.insecure:
            config.append("insecure")
        with tempfile.TemporaryDirectory(prefix="townhaus-backup-") as temporary:
            body = Path(temporary) / "body"
            headers_file = Path(temporary) / "headers"
            result = subprocess.run(
                ["curl", "--disable", "--silent", "--show-error", "--fail",
                 "--connect-timeout", "20", "--max-time", "300", "--max-filesize", str(MAX_BYTES),
                 "--config", "-", "--output", str(body), "--dump-header", str(headers_file),
                 "--write-out", "%{http_code}"],
                input="\n".join(config) + "\n", text=True, capture_output=True, timeout=310,
            )
            status = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
            if status and not 200 <= status < 300:
                raise urllib.error.HTTPError(self.settings.url, status, "Backup request rejected", {}, None)
            if result.returncode or not status:
                raise BackupError(f"curl backup transfer failed (exit {result.returncode})")
            blocks = headers_file.read_bytes().replace(b"\r\n", b"\n").split(b"\n\n")
            responses = [block for block in blocks if block.startswith(b"HTTP/")]
            if not responses:
                raise BackupError("curl returned no HTTP response headers")
            headers = BytesParser().parsebytes(responses[-1].split(b"\n", 1)[1])
            with body.open("rb") as response:
                response.headers = headers
                yield response


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise BackupError(f"Missing {name}; run through scripts/network-backup.sh")
    return value


def validate_file(path, kind, content_type):
    if path.stat().st_size == 0:
        raise BackupError("Backup response was empty")
    with path.open("rb") as handle:
        prefix = handle.read(1024).lstrip().lower()
    if "text/html" in content_type.lower() or prefix.startswith((b"<!doctype html", b"<html")):
        raise BackupError("Received an HTML page instead of a backup")
    if "application/json" in content_type.lower():
        raise BackupError("Received JSON instead of a backup")
    if kind == "opnsense":
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise BackupError("OPNsense response is not valid configuration XML") from exc
        if root.tag != "opnsense" or root.find("system") is None or root.find("interfaces") is None:
            raise BackupError("XML does not contain an OPNsense system/interface configuration")
    else:
        if path.stat().st_size < 1024:
            raise BackupError("UniFi response is too small to accept as a backup")
        # UniFi archives can be encrypted. Verify transfer/response shape, not
        # restorability; do not claim to decrypt or restore-test them here.
        if prefix.startswith((b"{", b"[", b"<?xml")):
            raise BackupError("Unexpected structured response instead of a UniFi archive")


def download(client, endpoint, directory, filename, kind):
    destination = directory / filename
    partial = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with client.request(endpoint) as response, partial.open("xb") as output:
            os.chmod(partial, 0o600)
            for chunk in iter(lambda: response.read(128 * 1024), b""):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise BackupError("Backup exceeded the 1 GiB transfer limit")
                output.write(chunk)
                digest.update(chunk)
            expected = response.headers.get("Content-Length")
            if expected is not None and size != int(expected):
                raise BackupError("Incomplete backup transfer")
            content_type = response.headers.get("Content-Type", "")
            output.flush()
            os.fsync(output.fileno())
        validate_file(partial, kind, content_type)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return {"file": filename, "bytes": size, "sha256": digest.hexdigest(), "status": "downloaded"}


def network_endpoint(client, site):
    path = "/proxy/network/api/s/" + urllib.parse.quote(site, safe="") + "/cmd/backup"
    with client.request(path, {"cmd": "backup", "days": 0}) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get("meta", {}).get("rc") != "ok" or not result.get("data"):
        raise BackupError("UniFi Network backup generation did not return a download")
    url = result["data"][0].get("url", "")
    if not isinstance(url, str):
        raise BackupError("UniFi returned an unexpected backup download path")
    # Only download a backup path on this controller; never follow arbitrary
    # URLs returned by an API with authenticated credentials/cookies.
    if url.startswith("/dl/backup/"):
        url = "/proxy/network" + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path.startswith("/proxy/network/dl/backup/") or ".." in parts.path:
        raise BackupError("UniFi returned an unexpected backup download path")
    return url


def safe_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}; check backup permissions (401/403) and the endpoint"
    if isinstance(exc, urllib.error.URLError):
        return "Connection/TLS error; verify reachability and the correct CA certificate"
    if isinstance(exc, BackupError):
        return str(exc)
    return "Backup failed; check connectivity, file permissions and API compatibility"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=("both", "opnsense", "unifi"), default="both")
    parser.add_argument("--output-dir", type=Path, default=REPO / "backups" / "network")
    parser.add_argument("--opnsense-url", default="https://192.168.2.1")
    parser.add_argument("--unifi-url", default="https://unifi-admin.home.arpa")
    parser.add_argument("--opnsense-ca-file")
    default_ca = REPO / "certs" / "beelink-caddy-root.crt"
    parser.add_argument("--unifi-ca-file", default=str(default_ca) if default_ca.is_file() else None)
    parser.add_argument("--opnsense-insecure", action="store_true")
    parser.add_argument("--opnsense-transport", choices=("curl", "urllib"), default="curl")
    parser.add_argument("--unifi-insecure", action="store_true")
    parser.add_argument("--unifi-scope", choices=("system", "network", "both"), default="system")
    parser.add_argument("--site", default="default")
    args = parser.parse_args(argv)
    os.umask(0o077)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = args.output_dir / timestamp
    directory.mkdir(mode=0o700)
    manifest = {"created_utc": timestamp, "files": {}, "restore_tested": False}

    def attempt(name, operation):
        try:
            manifest["files"][name] = operation()
            print(f"{name}: saved {manifest['files'][name]['bytes']} bytes")
        except (OSError, ValueError, KeyError, IndexError, http.client.HTTPException, subprocess.TimeoutExpired, BackupError) as exc:
            message = safe_error(exc)
            manifest["files"][name] = {"status": "failed", "error": message}
            print(f"{name}: {message}", file=sys.stderr)

    if args.only in ("both", "opnsense"):
        def opnsense_backup():
            auth = base64.b64encode((required_env("OPNSENSE_API_KEY") + ":" + required_env("OPNSENSE_API_SECRET")).encode()).decode()
            transport = CurlClient if args.opnsense_transport == "curl" else Client
            client = transport(args.opnsense_url, args.opnsense_ca_file, args.opnsense_insecure, {"Authorization": "Basic " + auth})
            return download(client, "/api/core/backup/download/this", directory, "opnsense-config.xml", "opnsense")
        attempt("opnsense", opnsense_backup)

    if args.only in ("both", "unifi"):
        client = None
        try:
            client = Client(args.unifi_url, args.unifi_ca_file, args.unifi_insecure)
            client.login()
            if args.unifi_scope in ("system", "both"):
                attempt("unifi_system", lambda: download(client, "/api/backup/download", directory, "unifi-system.unifi", "unifi"))
            if args.unifi_scope in ("network", "both"):
                attempt("unifi_network", lambda: download(client, network_endpoint(client, args.site), directory, "unifi-network.unf", "unifi"))
        except (OSError, ValueError, http.client.HTTPException, BackupError) as exc:
            message = safe_error(exc)
            manifest["files"]["unifi_login"] = {"status": "failed", "error": message}
            print(f"unifi: {message}", file=sys.stderr)
        finally:
            if client is not None and client.cookies:
                client.logout()

    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    checksums = [f"{item['sha256']}  {item['file']}" for item in manifest["files"].values() if item["status"] == "downloaded"]
    (directory / "SHA256SUMS").write_text("\n".join(checksums) + ("\n" if checksums else ""))
    print(f"Backup directory: {directory}")
    return int(any(item["status"] != "downloaded" for item in manifest["files"].values()))


if __name__ == "__main__":
    sys.exit(main())
