"""Command-line interface for sdale.

This is the main entry point. Uses argparse (stdlib) for command parsing.
Each subcommand maps to a function that orchestrates config loading,
remote execution, and event logging.

Usage:
    sdale connect <dale>
    sdale watch <dale>
    sdale exec <dale> "<command>"
    sdale push <dale> <local-file> <remote-path>
    sdale run <dale> "<command>"
    sdale output <dale> [--lines N]
    sdale sync <dale> <src> [dst]
    sdale status [dale]
    sdale list
    sdale log <dale> [--full | --since DURATION]
    sdale disconnect <dale>
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .config import DaleConfig, get_dale, list_dales, find_config_path
from .logger import EventLogger
from .remote import (
    rsync,
    scp_from,
    scp_to,
    ssh,
    tmux_capture,
    tmux_ensure,
    tmux_has_session,
    tmux_kill,
    tmux_send,
    tmux_send_wait,
)


# ── Output helpers ───────────────────────────────────────────────────


def info(msg: str) -> None:
    """Print an informational message with the sdale horse emoji prefix."""
    print(f"\U0001F40E {msg}")


def err(msg: str) -> None:
    """Print an error message to stderr."""
    print(f"sdale: {msg}", file=sys.stderr)


# ── Helpers ──────────────────────────────────────────────────────────


def _install_watch_script(dale: DaleConfig) -> None:
    """Install sdale-watch helper script in the shared volume.

    Creates /opt/stacks/.sdale-watch (visible as /workspace/.sdale-watch
    inside containers). The script takes a dale name and tails its log,
    or lists available logs if no name given.

    Idempotent — only writes once.
    """
    script_path = "/opt/stacks/.sdale-watch"
    try:
        result = ssh(dale, f"test -x {script_path} && echo exists || echo missing", capture=True)
        if "exists" in result.stdout:
            return
    except subprocess.CalledProcessError:
        pass

    script = r'''#!/bin/bash
# sdale-watch — tail agent activity logs
# Installed by: sdale connect
LOG_DIR="${SDALE_LOG_DIR:-/workspace}"
if [ -z "$1" ]; then
    echo "🐎 sdale activity logs:"
    for f in "$LOG_DIR"/.sdale-*.log; do
        [ -f "$f" ] || continue
        name=$(basename "$f" | sed 's/^\.sdale-//;s/\.log$//')
        lines=$(wc -l < "$f" 2>/dev/null || echo 0)
        last=$(tail -1 "$f" 2>/dev/null | head -c 80)
        echo "  $name ($lines lines) — $last"
    done
    echo ""
    echo "Usage: sdale-watch <name>        # tail one log"
    echo "       sdale-watch --all         # tail all logs"
    exit 0
fi
if [ "$1" = "--all" ]; then
    tail -f "$LOG_DIR"/.sdale-*.log
else
    LOG="$LOG_DIR/.sdale-$1.log"
    if [ ! -f "$LOG" ]; then
        echo "No log for '$1'. Run: sdale-watch (no args) to list available."
        exit 1
    fi
    tail -f "$LOG"
fi
'''
    try:
        ssh(dale, f"cat > {script_path} << 'SDALESCRIPT'\n{script}SDALESCRIPT\nchmod +x {script_path}", capture=True)
        # Also symlink into /usr/local/bin on any running clide containers
        # so sdale-watch is on PATH inside the container
        ssh(dale,
            "for c in $(docker ps --format '{{.Names}}' --filter name=clide 2>/dev/null); do "
            f"docker exec \"$c\" ln -sf /workspace/.sdale-watch /usr/local/bin/sdale-watch 2>/dev/null; "
            "done",
            capture=True)
    except subprocess.CalledProcessError:
        pass  # best effort


# ── Subcommands ──────────────────────────────────────────────────────


def cmd_connect(args: argparse.Namespace) -> None:
    """Connect to a dale and set up the activity log.

    Creates a tmux session for `sdale run` commands and initializes
    the activity log file for `sdale watch`.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)

    info(f"Connecting to dale '{dale.name}' ({dale.ssh_dest})...")
    tmux_ensure(dale)

    # Initialize the activity log in the shared volume (visible inside containers)
    log_file = f"/opt/stacks/.sdale-{dale.name}.log"
    try:
        ssh(dale, f"touch {log_file} && echo '── sdale connected ({dale.name}) ──' >> {log_file}", capture=True)
    except subprocess.CalledProcessError:
        pass

    # Drop a watch helper script into the shared volume (idempotent)
    _install_watch_script(dale)

    logger.log("dale_connect", tmux_session=dale.session, host=dale.host)
    info(f"tmux session '{dale.session}' ready")
    info(f"Watch with: sdale-watch {dale.name}  (from clide ttyd)")


def cmd_watch(args: argparse.Namespace) -> None:
    """Watch agent activity on a dale in real time.

    Tails the activity log file on the remote host. All sdale exec
    and run commands are logged here with timestamps, commands, and
    output. Ctrl-c to stop watching.
    """
    dale = get_dale(args.dale)
    log_file = f"/opt/stacks/.sdale-{dale.name}.log"

    info(f"Watching dale '{dale.name}' — Ctrl-c to stop")
    print()

    cmd = ["ssh", *dale.ssh_args, "-t", dale.ssh_dest,
           f"touch {log_file} && tail -f {log_file}"]
    try:
        os.execvp("ssh", cmd)
    except KeyboardInterrupt:
        pass


def cmd_exec(args: argparse.Namespace) -> None:
    """Run a command on the dale via direct SSH (no tmux).

    Unlike `run`, this captures stdout/stderr directly and returns
    the exit code. Use this for scripting, automation, or any command
    with complex quoting that tmux send-keys would mangle.

    With --merge-stderr / -e, stderr is printed to stdout instead of
    stderr. This avoids needing ``2>&1`` in the outer shell (which
    breaks allowlist patterns like ``Bash(sdale exec:*)``).
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    command = args.command
    merge = getattr(args, "merge_stderr", False)
    stderr_dest = sys.stdout if merge else sys.stderr

    try:
        result = ssh(dale, command, capture=True, log=True)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=stderr_dest)
        logger.log("dale_exec", command=command, exit_code="0")
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        if exc.stderr:
            print(exc.stderr, end="", file=stderr_dest)
        logger.log("dale_exec", command=command, exit_code=str(exc.returncode))
        sys.exit(exc.returncode)


def cmd_push(args: argparse.Namespace) -> None:
    """Copy a local file to the dale via scp.

    Useful for deploying config files (.env, etc.) without needing
    to set up a full rsync directory sync.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    src = args.src
    dst = args.dst

    scp_to(dale, src, dst)
    logger.log("dale_push", src=src, dst=dst)
    info(f"Pushed {src} → {dale.name}:{dst}")


def cmd_pull(args: argparse.Namespace) -> None:
    """Copy a file from the dale to local.

    Inverse of ``push``. Downloads a single remote file via scp.
    If no local destination is given, saves to the current directory
    using the remote filename.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    remote = args.remote
    local = args.local or os.path.basename(remote)

    scp_from(dale, remote, local)
    logger.log("dale_pull", remote=remote, local=local)
    info(f"Pulled {dale.name}:{remote} → {local}")


def cmd_cat(args: argparse.Namespace) -> None:
    """Read one or more remote files and print their contents.

    When reading multiple files, each file's output is preceded by
    a header line showing the path.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    paths = args.paths
    multi = len(paths) > 1

    for path in paths:
        try:
            result = ssh(dale, f"cat '{path}'", capture=True)
            if multi:
                print(f"── {path} ──")
            if result.stdout:
                print(result.stdout, end="")
            if multi:
                print()  # blank line between files
        except subprocess.CalledProcessError as exc:
            if multi:
                print(f"── {path} ──")
            err(f"{path}: {exc.stderr.strip() if exc.stderr else 'not found'}")

    logger.log("dale_cat", paths=",".join(paths), count=str(len(paths)))


def cmd_multi(args: argparse.Namespace) -> None:
    """Run multiple commands in a single SSH round-trip.

    Each command runs sequentially on the dale. Output is formatted
    with separator headers between commands. Stderr is merged into
    stdout per-command.

    Exit code is 0 if all commands succeed, otherwise the last
    non-zero exit code.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    commands = args.commands
    last_error = 0

    # Build a single SSH command that runs all commands with separators
    # Each command's stderr is merged and exit code is captured
    parts = []
    for cmd in commands:
        # Escape for shell embedding, merge stderr
        safe = cmd.replace("'", "'\\''")
        parts.append(f"echo '── {safe[:80]} ──'; {{ {cmd} ; }} 2>&1; echo")
    combined = "; ".join(parts)

    try:
        result = ssh(dale, combined, capture=True)
        if result.stdout:
            print(result.stdout, end="")
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        last_error = exc.returncode

    logger.log("dale_multi", commands=";".join(commands),
               count=str(len(commands)))

    if last_error:
        sys.exit(last_error)


def cmd_health(args: argparse.Namespace) -> None:
    """Quick health check for a dale.

    Checks SSH connectivity, tmux session status, disk usage, load
    average, and optionally Docker container status. Prints a
    single-line summary or detailed report.
    """
    import time as _time

    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)

    # Measure SSH round-trip
    t0 = _time.monotonic()
    try:
        result = ssh(dale, "echo ok", capture=True)
        latency_ms = int((_time.monotonic() - t0) * 1000)
        if "ok" not in (result.stdout or ""):
            err(f"{dale.name}: SSH connected but unexpected output")
            sys.exit(1)
    except subprocess.CalledProcessError:
        err(f"{dale.name}: SSH connection failed")
        sys.exit(1)

    # Gather system info in one round-trip
    checks = (
        "cat /proc/loadavg | awk '{print $1}'",
        "df -h / | tail -1 | awk '{print $5}'",
        "uptime -p 2>/dev/null || uptime",
    )
    tmux_check = f"tmux has-session -t '{dale.session}' 2>/dev/null && echo tmux:yes || echo tmux:no"
    docker_check = "docker ps --format '{{.Names}}:{{.Status}}' 2>/dev/null | head -20"

    info_cmd = "; ".join([
        f"echo LOAD=$({checks[0]})",
        f"echo DISK=$({checks[1]})",
        f"echo UP=$({checks[2]})",
        tmux_check,
    ])

    if args.docker:
        info_cmd += f"; echo DOCKER_START; {docker_check}; echo DOCKER_END"

    try:
        result = ssh(dale, info_cmd, capture=True)
    except subprocess.CalledProcessError:
        # Partial failure is fine — print what we have
        result = subprocess.CompletedProcess([], 0, stdout="")

    output = result.stdout or ""
    load = disk = up = tmux_status = "?"
    docker_lines = []

    for line in output.splitlines():
        if line.startswith("LOAD="):
            load = line[5:]
        elif line.startswith("DISK="):
            disk = line[5:]
        elif line.startswith("UP="):
            up = line[3:]
        elif line.startswith("tmux:"):
            tmux_status = "running" if "yes" in line else "no session"

    # Parse docker output
    in_docker = False
    for line in output.splitlines():
        if line == "DOCKER_START":
            in_docker = True
            continue
        if line == "DOCKER_END":
            in_docker = False
            continue
        if in_docker and line.strip():
            docker_lines.append(line.strip())

    # Print summary
    tmux_icon = "\U0001F7E2" if tmux_status == "running" else "\u26AA"
    info(f"{dale.name}: \u2705 SSH ok ({latency_ms}ms) | {tmux_icon} tmux: {tmux_status} | disk: {disk} | load: {load}")

    if args.docker and docker_lines:
        print(f"  Containers ({len(docker_lines)}):")
        for line in docker_lines:
            print(f"    {line}")
    elif args.docker:
        print("  No Docker containers found")

    logger.log("dale_health", latency_ms=str(latency_ms), tmux=tmux_status,
               disk=disk, load=load)


def cmd_run(args: argparse.Namespace) -> None:
    """Send a command to the dale's tmux session.

    The command is sent as keystrokes, so it appears in the tmux
    session exactly as if someone typed it. The human watching
    the session sees everything in real time.

    With --wait, blocks until the command finishes and prints output.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    command = args.command

    # Log to activity file so watchers see run commands too
    log_file = f"/opt/stacks/.sdale-{dale.name}.log"
    safe = command.replace("'", "'\\''")[:200]
    try:
        ssh(dale, f"echo '\\n── '$(date +\"%H:%M:%S\")' ── [run] $ {safe}' >> {log_file}", capture=True)
    except subprocess.CalledProcessError:
        pass

    if args.wait:
        info(f"[{dale.name}] $ {command}")
        timeout = args.timeout
        output = tmux_send_wait(dale, command, timeout=timeout)
        print(output, end="")
        logger.log("dale_run", command=command, wait="true")
    else:
        tmux_send(dale, command)
        logger.log("dale_run", command=command)
        info(f"[{dale.name}] $ {command}")


def cmd_output(args: argparse.Namespace) -> None:
    """Capture and print recent output from the dale's tmux pane.

    Defaults to 20 lines. Use --lines to adjust.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    lines = args.lines

    output = tmux_capture(dale, lines=lines)
    print(output, end="")

    logger.log("dale_output", lines=str(lines))


def cmd_sync(args: argparse.Namespace) -> None:
    """Rsync a local directory to the dale.

    Excludes patterns defined in the dale's config (defaults to
    node_modules and .git). Prints the rsync output so you can
    see what was transferred.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    src = args.src
    dst = args.dst

    info(f"Syncing {src} → {dale.name}:{dst}...")
    output = rsync(dale, src, dst)
    print(output, end="")

    # Count transferred files (lines not starting with sending/receiving/total)
    file_count = sum(
        1 for line in output.splitlines()
        if line and not line.startswith(("sending", "sent", "total", "created"))
    )

    logger.log("dale_sync", src=src, dst=dst, files=str(file_count))
    info("Sync complete")


def cmd_status(args: argparse.Namespace) -> None:
    """Show the status of a specific dale, or list all dales."""
    if not args.dale:
        cmd_list(args)
        return

    dale = get_dale(args.dale)
    has_session = tmux_has_session(dale)

    info(f"Dale: {dale.name}")
    print(f"  Host:    {dale.ssh_dest}")
    print(f"  Key:     {dale.key}")
    print(f"  Session: {dale.session}")
    if has_session:
        print("  Status:  \U0001F7E2 connected")
    else:
        print("  Status:  \u26AA no active session")


def cmd_list(args: argparse.Namespace) -> None:
    """List all configured dales from sdale.json."""
    config_path = find_config_path()
    if config_path is None:
        err("No sdale.json found")
        sys.exit(1)

    dales = list_dales()
    info(f"Configured dales ({config_path}):")

    if not dales:
        print("  (none)")
        return

    # Find the longest name for alignment
    max_name = max(len(name) for name in dales)
    for name, cfg in dales.items():
        host = cfg.get("host", "—")
        user = cfg.get("user", "—")
        print(f"  {name:<{max_name}}  {host}  {user}")


def cmd_log(args: argparse.Namespace) -> None:
    """Show the event log for a dale.

    Modes:
        (default)     Last 20 events
        --full        All events
        --since DUR   Events newer than duration (e.g. 1h, 30m, 2d)
    """
    log_path = EventLogger.get_log_path(args.dale)
    if log_path is None:
        err(f"No logs for dale '{args.dale}'")
        sys.exit(1)

    lines = log_path.read_text().splitlines()

    if args.full:
        for line in lines:
            print(line)
    elif args.since:
        cutoff = _parse_since(args.since)
        for line in lines:
            try:
                event = json.loads(line)
                event_ts = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
                if event_ts >= cutoff:
                    print(line)
            except (json.JSONDecodeError, KeyError):
                continue
    else:
        # Default: last 20
        for line in lines[-20:]:
            print(line)


def cmd_disconnect(args: argparse.Namespace) -> None:
    """Kill the tmux session on a dale."""
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)

    if tmux_kill(dale):
        logger.log("dale_disconnect")
        info(f"Disconnected from dale '{dale.name}'")
    else:
        err(f"No active session '{dale.session}' on {dale.name}")
        sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────


def _parse_since(duration: str) -> datetime:
    """Parse a human duration string into a UTC cutoff datetime.

    Supported suffixes: m (minutes), h (hours), d (days).

    Args:
        duration: A string like "30m", "1h", "2d".

    Returns:
        A timezone-aware datetime representing the cutoff.

    Raises:
        ValueError: If the duration format is not recognized.
    """
    now = datetime.now(timezone.utc)
    unit = duration[-1]
    try:
        amount = int(duration[:-1])
    except ValueError:
        raise ValueError(f"Invalid duration: '{duration}'. Use e.g. 30m, 1h, 2d")

    if unit == "m":
        return now - timedelta(minutes=amount)
    elif unit == "h":
        return now - timedelta(hours=amount)
    elif unit == "d":
        return now - timedelta(days=amount)
    else:
        raise ValueError(f"Unknown duration unit '{unit}'. Use m, h, or d")


# ── Argument parsing ─────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with all subcommands.

    Returns:
        The configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="sdale",
        description="\U0001F40E sdale — give your agent a VPS",
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"sdale {__version__}"
    )
    sub = parser.add_subparsers(dest="subcmd", help="Available commands")

    # connect
    p = sub.add_parser("connect", help="Create/reuse tmux session on a dale")
    p.add_argument("dale", help="Dale name from sdale.json")

    # watch
    p = sub.add_parser("watch", help="Attach to the dale's tmux session (live view)")
    p.add_argument("dale", help="Dale name from sdale.json")

    # exec
    p = sub.add_parser("exec", help="Run a command via direct SSH (no tmux)")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("command", help="Command to run (quote it)")
    p.add_argument("--merge-stderr", "-e", action="store_true",
                    help="Print stderr to stdout (avoids outer 2>&1)")

    # push
    p = sub.add_parser("push", help="Copy a local file to the dale via scp")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("src", help="Local file path")
    p.add_argument("dst", help="Remote destination path")

    # pull
    p = sub.add_parser("pull", help="Copy a file from the dale to local")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("remote", help="Remote file path on the dale")
    p.add_argument("local", nargs="?", default="", help="Local destination (default: current dir, same filename)")

    # cat
    p = sub.add_parser("cat", help="Read remote file(s) and print contents")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("paths", nargs="+", help="Remote file path(s) to read")

    # multi
    p = sub.add_parser("multi", help="Run multiple commands in one SSH round-trip")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("commands", nargs="+", help="Commands to run (each quoted separately)")

    # health
    p = sub.add_parser("health", help="Quick dale connectivity and status check")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("--docker", "-d", action="store_true", help="Include Docker container status")

    # run
    p = sub.add_parser("run", help="Send a command to the dale's tmux session")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("command", help="Command to run (quote it)")
    p.add_argument("--wait", "-w", action="store_true", help="Wait for command to finish and print output")
    p.add_argument("--timeout", "-t", type=int, default=300, help="Timeout in seconds for --wait (default: 300)")

    # output
    p = sub.add_parser("output", help="Capture recent tmux pane output")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("--lines", "-n", type=int, default=20, help="Number of lines (default: 20)")

    # sync
    p = sub.add_parser("sync", help="Rsync local directory to the dale")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("src", help="Local source directory")
    p.add_argument("dst", nargs="?", default="/tmp/sdale-sync", help="Remote destination (default: /tmp/sdale-sync)")

    # status
    p = sub.add_parser("status", help="Show dale status")
    p.add_argument("dale", nargs="?", default="", help="Dale name (omit to list all)")

    # list
    sub.add_parser("list", help="List configured dales")

    # log
    p = sub.add_parser("log", help="Show command log for a dale")
    p.add_argument("dale", help="Dale name")
    p.add_argument("--full", action="store_true", help="Show full log")
    p.add_argument("--since", metavar="DUR", help="Filter by duration (e.g. 1h, 30m, 2d)")

    # disconnect
    p = sub.add_parser("disconnect", help="Kill tmux session on a dale")
    p.add_argument("dale", help="Dale name from sdale.json")

    return parser


# ── Entry point ──────────────────────────────────────────────────────


def main() -> None:
    """Main entry point for the sdale CLI.

    Parses arguments and dispatches to the appropriate subcommand.
    Handles common exceptions with user-friendly error messages.
    """
    parser = build_parser()
    args = parser.parse_args()

    if not args.subcmd:
        parser.print_help()
        sys.exit(0)

    # Map subcommands to functions
    commands = {
        "connect": cmd_connect,
        "watch": cmd_watch,
        "exec": cmd_exec,
        "push": cmd_push,
        "pull": cmd_pull,
        "cat": cmd_cat,
        "multi": cmd_multi,
        "health": cmd_health,
        "run": cmd_run,
        "output": cmd_output,
        "sync": cmd_sync,
        "status": cmd_status,
        "list": cmd_list,
        "log": cmd_log,
        "disconnect": cmd_disconnect,
    }

    handler = commands.get(args.subcmd)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        handler(args)
    except FileNotFoundError as exc:
        err(str(exc))
        sys.exit(1)
    except KeyError as exc:
        err(str(exc))
        sys.exit(1)
    except RuntimeError as exc:
        err(str(exc))
        sys.exit(1)
    except ValueError as exc:
        err(str(exc))
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        # Friendly SSH/rsync error messages
        cmd_name = Path(exc.cmd[0]).name if exc.cmd else "command"
        if cmd_name == "ssh":
            err(f"SSH connection failed (exit {exc.returncode}). Is the dale reachable?")
            if exc.stderr:
                err(exc.stderr.strip())
        elif cmd_name == "rsync":
            err(f"Rsync failed (exit {exc.returncode}).")
            if exc.stderr:
                err(exc.stderr.strip())
        else:
            err(f"{cmd_name} failed (exit {exc.returncode})")
        sys.exit(exc.returncode)
    except KeyboardInterrupt:
        sys.exit(130)
