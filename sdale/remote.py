"""Remote execution helpers for sdale.

Wraps SSH and rsync commands for interacting with dales.
All remote commands go through tmux sessions for co-dev observability.
"""

import subprocess
import sys
from typing import Optional

from .config import DaleConfig


def ssh(dale: DaleConfig, command: str, capture: bool = False) -> subprocess.CompletedProcess:
    """Run a command on the dale via SSH.

    Args:
        dale:    The dale configuration.
        command: The remote command string to execute.
        capture: If True, capture stdout/stderr. If False, inherit terminal.

    Returns:
        The CompletedProcess result.

    Raises:
        subprocess.CalledProcessError: If the SSH command fails.
    """
    cmd = ["ssh", *dale.ssh_args, dale.ssh_dest, command]
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=True,
    )


def tmux_ensure(dale: DaleConfig) -> None:
    """Ensure a tmux session exists on the dale, creating it if needed.

    Args:
        dale: The dale configuration (uses dale.session for the session name).
    """
    ssh(
        dale,
        f"tmux new-session -d -s '{dale.session}' 'bash' 2>/dev/null || true",
        capture=True,
    )


def tmux_has_session(dale: DaleConfig) -> bool:
    """Check if a tmux session exists on the dale.

    Args:
        dale: The dale configuration.

    Returns:
        True if the session exists, False otherwise.
    """
    try:
        ssh(dale, f"tmux has-session -t '{dale.session}' 2>/dev/null", capture=True)
        return True
    except subprocess.CalledProcessError:
        return False


def tmux_send(dale: DaleConfig, command: str) -> None:
    """Send a command to the dale's tmux session.

    The command is sent as keystrokes followed by Enter, so it appears
    exactly as if someone typed it in the terminal. This is what makes
    the co-dev tmux pattern work — the human sees it too.

    Args:
        dale:    The dale configuration.
        command: The command string to send.

    Raises:
        RuntimeError: If no tmux session exists on the dale.
    """
    if not tmux_has_session(dale):
        raise RuntimeError(
            f"No tmux session '{dale.session}' on {dale.name}. "
            f"Run 'sdale connect {dale.name}' first."
        )
    # Escape single quotes in the command for the outer SSH shell
    escaped = command.replace("'", "'\\''")
    ssh(dale, f"tmux send-keys -t '{dale.session}' '{escaped}' Enter", capture=True)


def tmux_capture(dale: DaleConfig, lines: int = 20) -> str:
    """Capture recent output from the dale's tmux pane.

    Args:
        dale:  The dale configuration.
        lines: Number of lines to capture from the bottom of the pane.

    Returns:
        The captured terminal output as a string.
    """
    result = ssh(
        dale,
        f"tmux capture-pane -t '{dale.session}' -p | tail -{lines}",
        capture=True,
    )
    return result.stdout


def tmux_kill(dale: DaleConfig) -> bool:
    """Kill the tmux session on the dale.

    Args:
        dale: The dale configuration.

    Returns:
        True if the session was killed, False if it didn't exist.
    """
    try:
        ssh(dale, f"tmux kill-session -t '{dale.session}' 2>/dev/null", capture=True)
        return True
    except subprocess.CalledProcessError:
        return False


def rsync(dale: DaleConfig, src: str, dst: str) -> str:
    """Rsync a local directory to the dale.

    Syncs files using compression, skipping patterns defined in the
    dale's exclude list (defaults to node_modules and .git).

    Args:
        dale: The dale configuration (uses dale.exclude for patterns).
        src:  Local source directory path.
        dst:  Remote destination directory path.

    Returns:
        The rsync stdout output.

    Raises:
        subprocess.CalledProcessError: If rsync fails.
    """
    # Ensure remote directory exists
    ssh(dale, f"mkdir -p '{dst}'", capture=True)

    # Build rsync command
    exclude_args = []
    for pattern in dale.exclude:
        exclude_args.extend(["--exclude", pattern])

    ssh_cmd = f"ssh {' '.join(dale.ssh_args)}"
    cmd = [
        "rsync", "-avz",
        *exclude_args,
        "-e", ssh_cmd,
        f"{src.rstrip('/')}/",
        f"{dale.ssh_dest}:{dst}/",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout
