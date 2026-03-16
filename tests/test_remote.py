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
    scp_to,
    ssh,
    tmux_attach,
    tmux_capture,
    tmux_ensure,
    tmux_has_session,
    tmux_kill,
    tmux_send,
    tmux_send_wait,
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

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("-i", cmd)
        self.assertIn("/tmp/test-key", cmd)
        self.assertIn("deploy@203.0.113.10", cmd)
        self.assertEqual(cmd[-1], "echo hello")

    @patch("sdale.remote.subprocess.run")
    def test_no_key_omits_flag(self, mock_run: MagicMock) -> None:
        """SSH command omits -i when no key is configured."""
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        dale = make_dale(key="")

        ssh(dale, "ls", capture=True)

        cmd = mock_run.call_args[0][0]
        self.assertNotIn("-i", cmd)
        self.assertEqual(cmd[0], "ssh")
        self.assertEqual(cmd[-1], "ls")
        self.assertIn("deploy@203.0.113.10", cmd)

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


class TestTmuxSendWait(unittest.TestCase):
    """Tests for tmux_send_wait — send command and wait for completion."""

    @patch("sdale.remote.time.sleep")
    @patch("sdale.remote.subprocess.run")
    def test_returns_output_on_marker(self, mock_run: MagicMock, mock_sleep: MagicMock) -> None:
        """Returns command output when marker is detected."""
        dale = make_dale(session="work")

        # First calls: has-session check + send-keys (from tmux_send)
        # Then: capture-pane poll returns output with marker
        def side_effect(*args, **kwargs):
            cmd = args[0]
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "capture-pane" in cmd_str:
                # Simulate output with marker
                return subprocess.CompletedProcess([], 0,
                    stdout="$ hostname ; echo SDabcd12\nforge-edge\nSDabcd12\n$\n")
            return subprocess.CompletedProcess([], 0, stdout="")

        mock_run.side_effect = side_effect

        # Patch secrets.token_hex to return predictable value
        with patch("sdale.remote.secrets.token_hex", return_value="abcd12"):
            output = tmux_send_wait(dale, "hostname", timeout=10, interval=0.1)

        self.assertIn("forge-edge", output)

    @patch("sdale.remote.time.sleep")
    @patch("sdale.remote.time.monotonic")
    @patch("sdale.remote.subprocess.run")
    def test_raises_on_timeout(self, mock_run: MagicMock, mock_mono: MagicMock, mock_sleep: MagicMock) -> None:
        """Raises RuntimeError when command doesn't finish in time."""
        dale = make_dale()
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="still running...\n")
        # Simulate time passing beyond timeout
        mock_mono.side_effect = [0, 0, 5, 10, 20]

        with self.assertRaises(RuntimeError) as ctx:
            tmux_send_wait(dale, "sleep 999", timeout=10, interval=1)
        self.assertIn("Timed out", str(ctx.exception))


class TestScpTo(unittest.TestCase):
    """Tests for scp_to — copying files to a dale."""

    @patch("sdale.remote.subprocess.run")
    @patch("sdale.remote.Path.exists", return_value=True)
    def test_copies_file(self, mock_exists: MagicMock, mock_run: MagicMock) -> None:
        """Builds correct scp command."""
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        dale = make_dale()

        scp_to(dale, "/tmp/myfile.env", "/opt/stacks/.env")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "scp")
        self.assertIn("/tmp/myfile.env", cmd)
        self.assertIn("deploy@203.0.113.10:/opt/stacks/.env", cmd)

    def test_raises_on_missing_file(self) -> None:
        """Raises FileNotFoundError for nonexistent local file."""
        dale = make_dale()
        with self.assertRaises(FileNotFoundError):
            scp_to(dale, "/nonexistent/file.txt", "/tmp/dst")


class TestTmuxAttach(unittest.TestCase):
    """Tests for tmux_attach — attaching to tmux sessions."""

    @patch("sdale.remote.os.execvp")
    @patch("sdale.remote.subprocess.run")
    def test_attaches_to_session(self, mock_run: MagicMock, mock_exec: MagicMock) -> None:
        """Calls execvp with the correct ssh + tmux attach command."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="")
        dale = make_dale(session="work")

        tmux_attach(dale)

        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0][1]
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("-t", cmd)
        self.assertIn("tmux attach -t 'work'", cmd[-1])

    @patch("sdale.remote.subprocess.run")
    def test_raises_when_no_session(self, mock_run: MagicMock) -> None:
        """Raises RuntimeError when no tmux session exists."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh")
        dale = make_dale()

        with self.assertRaises(RuntimeError) as ctx:
            tmux_attach(dale, )
        self.assertIn("No tmux session", str(ctx.exception))


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
