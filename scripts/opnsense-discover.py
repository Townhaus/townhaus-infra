#!/usr/bin/env python3
"""Read OPNsense VLAN, assignment and DHCP inventory without changing settings."""

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://192.168.2.1")
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument("--ca-file", help="PEM file for the router's certificate issuer")
    tls.add_argument("--insecure", action="store_true", help="Explicitly skip TLS verification for local bootstrap")
    args = parser.parse_args()
    origin = urllib.parse.urlsplit(args.url)
    if origin.scheme != "https" or not origin.netloc or origin.path not in ("", "/") or origin.query or origin.fragment or origin.username:
        parser.error("--url must be an HTTPS origin without credentials or paths")
    key = os.environ.get("OPNSENSE_API_KEY")
    secret = os.environ.get("OPNSENSE_API_SECRET")
    if not key or not secret:
        parser.error("Supply OPNSENSE_API_KEY and OPNSENSE_API_SECRET using op run")
    context = ssl.create_default_context(cafile=args.ca_file)
    if args.insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        print("Local bootstrap: router TLS certificate verification is disabled.", file=sys.stderr)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect())
    auth = base64.b64encode(f"{key}:{secret}".encode()).decode()
    endpoints = {
        "firewall_rules": (
            "/api/firewall/filter/search_rule",
            ("uuid", "description", "sequence", "enabled", "interface", "direction", "quick", "action", "ipprotocol", "protocol", "source_net", "source_port", "destination_net", "destination_port"),
        ),
        "vlans": (
            "/api/interfaces/vlan_settings/search_item",
            ("uuid", "vlanif", "if", "tag", "pcp", "descr"),
        ),
        "assignments": (
            "/api/interfaces/assignment/search_item",
            ("identifier", "ifname", "if", "device", "descr", "description", "enabled", "enable", "ipaddr", "subnet", "ipv4"),
        ),
        "dhcp_ranges": (
            "/api/dnsmasq/settings/search_range",
            ("uuid", "interface", "start_addr", "end_addr", "subnet_mask", "description", "domain", "ra_mode", "ra_only", "ra_slaac"),
        ),
    }
    result = {}
    failed = False
    for name, (path, fields) in endpoints.items():
        req = urllib.request.Request(
            args.url.rstrip("/") + path + "?rowCount=-1",
            headers={"Authorization": "Basic " + auth, "Accept": "application/json"},
        )
        try:
            with opener.open(req, timeout=30) as response:
                data = json.load(response)
            if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
                raise ValueError("Unexpected API response")
            result[name] = {
                "total": data.get("total"),
                "rows": [{field: row[field] for field in fields if field in row} for row in data["rows"]],
            }
        except urllib.error.HTTPError as exc:
            result[name] = {"error": f"HTTP {exc.code}", "endpoint": path}
            failed = True
        except (ValueError, KeyError, TypeError):
            result[name] = {"error": "Unexpected response; no raw data printed", "endpoint": path}
            failed = True
    print(json.dumps(result, indent=2))
    return int(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"Router connection failed: {exc.reason}", file=sys.stderr)
        print("For certificate errors, supply --ca-file or explicitly use --insecure for local bootstrap.", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Unable to read the CA file or connect to the router.", file=sys.stderr)
        sys.exit(1)
