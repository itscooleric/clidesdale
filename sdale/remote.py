"""Remote execution helpers for sdale.

Wraps SSH and rsync commands for interacting with dales.
All remote commands go through tmux sessions for co-dev observability.
"""

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from .config import DaleConfig


def _known_hosts_path() -> Path:
    """Return the path to sdale's known_hosts file."""
    p = Path.home() / ".config" / "sdale"
    p.mkdir(parents=True, exist_ok=True)
    return p / "known_hosts"


def _ensure_host_known(dale: DaleConfig) -> None:
    """Add the dale's host key to sdale's known_hosts if not already present.

    Uses StrictHostKeyChecking=accept-new in ssh_args instead of
    ssh-keyscan, so no separate subprocess call is needed. This
    function just ensures the known_hosts file directory exists.
    """
    kh = _known_hosts_path()
    kh.parent.mkdir(parents=True, exist_ok=True)


def ssh(dale: DaleConfig, command: str, capture: bool = False,
        stdin_data: str | None = None, log: bool = False) -> subprocess.CompletedProcess:
    """Run a command on the dale via SSH.

    When log=True, the command and its output are appended to a live
    activity log on the remote host at /tmp/sdale-<dale>.log so humans
    can watch agent activity with ``tail -f``.

    Args:
        dale:       The dale configuration.
        command:    The remote command string to execute.
        capture:    If True, capture stdout/stderr. If False, inherit terminal.
        stdin_data: If provided, pipe this string to stdin of the remote command.
        log:        If True, tee output to the remote activity log.

    Returns:
        The CompletedProcess result.

    Raises:
        subprocess.CalledProcessError: If the SSH command fails.
    """
    _ensure_host_known(dale)

    if log:
        log_file = f"/opt/stacks/.sdale-{dale.name}.log"
        safe_cmd = command.replace("'", "'\\''")[:200]
        remote_cmd = (
            f"echo '' >> {log_file}; "
            f"echo '── '$(date +\"%H:%M:%S\")' $ {safe_cmd}' >> {log_file}; "
            f"{{ {command} ; }} 2>&1 | tee -a {log_file}"
        )
    else:
        remote_cmd = command

    cmd = ["ssh", *dale.ssh_args, dale.ssh_dest, remote_cmd]
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=True,
        input=stdin_data,
    )


def scp_to(dale: DaleConfig, local_path: str, remote_path: str) -> None:
    """Copy a local file to the dale via scp.

    Args:
        dale:        The dale configuration.
        local_path:  Path to the local file.
        remote_path: Destination path on the dale.

    Raises:
        FileNotFoundError: If the local file doesn't exist.
        subprocess.CalledProcessError: If scp fails.
    """
    if not Path(local_path).exists():
        raise FileNotFoundError(f"Local file not found: {local_path}")

    _ensure_host_known(dale)
    cmd = ["scp", *dale.ssh_args, local_path, f"{dale.ssh_dest}:{remote_path}"]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


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


def tmux_send_wait(dale: DaleConfig, command: str,
                   timeout: int = 300, interval: float = 2.0) -> str:
    """Send a command to the dale's tmux session and wait for it to finish.

    Appends a unique marker after the command, then polls tmux
    capture-pane until the marker appears in the output. Returns
    the output between the command and the marker.

    The command is still visible in tmux (co-dev friendly) but
    the caller blocks until completion — no blind sleep needed.

    Args:
        dale:     The dale configuration.
        command:  The command string to send.
        timeout:  Max seconds to wait (default: 300 = 5 min).
        interval: Seconds between polls (default: 2).

    Returns:
        The captured output from the command.

    Raises:
        RuntimeError: If no tmux session or timeout exceeded.
    """
    marker = f"SD{secrets.token_hex(3)}"

    # Send the command followed by an echo of the marker
    full_cmd = f"{command} ; echo {marker}"
    tmux_send(dale, full_cmd)

    # Poll until marker appears
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(interval)
        output = tmux_capture(dale, lines=200)
        if marker in output:
            # Extract output between command and marker.
            # The tmux pane shows: <prompt>command ; echo MARKER\n<output>\nMARKER
            # We want everything between the command line and the marker line.
            lines_list = output.splitlines()
            marker_idx = None
            cmd_idx = None
            for i, line in enumerate(lines_list):
                stripped = line.strip()
                if stripped == marker:
                    # This is the marker output line (exact match)
                    marker_idx = i
                elif marker in line:
                    # This is the command line containing "echo MARKER"
                    cmd_idx = i
            # If we found the marker output but not a separate command line,
            # look for any line containing the original command
            if cmd_idx is None:
                for i, line in enumerate(lines_list):
                    if command in line:
                        cmd_idx = i
                        break
            if cmd_idx is not None and marker_idx is not None and marker_idx > cmd_idx:
                result_lines = lines_list[cmd_idx + 1:marker_idx]
                return "\n".join(result_lines) + "\n" if result_lines else ""
            # Fallback: return everything before the marker
            if marker_idx is not None:
                result_lines = [l for l in lines_list[:marker_idx]
                                if marker not in l and command not in l]
                return "\n".join(result_lines) + "\n" if result_lines else ""
            return output

    raise RuntimeError(
        f"Timed out after {timeout}s waiting for command to finish on {dale.name}. "
        f"The command may still be running in tmux session '{dale.session}'."
    )


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


def tmux_attach(dale: DaleConfig) -> None:
    """Attach to the dale's tmux session interactively.

    This gives the user a live view of the tmux session where sdale
    sends commands. The user sees everything in real time and can
    even type (though the session is meant for observation).

    Replaces the current process with an interactive SSH + tmux attach.

    Args:
        dale: The dale configuration.

    Raises:
        RuntimeError: If no tmux session exists on the dale.
    """
    if not tmux_has_session(dale):
        raise RuntimeError(
            f"No tmux session '{dale.session}' on {dale.name}. "
            f"Run 'sdale connect {dale.name}' first."
        )
    cmd = ["ssh", *dale.ssh_args, "-t", dale.ssh_dest,
           f"tmux attach -t '{dale.session}'"]
    os.execvp("ssh", cmd)


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
