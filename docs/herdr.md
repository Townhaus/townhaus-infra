# Townhaus Infra in Herdr

From your regular terminal or a shell pane inside Herdr:

```bash
cd ~/Work/townhaus-infra
just herdr
```

The launcher requires Herdr on PATH and Python 3. From a regular terminal it
starts or reconnects to the named `townhaus` session and attaches the UI with
the `townhaus-infra` workspace selected. It reuses the workspace with that
label, preserving its tabs and running processes, and creates the layout only
when no matching workspace exists. Inside Herdr it uses the current session
and preserves your current focus. Herdr persists the layout as part of the
session. You can run `just herdr` each time you want to open it.

You can also attach directly from a regular terminal:

```bash
herdr --session townhaus
```

If the named server cannot start, check
`~/.cache/townhaus-infra/herdr-server.log`.

All panes start at the repository root. The launcher opens shells only;
choose commands when you need them.

| Tab | Panes | Useful commands |
| --- | --- | --- |
| `work` | `repo` | `just --list`, `just check`, your preferred coding agent |
| `caddy` | `proxy-config` | Edit `Caddyfile`; review with `git diff -- Caddyfile docker-compose.yml` |
| `beelink` | `stack-operations` | `just status`, `just logs caddy`, `just logs immich-server` |
| `audio` | `aswitch`, `pi-cam` | `just status-aswitch`, `just logs-ir`, `just status-pi-cam` |

Operations commands use the existing SSH hosts and credentials described in
the README. `just caddy-reload` reloads the deployed Caddy instance on beelink;
it does not run a desktop development proxy. Local development domains would
use a separate local Caddy configuration.

If setup fails after creating the workspace, inspect its tabs before retrying.
The launcher leaves any completed layout in place and reports the error.
