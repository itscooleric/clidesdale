"""Command-line interface for sdale.

This is the main entry point. Uses argparse (stdlib) for command parsing.
Each subcommand maps to a function that orchestrates config loading,
remote execution, and event logging.

Usage:
    sdale connect <dale>
    sdale watch <dale>
    sdale exec <dale> "<command>"
    sdale push <dale> <local-file> <remote-path>
    sdale write <dale> /remote/path          # reads stdin
    sdale write <dale> /remote/path --from /local/file
    sdale logs <dale> <container> [--tail N] [--since DUR] [--follow]
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
    script_path = f"{str(Path(dale.activity_log_path).parent)}/.sdale-watch"
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
    log_file = dale.activity_log_path
    try:
        ssh(dale, f"mkdir -p $(dirname '{log_file}') 2>/dev/null; touch '{log_file}' && echo '── sdale connected ({dale.name}) ──' >> '{log_file}'", capture=True)
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
    log_file = dale.activity_log_path

    info(f"Watching dale '{dale.name}' — Ctrl-c to stop")
    print()

    cmd = ["ssh", *dale.ssh_args, "-t", dale.ssh_dest,
           f"touch '{log_file}' && tail -f '{log_file}'"]
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


def cmd_write(args: argparse.Namespace) -> None:
    """Write content to a remote file, bypassing shell quoting issues.

    Reads content from stdin (pipe) or a local file (``--from``), writes
    it to a local temp file, scp's it to the dale, then atomically moves
    it into place. This avoids all heredoc/quoting problems that plague
    ``sdale exec`` for file writes.

    Examples::

        echo 'VPS_NAME=edge' | sdale write edge /opt/stacks/vps-caddy/.env
        sdale write edge /opt/stacks/app/config.yml --from ./config.yml
        cat Caddyfile | sdale write core /opt/stacks/vps-caddy/Caddyfile
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    remote_path = args.path

    # Read content from --from file or stdin.
    if args.from_file:
        local_path = args.from_file
        if not Path(local_path).exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")
        with open(local_path, "rb") as fh:
            content = fh.read()
    else:
        if sys.stdin.isatty():
            err("No input. Pipe content or use --from /local/file")
            sys.exit(1)
        content = sys.stdin.buffer.read()

    if not content:
        err("Empty input — nothing to write")
        sys.exit(1)

    # Write to local temp file, scp to dale, mv into place
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".sdale-write") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        remote_tmp = f"/tmp/.sdale-write-{os.getpid()}"
        scp_to(dale, tmp_path, remote_tmp)
        # Atomic move — ensures the file appears complete, not partially written.
        # mkdir -p the parent in case it doesn't exist yet.
        parent_dir = str(Path(remote_path).parent)
        ssh(dale, f"mkdir -p '{parent_dir}' && mv -f '{remote_tmp}' '{remote_path}'",
            capture=True)
    finally:
        os.unlink(tmp_path)

    size = len(content)
    logger.log("dale_write", path=remote_path, bytes=str(size))
    info(f"Wrote {size} bytes → {dale.name}:{remote_path}")


def cmd_script(args: argparse.Namespace) -> None:
    """Upload a local script to the dale and run it.

    Copies the script to a temp file on the dale, makes it executable,
    runs it with the provided arguments, then cleans up. Output streams
    directly to stdout/stderr.

    Examples::

        sdale script edge ./setup.sh
        sdale script mesa ./deploy.py --env prod
        sdale script core ./check.sh arg1 arg2
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    local_script = args.script
    script_args = args.script_args or []

    if not Path(local_script).is_file():
        err(f"Script not found: {local_script}")
        sys.exit(1)

    # Determine interpreter from shebang or extension
    ext = Path(local_script).suffix
    remote_tmp = f"/tmp/.sdale-script-{os.getpid()}{ext or '.sh'}"

    # Upload
    scp_to(dale, local_script, remote_tmp)

    # Make executable and run
    args_str = " ".join(f"'{a}'" for a in script_args)
    run_cmd = f"chmod +x '{remote_tmp}' && '{remote_tmp}' {args_str}; _rc=$?; rm -f '{remote_tmp}'; exit $_rc"

    try:
        result = ssh(dale, run_cmd, capture=True)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        logger.log("dale_script", script=local_script, exit_code="0")
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        if exc.stderr:
            print(exc.stderr, end="", file=sys.stderr)
        logger.log("dale_script", script=local_script, exit_code=str(exc.returncode))
        sys.exit(exc.returncode)


def cmd_logs(args: argparse.Namespace) -> None:
    """View container logs on a dale.

    Wraps ``docker logs`` with proper stderr merging and sensible
    defaults. Supports tail count, time-based filtering, and live
    follow mode.

    Examples::

        sdale logs edge cloperator
        sdale logs edge clem --tail 100
        sdale logs core homeassistant --since 1h
        sdale logs edge clide-web-1 --follow
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    container = args.container

    docker_args = ["docker", "logs"]
    docker_args.extend(["--tail", str(args.tail)])
    if args.since:
        docker_args.extend(["--since", args.since])
    if args.follow:
        docker_args.append("--follow")
    docker_args.append(container)

    cmd_str = " ".join(docker_args) + " 2>&1"

    if args.follow:
        # For --follow, don't capture — stream directly to terminal.
        # Use os.execvp-style via subprocess with inherited I/O.
        try:
            ssh(dale, cmd_str, capture=False)
        except subprocess.CalledProcessError:
            pass  # Ctrl-C or container stopped
        except KeyboardInterrupt:
            pass
    else:
        try:
            result = ssh(dale, cmd_str, capture=True)
            if result.stdout:
                print(result.stdout, end="")
        except subprocess.CalledProcessError as exc:
            if exc.stdout:
                print(exc.stdout, end="")
            if exc.stderr:
                err(exc.stderr.strip())

    logger.log("dale_logs", container=container, follow=str(args.follow))


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


def cmd_info(args: argparse.Namespace) -> None:
    """Gather structured system information from a dale.

    Collects hostname, OS, uptime, CPU, memory, disk, network interfaces,
    and optionally Docker info and installed tools in a single SSH
    round-trip. Replaces common ad-hoc ``sdale exec`` chains for system
    inspection.

    Examples::

        sdale info edge                    # system overview
        sdale info edge --docker           # include Docker info
        sdale info edge --tools            # check installed CLIs
        sdale info edge --all              # everything
        sdale info edge --json             # machine-readable output
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)

    show_docker = args.docker or args.all
    show_tools = args.tools or args.all
    show_net = args.net or args.all

    parts = [
        "echo HOSTNAME=$(hostname)",
        "echo KERNEL=$(uname -r)",
        "echo OS=$(. /etc/os-release 2>/dev/null && echo \"$PRETTY_NAME\" || uname -s)",
        "echo ARCH=$(uname -m)",
        "echo UPTIME=$(uptime -p 2>/dev/null || uptime | sed 's/.*up /up /' | sed 's/,.*load.*//')",
        "echo LOAD=$(cat /proc/loadavg 2>/dev/null | awk '{print $1, $2, $3}')",
        "echo CPUS=$(nproc 2>/dev/null || echo '?')",
        "echo CPU_MODEL=$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs || echo '?')",
        "echo MEM_TOTAL=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}')",
        "echo MEM_USED=$(free -m 2>/dev/null | awk '/^Mem:/{print $3}')",
        "echo MEM_AVAIL=$(free -m 2>/dev/null | awk '/^Mem:/{print $7}')",
        "echo SWAP_TOTAL=$(free -m 2>/dev/null | awk '/^Swap:/{print $2}')",
        "echo SWAP_USED=$(free -m 2>/dev/null | awk '/^Swap:/{print $3}')",
        "echo DISK_INFO_START",
        "df -h --output=target,size,used,avail,pcent -x tmpfs -x devtmpfs -x overlay 2>/dev/null || df -h / 2>/dev/null",
        "echo DISK_INFO_END",
        "echo TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo 'n/a')",
        "echo TAILSCALE_STATUS=$(tailscale status --self --json 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get(\"Self\",{}).get(\"Online\",\"?\"))' 2>/dev/null || echo 'n/a')",
    ]

    if show_net:
        parts.extend([
            "echo NET_INFO_START",
            "ip -4 addr show 2>/dev/null | grep -E 'inet |^[0-9]' | head -20",
            "echo NET_INFO_END",
        ])

    if show_docker:
        parts.extend([
            "echo DOCKER_VER=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',' || echo 'not installed')",
            "echo DOCKER_CONTAINERS_START",
            "docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null | head -30",
            "echo DOCKER_CONTAINERS_END",
            "echo DOCKER_IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | wc -l)",
        ])

    if show_tools:
        parts.extend([
            "echo TOOLS_START",
            "for t in claude codex copilot gh glab node python3 pip docker tmux; do "
            "  v=$($t --version 2>/dev/null | head -1) || v='not found'; "
            "  echo \"$t=$v\"; "
            "done",
            "echo TOOLS_END",
        ])

    combined = "; ".join(parts)

    try:
        result = ssh(dale, combined, capture=True)
    except subprocess.CalledProcessError as exc:
        result = subprocess.CompletedProcess([], 0, stdout=exc.stdout or "")

    output = result.stdout or ""

    kv = {}
    disk_lines = []
    docker_lines = []
    net_lines = []
    tool_lines = []
    section = None

    for line in output.splitlines():
        if line == "DISK_INFO_START":
            section = "disk"
            continue
        elif line == "DISK_INFO_END":
            section = None
            continue
        elif line == "DOCKER_CONTAINERS_START":
            section = "docker"
            continue
        elif line == "DOCKER_CONTAINERS_END":
            section = None
            continue
        elif line == "NET_INFO_START":
            section = "net"
            continue
        elif line == "NET_INFO_END":
            section = None
            continue
        elif line == "TOOLS_START":
            section = "tools"
            continue
        elif line == "TOOLS_END":
            section = None
            continue

        if section == "disk" and line.strip():
            disk_lines.append(line)
        elif section == "docker" and line.strip():
            docker_lines.append(line)
        elif section == "net" and line.strip():
            net_lines.append(line)
        elif section == "tools" and line.strip():
            tool_lines.append(line)
        elif "=" in line and section is None:
            key, _, val = line.partition("=")
            kv[key.strip()] = val.strip()

    if args.json:
        import json as _json
        data = {
            "dale": dale.name,
            "hostname": kv.get("HOSTNAME", "?"),
            "os": kv.get("OS", "?"),
            "kernel": kv.get("KERNEL", "?"),
            "arch": kv.get("ARCH", "?"),
            "uptime": kv.get("UPTIME", "?"),
            "load": kv.get("LOAD", "?"),
            "cpus": kv.get("CPUS", "?"),
            "cpu_model": kv.get("CPU_MODEL", "?"),
            "mem_total_mb": kv.get("MEM_TOTAL", "?"),
            "mem_used_mb": kv.get("MEM_USED", "?"),
            "mem_avail_mb": kv.get("MEM_AVAIL", "?"),
            "swap_total_mb": kv.get("SWAP_TOTAL", "?"),
            "swap_used_mb": kv.get("SWAP_USED", "?"),
            "tailscale_ip": kv.get("TAILSCALE_IP", "?"),
        }
        if show_docker:
            data["docker_version"] = kv.get("DOCKER_VER", "?")
            data["docker_images"] = kv.get("DOCKER_IMAGES", "?")
            data["containers"] = [
                dict(zip(("name", "status", "image"), l.split("\t")))
                for l in docker_lines if "\t" in l
            ]
        if show_tools:
            data["tools"] = {
                l.split("=", 1)[0]: l.split("=", 1)[1]
                for l in tool_lines if "=" in l
            }
        print(_json.dumps(data, indent=2))
    else:
        print(f"  {dale.name} ({kv.get('HOSTNAME', '?')})")
        print(f"  {'─' * 40}")
        print(f"  OS:        {kv.get('OS', '?')} ({kv.get('ARCH', '?')})")
        print(f"  Kernel:    {kv.get('KERNEL', '?')}")
        print(f"  Uptime:    {kv.get('UPTIME', '?')}")
        print(f"  Load:      {kv.get('LOAD', '?')}")
        print(f"  CPUs:      {kv.get('CPUS', '?')} ({kv.get('CPU_MODEL', '?')})")

        mem_total = kv.get("MEM_TOTAL", "?")
        mem_used = kv.get("MEM_USED", "?")
        mem_avail = kv.get("MEM_AVAIL", "?")
        swap_total = kv.get("SWAP_TOTAL", "0")
        swap_used = kv.get("SWAP_USED", "0")
        print(f"  Memory:    {mem_used}MB / {mem_total}MB ({mem_avail}MB available)")
        if swap_total != "0":
            print(f"  Swap:      {swap_used}MB / {swap_total}MB")

        ts_ip = kv.get("TAILSCALE_IP", "n/a")
        ts_status = kv.get("TAILSCALE_STATUS", "n/a")
        if ts_ip != "n/a":
            print(f"  Tailscale: {ts_ip} (online: {ts_status})")

        if disk_lines:
            print()
            print("  Disk:")
            for dl in disk_lines:
                print(f"    {dl}")

        if show_net and net_lines:
            print()
            print("  Network:")
            for nl in net_lines:
                print(f"    {nl}")

        if show_docker:
            docker_ver = kv.get("DOCKER_VER", "?")
            docker_images = kv.get("DOCKER_IMAGES", "?")
            print()
            print(f"  Docker:    {docker_ver} ({docker_images} images)")
            if docker_lines:
                print(f"  Containers ({len(docker_lines)}):")
                for dl in docker_lines:
                    parts_line = dl.split("\t")
                    if len(parts_line) == 3:
                        print(f"    {parts_line[0]:24s} {parts_line[1]}")
                    else:
                        print(f"    {dl}")

        if show_tools and tool_lines:
            print()
            print("  Tools:")
            for tl in tool_lines:
                if "=" in tl:
                    name, _, ver = tl.partition("=")
                    status = "\u2705" if "not found" not in ver else "\u274C"
                    print(f"    {status} {name:12s} {ver}")

    logger.log("dale_info", hostname=kv.get("HOSTNAME", "?"),
               sections=",".join(
                   s for s, v in [("docker", show_docker), ("tools", show_tools),
                                  ("net", show_net)] if v
               ))


def cmd_probe(args: argparse.Namespace) -> None:
    """Network and DNS diagnostics for a dale.

    Default (no flags) shows DNS config, IP addresses, routes, default
    gateway reachability, and Tailscale status. Optional flags probe
    specific hostnames, targets, or ports.

    Examples::

        sdale probe edge                          # overview
        sdale probe edge --dns git.lan.wubi.sh    # resolve hostname
        sdale probe edge --ping 8.8.8.8           # connectivity test
        sdale probe edge --reach hub.edge.wubi.sh # HTTP reachability
        sdale probe edge --ports 80,443,7681      # check listening ports
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)

    dns_hosts = args.dns or []
    ping_targets = args.ping or []
    reach_targets = args.reach or []
    port_list = args.ports or ""

    # ── Default overview (always runs) ────────────────────────────
    parts = [
        # DNS config
        "echo DNS_START",
        "cat /etc/resolv.conf 2>/dev/null | grep -v '^#' | grep -v '^$'",
        "echo DNS_END",
        # IP addresses (non-docker, non-veth)
        "echo IP_START",
        "ip -4 addr show 2>/dev/null | grep -E 'inet ' | grep -v 'docker\\|br-\\|veth' | awk '{print $NF, $2}'",
        "echo IP_END",
        # Default route
        "echo ROUTE=$(ip route show default 2>/dev/null | head -1)",
        # Gateway ping
        "GW=$(ip route show default 2>/dev/null | awk '{print $3}' | head -1)",
        "echo GW=$GW",
        "if [ -n \"$GW\" ]; then "
        "  ping -c 1 -W 2 $GW >/dev/null 2>&1 && echo GW_PING=ok || echo GW_PING=fail; "
        "else echo GW_PING=no_gateway; fi",
        # Tailscale
        "echo TS_IP=$(tailscale ip -4 2>/dev/null || echo n/a)",
        "echo TS_STATUS=$(tailscale status --self --json 2>/dev/null "
        "| python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get(\"Self\",{}).get(\"Online\",\"?\"))' "
        "2>/dev/null || echo n/a)",
        # Public IP
        "echo PUB_IP=$(curl -4 -s --max-time 3 ifconfig.me 2>/dev/null || echo n/a)",
        # Internet connectivity
        "curl -s --max-time 3 -o /dev/null -w '%{http_code}' https://api.github.com/ 2>/dev/null "
        "| xargs -I{} echo INET_CHECK={}",
    ]

    # ── DNS resolution ────────────────────────────────────────────
    for host in dns_hosts:
        safe = host.replace("'", "")
        parts.append(f"echo DNS_LOOKUP_START={safe}")
        parts.append(f"getent hosts '{safe}' 2>&1 || echo 'NXDOMAIN'")
        parts.append(
            f"dig +short '{safe}' 2>/dev/null | head -5 || "
            f"nslookup '{safe}' 2>/dev/null | grep -A2 'Name:' | tail -3 || "
            f"echo 'no dig/nslookup available'"
        )
        parts.append("echo DNS_LOOKUP_END")

    # ── Ping targets ──────────────────────────────────────────────
    for target in ping_targets:
        safe = target.replace("'", "")
        parts.append(f"echo PING_START={safe}")
        parts.append(f"ping -c 3 -W 2 '{safe}' 2>&1 | tail -2")
        parts.append("echo PING_END")

    # ── HTTP reachability ─────────────────────────────────────────
    for target in reach_targets:
        safe = target.replace("'", "")
        # Add https:// if no scheme
        url = safe if "://" in safe else f"https://{safe}"
        parts.append(f"echo REACH_START={safe}")
        parts.append(
            f"curl -sk --max-time 5 -o /dev/null "
            f"-w 'status=%{{http_code}} time=%{{time_total}}s ip=%{{remote_ip}}\n' "
            f"'{url}' 2>&1"
        )
        parts.append("echo REACH_END")

    # ── Port check ────────────────────────────────────────────────
    if port_list:
        parts.append("echo PORTS_START")
        parts.append("ss -tlnp 2>/dev/null | head -1")
        for port in port_list.split(","):
            port = port.strip()
            if port:
                parts.append(
                    f"ss -tlnp 2>/dev/null | grep ':{port} ' || "
                    f"echo '  (nothing listening on :{port})'"
                )
        parts.append("echo PORTS_END")

    combined = "; ".join(parts)

    try:
        result = ssh(dale, combined, capture=True)
    except subprocess.CalledProcessError as exc:
        result = subprocess.CompletedProcess([], 0, stdout=exc.stdout or "")

    output = result.stdout or ""

    # ── Parse output ──────────────────────────────────────────────
    kv = {}
    dns_lines = []
    ip_lines = []
    section = None
    dns_lookup_host = ""
    dns_lookups = {}   # host -> lines
    ping_host = ""
    pings = {}         # host -> lines
    reach_host = ""
    reaches = {}       # host -> line
    port_lines = []

    for line in output.splitlines():
        # Section markers
        if line == "DNS_START":
            section = "dns"
            continue
        elif line == "DNS_END":
            section = None
            continue
        elif line == "IP_START":
            section = "ip"
            continue
        elif line == "IP_END":
            section = None
            continue
        elif line.startswith("DNS_LOOKUP_START="):
            dns_lookup_host = line.split("=", 1)[1]
            dns_lookups[dns_lookup_host] = []
            section = "dns_lookup"
            continue
        elif line == "DNS_LOOKUP_END":
            section = None
            continue
        elif line.startswith("PING_START="):
            ping_host = line.split("=", 1)[1]
            pings[ping_host] = []
            section = "ping"
            continue
        elif line == "PING_END":
            section = None
            continue
        elif line.startswith("REACH_START="):
            reach_host = line.split("=", 1)[1]
            reaches[reach_host] = []
            section = "reach"
            continue
        elif line == "REACH_END":
            section = None
            continue
        elif line == "PORTS_START":
            section = "ports"
            continue
        elif line == "PORTS_END":
            section = None
            continue

        if section == "dns" and line.strip():
            dns_lines.append(line.strip())
        elif section == "ip" and line.strip():
            ip_lines.append(line.strip())
        elif section == "dns_lookup" and line.strip():
            dns_lookups[dns_lookup_host].append(line.strip())
        elif section == "ping" and line.strip():
            pings[ping_host].append(line.strip())
        elif section == "reach" and line.strip():
            reaches[reach_host].append(line.strip())
        elif section == "ports" and line.strip():
            port_lines.append(line.strip())
        elif "=" in line and section is None:
            key, _, val = line.partition("=")
            kv[key.strip()] = val.strip()

    # ── Print ─────────────────────────────────────────────────────
    print(f"  {dale.name} — network probe")
    print(f"  {'─' * 40}")

    # IPs
    if ip_lines:
        print("  Interfaces:")
        for ipl in ip_lines:
            print(f"    {ipl}")

    ts_ip = kv.get("TS_IP", "n/a")
    ts_status = kv.get("TS_STATUS", "n/a")
    if ts_ip != "n/a":
        print(f"  Tailscale:  {ts_ip} (online: {ts_status})")

    pub_ip = kv.get("PUB_IP", "n/a")
    if pub_ip != "n/a":
        print(f"  Public IP:  {pub_ip}")

    # Gateway
    gw = kv.get("GW", "?")
    gw_ping = kv.get("GW_PING", "?")
    gw_icon = "\u2705" if gw_ping == "ok" else "\u274C"
    print(f"  Gateway:    {gw} {gw_icon}")
    print(f"  Route:      {kv.get('ROUTE', '?')}")

    # Internet
    inet = kv.get("INET_CHECK", "?")
    inet_icon = "\u2705" if inet == "200" else "\u274C"
    print(f"  Internet:   {inet_icon} (github API: {inet})")

    # DNS config
    if dns_lines:
        print()
        print("  DNS config:")
        for dl in dns_lines:
            print(f"    {dl}")

    # DNS lookups
    if dns_lookups:
        print()
        print("  DNS lookups:")
        for host, lines in dns_lookups.items():
            resolved = lines[0] if lines else "?"
            icon = "\u274C" if "NXDOMAIN" in resolved else "\u2705"
            print(f"    {icon} {host}")
            for ll in lines:
                print(f"      {ll}")

    # Pings
    if pings:
        print()
        print("  Ping:")
        for target, lines in pings.items():
            summary = lines[-1] if lines else "?"
            icon = "\u2705" if "0% packet loss" in summary else "\u274C"
            print(f"    {icon} {target}")
            for ll in lines:
                print(f"      {ll}")

    # Reachability
    if reaches:
        print()
        print("  HTTP reachability:")
        for target, lines in reaches.items():
            detail = lines[0] if lines else "?"
            icon = "\u2705" if "status=200" in detail or "status=30" in detail else "\u274C"
            print(f"    {icon} {target}  {detail}")

    # Ports
    if port_lines:
        print()
        print("  Listening ports:")
        for pl in port_lines:
            print(f"    {pl}")

    probed = ",".join(dns_hosts + ping_targets + reach_targets)
    logger.log("dale_probe", targets=probed or "overview",
               gateway=gw, gw_ping=gw_ping, internet=inet)


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
    log_file = dale.activity_log_path
    safe = command.replace("'", "'\\''")[:200]
    try:
        ssh(dale, f"echo '\\n── '$(date +\"%H:%M:%S\")' ── [run] $ {safe}' >> '{log_file}'", capture=True)
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

    # write
    p = sub.add_parser("write", help="Write content to a remote file (stdin or --from)")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("path", help="Remote file path to write")
    p.add_argument("--from", dest="from_file", metavar="FILE",
                    help="Read from local file instead of stdin")

    # script
    p = sub.add_parser("script", help="Upload and run a local script on a dale")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("script", help="Local script file to upload and run")
    p.add_argument("script_args", nargs="*", help="Arguments to pass to the script")

    # logs
    p = sub.add_parser("logs", help="View container logs on a dale")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("container", help="Container name")
    p.add_argument("--tail", "-n", type=int, default=50, help="Number of lines (default: 50)")
    p.add_argument("--since", metavar="DUR", help="Show logs since duration (e.g. 1h, 30m)")
    p.add_argument("--follow", "-f", action="store_true", help="Follow log output (Ctrl-C to stop)")

    # multi
    p = sub.add_parser("multi", help="Run multiple commands in one SSH round-trip")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("commands", nargs="+", help="Commands to run (each quoted separately)")

    # info
    p = sub.add_parser("info", help="Structured system info from a dale")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("--docker", "-d", action="store_true", help="Include Docker containers and images")
    p.add_argument("--tools", "-t", action="store_true", help="Check installed CLI tools")
    p.add_argument("--net", "-n", action="store_true", help="Show network interfaces")
    p.add_argument("--all", "-a", action="store_true", help="Show everything")
    p.add_argument("--json", "-j", action="store_true", help="Output as JSON")

    # probe
    p = sub.add_parser("probe", help="Network and DNS diagnostics")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("--dns", nargs="+", metavar="HOST", help="Resolve hostname(s)")
    p.add_argument("--ping", nargs="+", metavar="TARGET", help="Ping target(s)")
    p.add_argument("--reach", nargs="+", metavar="URL", help="HTTP reachability check")
    p.add_argument("--ports", metavar="LIST", help="Check listening ports (comma-separated)")

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
        "write": cmd_write,
        "script": cmd_script,
        "logs": cmd_logs,
        "multi": cmd_multi,
        "info": cmd_info,
        "probe": cmd_probe,
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
