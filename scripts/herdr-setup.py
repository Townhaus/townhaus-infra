#!/usr/bin/env python3
"""Open the Townhaus Infra layout, creating it only when missing."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


REPO = Path(__file__).resolve().parents[1]
INSIDE_HERDR = os.environ.get("HERDR_ENV") == "1"
SESSION = "townhaus"
CLI = ["herdr"] if INSIDE_HERDR else ["herdr", "--session", SESSION]


def herdr(*args):
    completed = subprocess.run(
        [*CLI, *args], text=True, capture_output=True, check=True, timeout=15
    )
    response = json.loads(completed.stdout)
    if response.get("error"):
        raise RuntimeError(str(response["error"]))
    return response["result"]


def main():
    if not shutil.which("herdr"):
        sys.exit("herdr must be installed and on PATH.")

    if not INSIDE_HERDR:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            sys.exit("Run just herdr from an interactive terminal.")
        ensure_server()

    workspaces = herdr("workspace", "list")["workspaces"]
    existing = next(
        (workspace for workspace in workspaces
         if workspace["label"] == "townhaus-infra"),
        None,
    )
    if existing:
        workspace_id = existing["workspace_id"]
        print(f"Reusing townhaus-infra ({workspace_id}).", flush=True)
    else:
        workspace_id = create_layout()

    if not INSIDE_HERDR:
        herdr("workspace", "focus", workspace_id)
        # Replace the launcher with the terminal UI; the server keeps running.
        os.chdir(REPO)
        os.execvp("herdr", ["herdr", "--session", SESSION])


def create_layout():
    created = herdr(
        "workspace", "create", "--cwd", str(REPO),
        "--label", "townhaus-infra", "--no-focus",
    )
    workspace_id = created["workspace"]["workspace_id"]
    herdr("tab", "rename", created["tab"]["tab_id"], "work")
    herdr("pane", "rename", created["root_pane"]["pane_id"], "repo")

    for label, pane_label in (
        ("caddy", "proxy-config"),
        ("beelink", "stack-operations"),
        ("audio", "aswitch"),
    ):
        tab = herdr(
            "tab", "create", "--workspace", workspace_id,
            "--cwd", str(REPO), "--label", label, "--no-focus",
        )
        pane_id = tab["root_pane"]["pane_id"]
        herdr("pane", "rename", pane_id, pane_label)
        if label == "audio":
            split = herdr(
                "pane", "split", "--pane", pane_id,
                "--direction", "right", "--cwd", str(REPO), "--no-focus",
            )
            herdr("pane", "rename", split["pane"]["pane_id"], "pi-cam")

    print(f"Created townhaus-infra ({workspace_id}) in the current Herdr session.")
    print("Tabs: work, caddy, beelink, audio. All panes start at the repo root.")
    print("Select it in the sidebar when ready. See docs/herdr.md for commands.")
    return workspace_id


def ensure_server():
    """Start the dedicated session if needed, then wait for its API."""
    try:
        herdr("workspace", "list")
        return
    except subprocess.CalledProcessError:
        pass

    log_path = Path.home() / ".cache" / "townhaus-infra" / "herdr-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log:
        server = subprocess.Popen(
            [*CLI, "server"], cwd=REPO, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True,
        )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            herdr("workspace", "list")
            return
        except subprocess.CalledProcessError:
            if server.poll() is not None:
                break
            time.sleep(0.2)
    sys.exit(f"Could not connect to the townhaus session. See {log_path}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.stderr.strip() or str(exc))
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.exit(str(exc))
    except (KeyError, ValueError, RuntimeError) as exc:
        sys.exit(f"Unexpected Herdr response: {exc}")
