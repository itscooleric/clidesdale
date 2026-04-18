"""Docker container diagnostics with blacklist, ACL, and tiered permissions.

Provides safe, audited Docker access through sdale. Commands are tiered:

  Green  (auto-allow): ps, logs, inspect, stats — read-only diagnostics
  Yellow (--confirm):  restart — guarded mutation
  Red    (blocked):    exec, rm, stop on blacklisted containers

Per-dale configuration in sdale.json:
  "docker_user":        SSH user with Docker socket access
  "docker_blacklist":   Container names that cannot be restarted
  "allowed_operators":  Operators permitted to use docker commands (empty = all)
  "operator_tiers":     Permission tiers: {"full": [...], "readonly": [...]}
  "mode":               Operating mode: unrestricted/supervised/locked
"""

import argparse
import sys

from .config import DaleConfig
from .logger import EventLogger, detect_operator
from .remote import ssh

import subprocess


# ── Access control ──────────────────────────────────────────────────


def _get_operator_tier(dale: DaleConfig, operator: str) -> str:
    """Determine the operator's permission tier.

    Returns:
        "full" — green + yellow commands allowed
        "readonly" — green commands only
        "none" — no docker access
    """
    tiers = dale.operator_tiers
    if not tiers:
        # No tiers configured — fall back to allowed_operators behavior
        if not dale.allowed_operators:
            return "full"  # empty = all operators, full access
        if operator in dale.allowed_operators:
            return "full"
        return "none"

    if operator in tiers.get("full", []):
        return "full"
    if operator in tiers.get("readonly", []):
        return "readonly"
    return "none"


def _check_operator_acl(dale: DaleConfig, require_tier: str = "readonly") -> str:
    """Check operator ACL and return the operator's tier.

    Args:
        dale: Dale configuration.
        require_tier: Minimum tier required ("readonly" or "full").

    Returns:
        The operator's tier string.

    Raises:
        SystemExit: If the operator lacks the required tier.
    """
    operator = detect_operator()
    tier = _get_operator_tier(dale, operator)

    if tier == "none":
        print(
            f"sdale: operator '{operator}' has no docker access "
            f"on dale '{dale.name}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    if require_tier == "full" and tier == "readonly":
        print(
            f"sdale: operator '{operator}' has readonly docker access "
            f"on dale '{dale.name}'. This command requires full tier.",
            file=sys.stderr,
        )
        sys.exit(1)

    return tier


def _check_blacklist(dale: DaleConfig, container: str, action: str) -> None:
    """Block actions on blacklisted containers.

    Raises:
        SystemExit: If the container is blacklisted.
    """
    if container in dale.docker_blacklist:
        print(
            f"sdale: container '{container}' is blacklisted on dale '{dale.name}'. "
            f"Cannot {action}.",
            file=sys.stderr,
        )
        sys.exit(1)


def _resolve_mode(dale: DaleConfig) -> str:
    """Resolve the current mode from state file or config.

    Priority: state file > config > default (no restriction).
    """
    from pathlib import Path
    mode_file = Path.home() / ".config" / "sdale" / "modes" / f"{dale.name}.mode"
    if mode_file.exists():
        stored = mode_file.read_text().strip()
        if stored in ("unrestricted", "supervised", "locked"):
            return stored
    return dale.mode or ""


def _check_mode(dale: DaleConfig, command_type: str) -> None:
    """Enforce mode restrictions on docker commands.

    Args:
        dale: Dale configuration.
        command_type: "read" for green-tier, "mutate" for yellow-tier.

    Raises:
        SystemExit: If mode blocks the command.
    """
    mode = _resolve_mode(dale)
    if not mode or mode == "unrestricted":
        return

    if mode == "locked":
        print(
            f"sdale: dale '{dale.name}' is in locked mode. "
            f"No docker commands allowed.",
            file=sys.stderr,
        )
        sys.exit(1)

    if mode == "supervised" and command_type == "mutate":
        print(
            f"sdale: dale '{dale.name}' is in supervised mode. "
            f"Mutations require escalation to unrestricted mode first.\n"
            f"  Use: sdale mode {dale.name} unrestricted",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Green-tier commands (read-only) ────────────────────────────────


def cmd_docker_ps(args: argparse.Namespace, dale: DaleConfig) -> None:
    """List containers on the dale."""
    logger = EventLogger(dale.name)
    _check_mode(dale, "read")
    _check_operator_acl(dale, require_tier="readonly")

    docker_dale = dale.for_docker()
    cmd = "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'"
    if args.all:
        cmd = "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'"

    try:
        result = ssh(docker_dale, cmd + " 2>&1", capture=True)
        if result.stdout:
            print(result.stdout, end="")
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        if exc.stderr:
            print(exc.stderr, end="", file=sys.stderr)
        sys.exit(exc.returncode)

    logger.log("docker_ps", all=str(getattr(args, "all", False)))


def cmd_docker_logs(args: argparse.Namespace, dale: DaleConfig) -> None:
    """View container logs on the dale."""
    logger = EventLogger(dale.name)
    _check_mode(dale, "read")
    _check_operator_acl(dale, require_tier="readonly")

    container = args.container
    docker_dale = dale.for_docker()

    docker_args = ["docker", "logs"]
    docker_args.extend(["--tail", str(args.tail)])
    if args.since:
        docker_args.extend(["--since", args.since])
    if args.follow:
        docker_args.append("--follow")
    docker_args.append(container)

    cmd_str = " ".join(docker_args) + " 2>&1"

    if args.follow:
        try:
            ssh(docker_dale, cmd_str, capture=False)
        except (subprocess.CalledProcessError, KeyboardInterrupt):
            pass
    else:
        try:
            result = ssh(docker_dale, cmd_str, capture=True)
            if result.stdout:
                print(result.stdout, end="")
        except subprocess.CalledProcessError as exc:
            if exc.stdout:
                print(exc.stdout, end="")
            if exc.stderr:
                print(exc.stderr, end="", file=sys.stderr)
            sys.exit(exc.returncode)

    logger.log("docker_logs", container=container,
               tail=str(args.tail), follow=str(args.follow))


def cmd_docker_inspect(args: argparse.Namespace, dale: DaleConfig) -> None:
    """Inspect a container on the dale."""
    logger = EventLogger(dale.name)
    _check_mode(dale, "read")
    _check_operator_acl(dale, require_tier="readonly")

    container = args.container
    docker_dale = dale.for_docker()

    try:
        result = ssh(docker_dale, f"docker inspect {container} 2>&1", capture=True)
        if result.stdout:
            print(result.stdout, end="")
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        if exc.stderr:
            print(exc.stderr, end="", file=sys.stderr)
        sys.exit(exc.returncode)

    logger.log("docker_inspect", container=container)


def cmd_docker_stats(args: argparse.Namespace, dale: DaleConfig) -> None:
    """Show container resource usage on the dale."""
    logger = EventLogger(dale.name)
    _check_mode(dale, "read")
    _check_operator_acl(dale, require_tier="readonly")

    docker_dale = dale.for_docker()
    cmd = "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}' 2>&1"

    try:
        result = ssh(docker_dale, cmd, capture=True)
        if result.stdout:
            print(result.stdout, end="")
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        if exc.stderr:
            print(exc.stderr, end="", file=sys.stderr)
        sys.exit(exc.returncode)

    logger.log("docker_stats")


# ── Yellow-tier commands (guarded mutations) ───────────────────────


def cmd_docker_restart(args: argparse.Namespace, dale: DaleConfig) -> None:
    """Restart a container on the dale (requires --confirm + full tier)."""
    logger = EventLogger(dale.name)
    _check_mode(dale, "mutate")
    _check_operator_acl(dale, require_tier="full")

    container = args.container
    _check_blacklist(dale, container, "restart")

    if not args.confirm:
        print(
            f"sdale: restart is a yellow-tier mutation. "
            f"Re-run with --confirm to restart '{container}' on '{dale.name}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    docker_dale = dale.for_docker()

    print(f"\U0001F40E Restarting {container} on {dale.name}...")
    try:
        result = ssh(docker_dale, f"docker restart {container} 2>&1", capture=True)
        if result.stdout:
            print(result.stdout, end="")
        print(f"\U0001F40E {container} restarted.")
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        if exc.stderr:
            print(exc.stderr, end="", file=sys.stderr)
        sys.exit(exc.returncode)

    logger.log("docker_restart", container=container, confirmed="true")


# ── Argument parser ────────────────────────────────────────────────


def add_docker_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the 'docker' subcommand group with the main CLI parser."""
    docker_parser = subparsers.add_parser(
        "docker",
        help="Container diagnostics with blacklist + ACL",
        description=(
            "Safe Docker container access. Green-tier reads (ps/logs/inspect/stats) "
            "run freely. Yellow-tier mutations (restart) require --confirm. "
            "Blacklisted containers are protected from mutations."
        ),
    )
    docker_sub = docker_parser.add_subparsers(dest="docker_cmd", help="Docker commands")

    # ps
    p = docker_sub.add_parser("ps", help="List containers")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("--all", "-a", action="store_true", help="Show all containers (including stopped)")

    # logs
    p = docker_sub.add_parser("logs", help="View container logs")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("container", help="Container name")
    p.add_argument("--tail", "-n", type=int, default=50, help="Number of lines (default: 50)")
    p.add_argument("--since", metavar="DUR", help="Show logs since duration (e.g. 1h, 30m)")
    p.add_argument("--follow", "-f", action="store_true", help="Follow log output (Ctrl-C to stop)")

    # inspect
    p = docker_sub.add_parser("inspect", help="Inspect a container")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("container", help="Container name")

    # stats
    p = docker_sub.add_parser("stats", help="Container resource usage")
    p.add_argument("dale", help="Dale name from sdale.json")

    # restart (yellow-tier)
    p = docker_sub.add_parser("restart", help="Restart a container (yellow-tier, requires --confirm + full tier)")
    p.add_argument("dale", help="Dale name from sdale.json")
    p.add_argument("container", help="Container name")
    p.add_argument("--confirm", action="store_true", help="Confirm restart (required)")
