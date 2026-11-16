# AdGuard DNS settings

AdGuard Home on the Beelink is the only DNS server for the LAN, Home Assistant
included. The `adguard_dns` role manages two things through the AdGuard API:

- DNS rewrites (`adguard_dns_rewrites` in group_vars), pointing short names and
  `.home.arpa` names at Caddy.
- Upstream, cache and rate-limit settings (`adguard_dns_settings` in
  `ansible/roles/adguard_dns/defaults/main.yml`), applied live through
  `/control/dns_config`. Only the listed keys are sent, and only when one of
  them differs from what AdGuard reports, so re-running the playbook is a no-op.

## Why these settings

On 2026-11-15 Home Assistant logged "Timeout while contacting DNS servers" for
Frigate, NWS and Reolink in short bursts (about 19:13, 19:56 and 20:29 PT).
AdGuard's log showed its plain-UDP queries to 1.1.1.1 / 1.0.0.1 going
unanswered for 20 s at the same moments, and an ESPHome node on the IoT VLAN
dropped at the same times, so the root cause looks like brief stalls at the
gateway. The settings make AdGuard ride those out instead of passing them on:

| Setting | Value | Reason |
|---|---|---|
| `upstream_dns` | Cloudflare and Quad9 DoH | TCP/HTTPS copes with brief packet loss better than single UDP queries. |
| `upstream_mode` | `parallel` | First answer wins; one stalled upstream no longer stalls the lookup. |
| `fallback_dns` | 9.9.9.9, 1.1.1.1 | Last resort when every upstream fails. |
| `cache_optimistic` | `true` | Serves expired entries while upstreams are unreachable. |
| `ratelimit` | `0` | The limit is per /24, so all LAN clients shared 20 queries/s and the excess was dropped silently. |

Apply with the Beelink playbook (`ansible/playbooks/beelink.yml`), which runs
the `adguard_dns` role.
