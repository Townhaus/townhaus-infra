#!/usr/bin/env python3
"""Read UniFi inventory for import, printing only an explicit field allowlist."""

import argparse
import http.cookiejar
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def select_fields(items, fields):
    return [{key: item[key] for key in fields if key in item} for item in items]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://unifi-admin.home.arpa")
    parser.add_argument("--site", default="default")
    parser.add_argument("--ca-file", help="PEM file containing the trusted Caddy root CA")
    parser.add_argument("--switches", action="store_true", help="Include filtered switch port/VLAN configuration")
    args = parser.parse_args()
    origin = urllib.parse.urlsplit(args.url)
    if origin.scheme != "https" or not origin.netloc or origin.path not in ("", "/") or origin.query or origin.fragment or origin.username:
        parser.error("--url must be an HTTPS origin without credentials or paths")
    username = os.environ.get("UNIFI_USERNAME")
    password = os.environ.get("UNIFI_PASSWORD")
    if not username or not password:
        parser.error("Supply UNIFI_USERNAME and UNIFI_PASSWORD using op run")

    # Do not forward the login body or session cookies to another origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    context = ssl.create_default_context(cafile=args.ca_file)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        NoRedirect(),
    )

    def request(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            args.url.rstrip("/") + path,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="GET" if body is None else "POST",
        )
        with opener.open(req, timeout=30) as response:
            result = json.load(response)
        if isinstance(result, dict) and result.get("meta", {}).get("rc", "ok") != "ok":
            raise ValueError("Controller API rejected the request")
        return result

    request("/api/auth/login", {"username": username, "password": password})
    sites = request("/proxy/network/api/self/sites")["data"]
    safe_sites = select_fields(sites, ("name", "desc", "_id"))
    if args.site not in {site.get("name") for site in sites}:
        print(json.dumps({"sites": safe_sites}, indent=2))
        print("Requested site not found; rerun with --site and its name above.", file=sys.stderr)
        return 1

    base = "/proxy/network/api/s/" + urllib.parse.quote(args.site, safe="")
    networks = request(base + "/rest/networkconf")["data"]
    wlans = request(base + "/rest/wlanconf")["data"]
    groups = request(base + "/rest/usergroup")["data"]
    result = {
        "site": args.site,
        "sites": safe_sites,
        "networks": select_fields(networks, ("_id", "name", "purpose", "vlan", "ip_subnet")),
        "user_groups": select_fields(groups, ("_id", "name")),
        "wifi": select_fields(wlans, (
            "_id", "name", "networkconf_id", "usergroup_id", "enabled",
            "security", "wpa_mode", "wpa_enc", "wpa3_support", "wpa3_transition",
            "pmf_mode", "wlan_band", "wlan_bands", "ap_group_ids",
            "ap_group_mode", "l2_isolation", "is_guest", "hide_ssid",
            "fast_roaming_enabled", "bss_transition", "uapsd", "multicast_enhance",
            "private_preshared_keys_enabled",
        )),
    }
    if args.switches:
        devices = request(base + "/stat/device")["data"]
        port_fields = (
            "port_idx", "name", "portconf_id", "native_networkconf_id",
            "tagged_vlan_mgmt", "excluded_networkconf_ids", "allowed_networkconf_ids",
            "forward", "op_mode", "up", "speed", "is_uplink",
        )
        result["switches"] = []
        for device in devices:
            if device.get("type") != "usw":
                continue
            switch = select_fields([device], ("_id", "name", "mac", "ip", "model"))[0]
            switch["port_overrides"] = select_fields(device.get("port_overrides", []), port_fields)
            switch["port_table"] = select_fields(device.get("port_table", []), port_fields)
            result["switches"].append(switch)
        profiles = request(base + "/rest/portconf")["data"]
        result["port_profiles"] = select_fields(profiles, (
            "_id", "name", "native_networkconf_id", "tagged_networkconf_ids",
            "tagged_vlan_mgmt", "excluded_networkconf_ids", "forward",
        ))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as exc:
        # Response bodies can contain sensitive controller data; never print them.
        print(f"Controller request failed: HTTP {exc.code}. Check the account permissions and URL.", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Controller connection failed: {exc.reason}", file=sys.stderr)
        print("For certificate errors, use --ca-file with your Caddy root CA PEM.", file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError, OSError):
        print("Discovery failed: check the CA file, controller API and site. No raw response was printed.", file=sys.stderr)
        sys.exit(1)
