"""Command-line interface for sdale.

This is the main entry point. Uses argparse (stdlib) for command parsing.
Each subcommand maps to a function that orchestrates config loading,
remote execution, and event logging.

Usage:
    sdale connect <dale>
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
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .config import DaleConfig, get_dale, list_dales, find_config_path
from .logger import EventLogger
from .remote import (
    rsync,
    tmux_capture,
    tmux_ensure,
    tmux_has_session,
    tmux_kill,
    tmux_send,
)


# ── Output helpers ───────────────────────────────────────────────────


def info(msg: str) -> None:
    """Print an informational message with the sdale horse emoji prefix."""
    print(f"\U0001F40E {msg}")


def err(msg: str) -> None:
    """Print an error message to stderr."""
    print(f"sdale: {msg}", file=sys.stderr)


# ── Subcommands ──────────────────────────────────────────────────────


def cmd_connect(args: argparse.Namespace) -> None:
    """Create or reuse a tmux session on a dale.

    If the tmux session already exists, this is a no-op.
    Prints the attach command so the human knows how to watch.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)

    info(f"Connecting to dale '{dale.name}' ({dale.ssh_dest})...")
    tmux_ensure(dale)

    logger.log("dale_connect", tmux_session=dale.session, host=dale.host)
    info(f"tmux session '{dale.session}' ready")
    info(f"Attach with: ssh {dale.ssh_dest} -t \"tmux attach -t {dale.session}\"")


def cmd_run(args: argparse.Namespace) -> None:
    """Send a command to the dale's tmux session.

    The command is sent as keystrokes, so it appears in the tmux
    session exactly as if someone typed it. The human watching
    the session sees everything in real time.
    """
    dale = get_dale(args.dale)
    logger = EventLogger(dale.name)
    command = args.command

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

    # run
    p = sub.add_parser("run", help="Send a command to the dale's tmux session")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("command", help="Command to run (quote it)")

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
