"""Tests for sdale.remote — SSH/tmux/rsync wrappers.

These tests mock subprocess.run to avoid needing actual SSH connections.
The remote module is thin wrappers, so we verify the correct commands
are constructed rather than testing SSH itself.
"""

import subprocess
import unittest
from unittest.mock import patch, call, MagicMock

from sdale.config import DaleConfig
from sdale.remote import (
    ssh,
    tmux_ensure,
    tmux_has_session,
    tmux_send,
    tmux_capture,
    tmux_kill,
)


def make_dale(**kwargs) -> DaleConfig:
    """Create a DaleConfig with test defaults."""
    defaults = {
        "name": "test",
        "host": "203.0.113.10",
        "user": "deploy",
        "key": "/tmp/test-key",
        "session": "sdale-test",
    }
    defaults.update(kwargs)
    return DaleConfig(**defaults)


class TestSsh(unittest.TestCase):
    """Tests for the ssh() function."""

    @patch("sdale.remote.subprocess.run")
    def test_builds_correct_command(self, mock_run: MagicMock) -> None:
        """SSH command includes key, destination, and remote command."""
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        dale = make_dale()

        ssh(dale, "echo hello", capture=True)

        mock_run.assert_called_once_with(
            ["ssh", "-i", "/tmp/test-key", "deploy@203.0.113.10", "echo hello"],
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("sdale.remote.subprocess.run")
    def test_no_key_omits_flag(self, mock_run: MagicMock) -> None:
        """SSH command omits -i when no key is configured."""
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        dale = make_dale(key="")

        ssh(dale, "ls", capture=True)

        cmd = mock_run.call_args[0][0]
        self.assertNotIn("-i", cmd)
        self.assertEqual(cmd, ["ssh", "deploy@203.0.113.10", "ls"])

    @patch("sdale.remote.subprocess.run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        """SSH raises CalledProcessError on non-zero exit."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh")
        dale = make_dale()

        with self.assertRaises(subprocess.CalledProcessError):
            ssh(dale, "false", capture=True)


class TestTmuxEnsure(unittest.TestCase):
    """Tests for tmux_ensure — creating tmux sessions."""

    @patch("sdale.remote.subprocess.run")
    def test_creates_session(self, mock_run: MagicMock) -> None:
        """Sends the correct tmux new-session command."""
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        dale = make_dale(session="work")

        tmux_ensure(dale)

        cmd = mock_run.call_args[0][0]
        # Should contain tmux new-session with the session name
        remote_cmd = cmd[-1]
        self.assertIn("tmux new-session -d -s 'work'", remote_cmd)


class TestTmuxHasSession(unittest.TestCase):
    """Tests for tmux_has_session — checking session existence."""

    @patch("sdale.remote.subprocess.run")
    def test_returns_true_when_exists(self, mock_run: MagicMock) -> None:
        """Returns True when tmux has-session succeeds."""
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        dale = make_dale()
        self.assertTrue(tmux_has_session(dale))

    @patch("sdale.remote.subprocess.run")
    def test_returns_false_when_missing(self, mock_run: MagicMock) -> None:
        """Returns False when tmux has-session fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh")
        dale = make_dale()
        self.assertFalse(tmux_has_session(dale))


class TestTmuxSend(unittest.TestCase):
    """Tests for tmux_send — sending commands to tmux."""

    @patch("sdale.remote.subprocess.run")
    def test_sends_command(self, mock_run: MagicMock) -> None:
        """Sends keys to the correct tmux session."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="")
        dale = make_dale(session="work")

        tmux_send(dale, "docker build .")

        # Second call should be the send-keys (first is has-session check)
        calls = mock_run.call_args_list
        self.assertEqual(len(calls), 2)
        send_cmd = calls[1][0][0][-1]
        self.assertIn("tmux send-keys -t 'work'", send_cmd)
        self.assertIn("docker build .", send_cmd)

    @patch("sdale.remote.subprocess.run")
    def test_raises_when_no_session(self, mock_run: MagicMock) -> None:
        """Raises RuntimeError when no tmux session exists."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh")
        dale = make_dale()

        with self.assertRaises(RuntimeError) as ctx:
            tmux_send(dale, "ls")
        self.assertIn("No tmux session", str(ctx.exception))
        self.assertIn("sdale connect", str(ctx.exception))

    @patch("sdale.remote.subprocess.run")
    def test_escapes_single_quotes(self, mock_run: MagicMock) -> None:
        """Single quotes in commands are properly escaped."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="")
        dale = make_dale()

        tmux_send(dale, "echo 'hello world'")

        send_cmd = mock_run.call_args_list[1][0][0][-1]
        self.assertIn("'\\''", send_cmd)


class TestTmuxCapture(unittest.TestCase):
    """Tests for tmux_capture — reading tmux pane output."""

    @patch("sdale.remote.subprocess.run")
    def test_captures_output(self, mock_run: MagicMock) -> None:
        """Returns stdout from tmux capture-pane."""
        mock_run.return_value = subprocess.CompletedProcess(
            [], 0, stdout="line1\nline2\nline3\n"
        )
        dale = make_dale()

        result = tmux_capture(dale, lines=10)
        self.assertEqual(result, "line1\nline2\nline3\n")

    @patch("sdale.remote.subprocess.run")
    def test_uses_correct_line_count(self, mock_run: MagicMock) -> None:
        """Passes the line count to tail."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="")
        dale = make_dale(session="work")

        tmux_capture(dale, lines=50)

        cmd = mock_run.call_args[0][0][-1]
        self.assertIn("tail -50", cmd)


class TestTmuxKill(unittest.TestCase):
    """Tests for tmux_kill — killing tmux sessions."""

    @patch("sdale.remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run: MagicMock) -> None:
        """Returns True when the session is killed."""
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        dale = make_dale()
        self.assertTrue(tmux_kill(dale))

    @patch("sdale.remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run: MagicMock) -> None:
        """Returns False when no session exists to kill."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh")
        dale = make_dale()
        self.assertFalse(tmux_kill(dale))


if __name__ == "__main__":
    unittest.main()
