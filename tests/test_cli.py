"""Tests for sdale.cli — argument parsing, dispatch, and helpers."""

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

from sdale.cli import (
    build_parser, _parse_since, cmd_cat, cmd_exec, cmd_health,
    cmd_info, cmd_log, cmd_logs, cmd_multi, cmd_pull, cmd_write, main,
)


class TestBuildParser(unittest.TestCase):
    """Tests for the argparse parser construction."""

    def setUp(self) -> None:
        """Build the parser once for all tests."""
        self.parser = build_parser()

    def test_version_flag(self) -> None:
        """--version flag prints version and exits."""
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_connect_subcommand(self) -> None:
        """'connect' subcommand parses dale name."""
        args = self.parser.parse_args(["connect", "edge"])
        self.assertEqual(args.subcmd, "connect")
        self.assertEqual(args.dale, "edge")

    def test_watch_subcommand(self) -> None:
        """'watch' subcommand parses dale name."""
        args = self.parser.parse_args(["watch", "edge"])
        self.assertEqual(args.subcmd, "watch")
        self.assertEqual(args.dale, "edge")

    def test_exec_subcommand(self) -> None:
        """'exec' subcommand parses dale name and command."""
        args = self.parser.parse_args(["exec", "edge", "docker ps"])
        self.assertEqual(args.subcmd, "exec")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.command, "docker ps")

    def test_push_subcommand(self) -> None:
        """'push' subcommand parses dale, src, and dst."""
        args = self.parser.parse_args(["push", "edge", ".env", "/opt/stacks/clem/.env"])
        self.assertEqual(args.subcmd, "push")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.src, ".env")
        self.assertEqual(args.dst, "/opt/stacks/clem/.env")

    def test_run_subcommand(self) -> None:
        """'run' subcommand parses dale name and command."""
        args = self.parser.parse_args(["run", "edge", "docker build ."])
        self.assertEqual(args.subcmd, "run")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.command, "docker build .")
        self.assertFalse(args.wait)

    def test_run_wait_flag(self) -> None:
        """'run --wait' sets wait=True."""
        args = self.parser.parse_args(["run", "--wait", "edge", "docker build ."])
        self.assertTrue(args.wait)
        self.assertEqual(args.timeout, 300)

    def test_run_wait_short_flag(self) -> None:
        """'run -w -t 60' sets wait and custom timeout."""
        args = self.parser.parse_args(["run", "-w", "-t", "60", "edge", "make build"])
        self.assertTrue(args.wait)
        self.assertEqual(args.timeout, 60)

    def test_output_defaults(self) -> None:
        """'output' subcommand defaults to 20 lines."""
        args = self.parser.parse_args(["output", "edge"])
        self.assertEqual(args.subcmd, "output")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.lines, 20)

    def test_output_custom_lines(self) -> None:
        """'output' --lines flag overrides the default."""
        args = self.parser.parse_args(["output", "edge", "--lines", "50"])
        self.assertEqual(args.lines, 50)

    def test_output_short_flag(self) -> None:
        """'output' -n flag works as shorthand for --lines."""
        args = self.parser.parse_args(["output", "edge", "-n", "5"])
        self.assertEqual(args.lines, 5)

    def test_sync_subcommand(self) -> None:
        """'sync' subcommand parses dale, src, and optional dst."""
        args = self.parser.parse_args(["sync", "edge", "./src", "/srv/app"])
        self.assertEqual(args.subcmd, "sync")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.src, "./src")
        self.assertEqual(args.dst, "/srv/app")

    def test_sync_default_dst(self) -> None:
        """'sync' defaults dst to /tmp/sdale-sync."""
        args = self.parser.parse_args(["sync", "edge", "./src"])
        self.assertEqual(args.dst, "/tmp/sdale-sync")

    def test_status_optional_dale(self) -> None:
        """'status' dale argument is optional."""
        args = self.parser.parse_args(["status"])
        self.assertEqual(args.subcmd, "status")
        self.assertEqual(args.dale, "")

    def test_status_with_dale(self) -> None:
        """'status' with a dale name."""
        args = self.parser.parse_args(["status", "edge"])
        self.assertEqual(args.dale, "edge")

    def test_log_full_flag(self) -> None:
        """'log' --full flag is captured."""
        args = self.parser.parse_args(["log", "edge", "--full"])
        self.assertTrue(args.full)

    def test_log_since_flag(self) -> None:
        """'log' --since flag captures duration string."""
        args = self.parser.parse_args(["log", "edge", "--since", "1h"])
        self.assertEqual(args.since, "1h")

    def test_disconnect_subcommand(self) -> None:
        """'disconnect' subcommand parses dale name."""
        args = self.parser.parse_args(["disconnect", "edge"])
        self.assertEqual(args.subcmd, "disconnect")
        self.assertEqual(args.dale, "edge")


class TestParseSince(unittest.TestCase):
    """Tests for the _parse_since duration parser."""

    def test_minutes(self) -> None:
        """Parses minute durations."""
        cutoff = _parse_since("30m")
        expected = datetime.now(timezone.utc) - timedelta(minutes=30)
        # Allow 2 seconds of drift
        self.assertAlmostEqual(
            cutoff.timestamp(), expected.timestamp(), delta=2
        )

    def test_hours(self) -> None:
        """Parses hour durations."""
        cutoff = _parse_since("2h")
        expected = datetime.now(timezone.utc) - timedelta(hours=2)
        self.assertAlmostEqual(
            cutoff.timestamp(), expected.timestamp(), delta=2
        )

    def test_days(self) -> None:
        """Parses day durations."""
        cutoff = _parse_since("7d")
        expected = datetime.now(timezone.utc) - timedelta(days=7)
        self.assertAlmostEqual(
            cutoff.timestamp(), expected.timestamp(), delta=2
        )

    def test_invalid_unit(self) -> None:
        """Raises ValueError for unknown duration unit."""
        with self.assertRaises(ValueError) as ctx:
            _parse_since("5x")
        self.assertIn("Unknown duration unit", str(ctx.exception))

    def test_invalid_amount(self) -> None:
        """Raises ValueError for non-numeric amount."""
        with self.assertRaises(ValueError) as ctx:
            _parse_since("abch")
        self.assertIn("Invalid duration", str(ctx.exception))


class TestCmdLog(unittest.TestCase):
    """Tests for the log subcommand."""

    def _write_events(self, log_dir: str, dale: str, events: list[dict]) -> None:
        """Helper to write test events to a log file."""
        dale_dir = Path(log_dir) / dale
        dale_dir.mkdir(parents=True, exist_ok=True)
        log_file = dale_dir / "events.jsonl"
        with open(log_file, "w") as fh:
            for event in events:
                fh.write(json.dumps(event) + "\n")

    def test_log_full(self) -> None:
        """--full flag prints all events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = [
                {"event": "dale_connect", "ts": "2026-03-15T00:00:00Z"},
                {"event": "dale_run", "ts": "2026-03-15T00:01:00Z", "command": "ls"},
                {"event": "dale_disconnect", "ts": "2026-03-15T00:02:00Z"},
            ]
            self._write_events(tmpdir, "edge", events)

            with patch.dict(os.environ, {"SDALE_LOG_DIR": tmpdir}):
                args = MagicMock()
                args.dale = "edge"
                args.full = True
                args.since = None

                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    cmd_log(args)
                    output = mock_out.getvalue()

                lines = output.strip().splitlines()
                self.assertEqual(len(lines), 3)

    def test_log_default_tail(self) -> None:
        """Default mode shows last 20 events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = [
                {"event": f"dale_run", "ts": f"2026-03-15T00:{i:02d}:00Z"}
                for i in range(25)
            ]
            self._write_events(tmpdir, "edge", events)

            with patch.dict(os.environ, {"SDALE_LOG_DIR": tmpdir}):
                args = MagicMock()
                args.dale = "edge"
                args.full = False
                args.since = None

                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    cmd_log(args)
                    output = mock_out.getvalue()

                lines = output.strip().splitlines()
                self.assertEqual(len(lines), 20)


class TestExecMergeStderr(unittest.TestCase):
    """Tests for the exec --merge-stderr / -e flag."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_merge_stderr_long_flag(self) -> None:
        """--merge-stderr flag is captured."""
        args = self.parser.parse_args(["exec", "--merge-stderr", "edge", "ls"])
        self.assertTrue(args.merge_stderr)

    def test_merge_stderr_short_flag(self) -> None:
        """-e flag is captured."""
        args = self.parser.parse_args(["exec", "-e", "edge", "ls"])
        self.assertTrue(args.merge_stderr)

    def test_no_merge_stderr_default(self) -> None:
        """merge_stderr defaults to False."""
        args = self.parser.parse_args(["exec", "edge", "ls"])
        self.assertFalse(args.merge_stderr)


class TestPullSubcommand(unittest.TestCase):
    """Tests for the pull subcommand parser."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_pull_with_local(self) -> None:
        """'pull' parses dale, remote, and local."""
        args = self.parser.parse_args(["pull", "edge", "/tmp/file", "./local"])
        self.assertEqual(args.subcmd, "pull")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.remote, "/tmp/file")
        self.assertEqual(args.local, "./local")

    def test_pull_default_local(self) -> None:
        """'pull' defaults local to empty string (uses remote filename)."""
        args = self.parser.parse_args(["pull", "edge", "/tmp/file"])
        self.assertEqual(args.local, "")


class TestCatSubcommand(unittest.TestCase):
    """Tests for the cat subcommand parser."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_cat_single_file(self) -> None:
        """'cat' parses single path."""
        args = self.parser.parse_args(["cat", "edge", "/etc/hosts"])
        self.assertEqual(args.subcmd, "cat")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.paths, ["/etc/hosts"])

    def test_cat_multiple_files(self) -> None:
        """'cat' parses multiple paths."""
        args = self.parser.parse_args(["cat", "edge", "/etc/hosts", "/etc/resolv.conf"])
        self.assertEqual(args.paths, ["/etc/hosts", "/etc/resolv.conf"])


class TestWriteSubcommand(unittest.TestCase):
    """Tests for the write subcommand parser."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_write_parses_dale_and_path(self) -> None:
        """'write' parses dale and remote path."""
        args = self.parser.parse_args(["write", "edge", "/opt/app/.env"])
        self.assertEqual(args.subcmd, "write")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.path, "/opt/app/.env")
        self.assertIsNone(args.from_file)

    def test_write_from_flag(self) -> None:
        """'write' parses --from for local file source."""
        args = self.parser.parse_args(["write", "edge", "/remote/f", "--from", "/local/f"])
        self.assertEqual(args.from_file, "/local/f")

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.scp_to")
    @patch("sdale.cli.get_dale")
    def test_write_from_file(self, mock_get: MagicMock, mock_scp: MagicMock,
                             mock_ssh: MagicMock, mock_logger: MagicMock) -> None:
        """'write --from' reads local file and pushes to dale."""
        from sdale.cli import cmd_write
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.return_value = subprocess.CompletedProcess([], 0)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            f.flush()
            tmp = f.name

        try:
            args = MagicMock()
            args.dale = "edge"
            args.path = "/remote/file"
            args.from_file = tmp
            cmd_write(args)
            mock_scp.assert_called_once()
            mock_ssh.assert_called_once()
        finally:
            os.unlink(tmp)


class TestLogsSubcommand(unittest.TestCase):
    """Tests for the logs subcommand parser."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_logs_parses_container(self) -> None:
        """'logs' parses dale and container name."""
        args = self.parser.parse_args(["logs", "edge", "cloperator"])
        self.assertEqual(args.subcmd, "logs")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.container, "cloperator")
        self.assertEqual(args.tail, 50)
        self.assertFalse(args.follow)

    def test_logs_with_options(self) -> None:
        """'logs' parses --tail, --since, --follow."""
        args = self.parser.parse_args(["logs", "edge", "clem", "-n", "100", "--since", "1h", "-f"])
        self.assertEqual(args.tail, 100)
        self.assertEqual(args.since, "1h")
        self.assertTrue(args.follow)

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_logs_builds_docker_command(self, mock_get: MagicMock,
                                        mock_ssh: MagicMock,
                                        mock_logger: MagicMock) -> None:
        """'logs' constructs correct docker logs command."""
        from sdale.cli import cmd_logs
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, stdout="log line\n")

        args = MagicMock()
        args.dale = "edge"
        args.container = "myapp"
        args.tail = 25
        args.since = "2h"
        args.follow = False
        cmd_logs(args)

        call_args = mock_ssh.call_args
        cmd_str = call_args[0][1]
        self.assertIn("docker logs", cmd_str)
        self.assertIn("--tail 25", cmd_str)
        self.assertIn("--since 2h", cmd_str)
        self.assertIn("myapp", cmd_str)
        self.assertNotIn("--follow", cmd_str)


class TestInfoSubcommand(unittest.TestCase):
    """Tests for the info subcommand parser."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_info_parses_dale(self) -> None:
        """'info' parses dale name."""
        args = self.parser.parse_args(["info", "edge"])
        self.assertEqual(args.subcmd, "info")
        self.assertEqual(args.dale, "edge")
        self.assertFalse(args.docker)
        self.assertFalse(args.tools)
        self.assertFalse(args.json)

    def test_info_all_flag(self) -> None:
        """'info --all' sets the all flag."""
        args = self.parser.parse_args(["info", "edge", "--all"])
        self.assertTrue(args.all)

    def test_info_json_flag(self) -> None:
        """'info --json' sets the json flag."""
        args = self.parser.parse_args(["info", "edge", "-j"])
        self.assertTrue(args.json)

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_info_parses_output(self, mock_get: MagicMock, mock_ssh: MagicMock,
                                 mock_logger: MagicMock) -> None:
        """'info' parses key=value output from SSH."""
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, stdout=(
                "HOSTNAME=forge-edge\n"
                "KERNEL=6.1.0\n"
                "OS=Ubuntu 24.04 LTS\n"
                "ARCH=x86_64\n"
                "UPTIME=up 3 days\n"
                "LOAD=0.05 0.03 0.01\n"
                "CPUS=2\n"
                "CPU_MODEL=Intel Xeon\n"
                "MEM_TOTAL=4096\n"
                "MEM_USED=1024\n"
                "MEM_AVAIL=2800\n"
                "SWAP_TOTAL=0\n"
                "SWAP_USED=0\n"
                "DISK_INFO_START\n"
                "Mounted on  Size  Used Avail Use%\n"
                "/           50G   8G   40G  16%\n"
                "DISK_INFO_END\n"
                "TAILSCALE_IP=100.95.91.31\n"
                "TAILSCALE_STATUS=true\n"
            )
        )

        args = MagicMock()
        args.dale = "edge"
        args.docker = False
        args.tools = False
        args.net = False
        args.all = False
        args.json = False

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_info(args)
            output = mock_out.getvalue()

        self.assertIn("forge-edge", output)
        self.assertIn("Ubuntu 24.04", output)
        self.assertIn("1024MB / 4096MB", output)
        self.assertIn("100.95.91.31", output)

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_info_json_output(self, mock_get: MagicMock, mock_ssh: MagicMock,
                               mock_logger: MagicMock) -> None:
        """'info --json' outputs valid JSON."""
        dale_mock = MagicMock()
        dale_mock.name = "edge"
        mock_get.return_value = dale_mock
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, stdout="HOSTNAME=test\nOS=Linux\nKERNEL=6.1\nARCH=x86\n"
                          "UPTIME=up 1 day\nLOAD=0.1\nCPUS=2\nCPU_MODEL=Xeon\n"
                          "MEM_TOTAL=4096\nMEM_USED=1024\nMEM_AVAIL=2800\n"
                          "SWAP_TOTAL=0\nSWAP_USED=0\n"
                          "TAILSCALE_IP=100.1.2.3\nTAILSCALE_STATUS=true\n"
        )

        args = MagicMock()
        args.dale = "edge"
        args.docker = False
        args.tools = False
        args.net = False
        args.all = False
        args.json = True

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_info(args)
            output = mock_out.getvalue()

        import json
        data = json.loads(output)
        self.assertEqual(data["hostname"], "test")
        self.assertEqual(data["cpus"], "2")


class TestCmdExecMergeStderr(unittest.TestCase):
    """Functional tests for cmd_exec with --merge-stderr."""

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_stderr_goes_to_stdout_with_flag(self, mock_get: MagicMock,
                                              mock_ssh: MagicMock,
                                              mock_logger: MagicMock) -> None:
        """With -e, stderr is printed to stdout."""
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, stdout="out\n", stderr="warn\n"
        )

        args = MagicMock()
        args.dale = "edge"
        args.command = "ls"
        args.merge_stderr = True

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_exec(args)
            output = mock_out.getvalue()

        self.assertIn("out\n", output)
        self.assertIn("warn\n", output)

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_stderr_goes_to_stderr_without_flag(self, mock_get: MagicMock,
                                                 mock_ssh: MagicMock,
                                                 mock_logger: MagicMock) -> None:
        """Without -e, stderr goes to stderr."""
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, stdout="out\n", stderr="warn\n"
        )

        args = MagicMock()
        args.dale = "edge"
        args.command = "ls"
        args.merge_stderr = False

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                cmd_exec(args)

        self.assertIn("out\n", mock_out.getvalue())
        self.assertIn("warn\n", mock_err.getvalue())


class TestCmdPull(unittest.TestCase):
    """Functional tests for cmd_pull."""

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.scp_from")
    @patch("sdale.cli.get_dale")
    def test_pull_with_explicit_local(self, mock_get: MagicMock,
                                       mock_scp: MagicMock,
                                       mock_logger: MagicMock) -> None:
        """Pulls to explicit local path."""
        mock_dale = MagicMock(name="edge")
        mock_get.return_value = mock_dale

        args = MagicMock()
        args.dale = "edge"
        args.remote = "/opt/stacks/app.log"
        args.local = "/tmp/local.log"

        with patch("sys.stdout", new_callable=StringIO):
            cmd_pull(args)

        mock_scp.assert_called_once_with(mock_dale, "/opt/stacks/app.log", "/tmp/local.log")

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.scp_from")
    @patch("sdale.cli.get_dale")
    def test_pull_default_local_uses_basename(self, mock_get: MagicMock,
                                               mock_scp: MagicMock,
                                               mock_logger: MagicMock) -> None:
        """Without local path, uses the remote filename."""
        mock_dale = MagicMock(name="edge")
        mock_get.return_value = mock_dale

        args = MagicMock()
        args.dale = "edge"
        args.remote = "/opt/stacks/app.log"
        args.local = ""

        with patch("sys.stdout", new_callable=StringIO):
            cmd_pull(args)

        mock_scp.assert_called_once_with(mock_dale, "/opt/stacks/app.log", "app.log")


class TestMultiSubcommand(unittest.TestCase):
    """Tests for the multi subcommand parser."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_multi_parses_commands(self) -> None:
        """'multi' parses dale and multiple commands."""
        args = self.parser.parse_args(["multi", "edge", "ls", "df -h", "uptime"])
        self.assertEqual(args.subcmd, "multi")
        self.assertEqual(args.dale, "edge")
        self.assertEqual(args.commands, ["ls", "df -h", "uptime"])

    def test_multi_single_command(self) -> None:
        """'multi' works with a single command."""
        args = self.parser.parse_args(["multi", "edge", "hostname"])
        self.assertEqual(args.commands, ["hostname"])


class TestHealthSubcommand(unittest.TestCase):
    """Tests for the health subcommand parser."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_health_basic(self) -> None:
        """'health' parses dale name."""
        args = self.parser.parse_args(["health", "edge"])
        self.assertEqual(args.subcmd, "health")
        self.assertEqual(args.dale, "edge")
        self.assertFalse(args.docker)

    def test_health_docker_flag(self) -> None:
        """'health --docker' sets docker flag."""
        args = self.parser.parse_args(["health", "--docker", "edge"])
        self.assertTrue(args.docker)

    def test_health_docker_short_flag(self) -> None:
        """'health -d' sets docker flag."""
        args = self.parser.parse_args(["health", "-d", "edge"])
        self.assertTrue(args.docker)


class TestCmdMulti(unittest.TestCase):
    """Functional tests for cmd_multi."""

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_combines_commands(self, mock_get: MagicMock,
                                mock_ssh: MagicMock,
                                mock_logger: MagicMock) -> None:
        """Sends all commands in a single SSH call."""
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, stdout="── ls ──\nfile1\n\n── uptime ──\nup 2d\n\n"
        )

        args = MagicMock()
        args.dale = "edge"
        args.commands = ["ls", "uptime"]

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_multi(args)

        # Should call ssh exactly once (single round-trip)
        mock_ssh.assert_called_once()
        output = mock_out.getvalue()
        self.assertIn("── ls ──", output)
        self.assertIn("── uptime ──", output)

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_exits_on_failure(self, mock_get: MagicMock,
                               mock_ssh: MagicMock,
                               mock_logger: MagicMock) -> None:
        """Exits with non-zero when SSH fails."""
        mock_get.return_value = MagicMock(name="edge")
        exc = subprocess.CalledProcessError(2, "ssh")
        exc.stdout = "── bad ──\nerror\n"
        mock_ssh.side_effect = exc

        args = MagicMock()
        args.dale = "edge"
        args.commands = ["bad-cmd"]

        with patch("sys.stdout", new_callable=StringIO):
            with self.assertRaises(SystemExit) as ctx:
                cmd_multi(args)
            self.assertEqual(ctx.exception.code, 2)


class TestCmdHealth(unittest.TestCase):
    """Functional tests for cmd_health."""

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_basic_health(self, mock_get: MagicMock,
                           mock_ssh: MagicMock,
                           mock_logger: MagicMock) -> None:
        """Prints health summary line."""
        mock_dale = MagicMock(name="edge", session="work")
        mock_dale.name = "edge"
        mock_dale.session = "work"
        mock_get.return_value = mock_dale

        mock_ssh.side_effect = [
            # First call: SSH echo ok
            subprocess.CompletedProcess([], 0, stdout="ok\n"),
            # Second call: system info
            subprocess.CompletedProcess([], 0, stdout="LOAD=0.3\nDISK=45%\nUP=up 2 days\ntmux:yes\n"),
        ]

        args = MagicMock()
        args.dale = "edge"
        args.docker = False

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_health(args)

        output = mock_out.getvalue()
        self.assertIn("edge", output)
        self.assertIn("SSH ok", output)
        self.assertIn("tmux: running", output)
        self.assertIn("45%", output)
        self.assertIn("0.3", output)

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_health_ssh_failure(self, mock_get: MagicMock,
                                 mock_ssh: MagicMock,
                                 mock_logger: MagicMock) -> None:
        """Exits with error when SSH fails."""
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.side_effect = subprocess.CalledProcessError(255, "ssh")

        args = MagicMock()
        args.dale = "edge"
        args.docker = False

        with patch("sys.stderr", new_callable=StringIO):
            with self.assertRaises(SystemExit) as ctx:
                cmd_health(args)
            self.assertEqual(ctx.exception.code, 1)

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_health_with_docker(self, mock_get: MagicMock,
                                 mock_ssh: MagicMock,
                                 mock_logger: MagicMock) -> None:
        """--docker flag shows container list."""
        mock_dale = MagicMock()
        mock_dale.name = "edge"
        mock_dale.session = "work"
        mock_get.return_value = mock_dale

        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="ok\n"),
            subprocess.CompletedProcess([], 0, stdout=(
                "LOAD=0.1\nDISK=30%\nUP=up 5d\ntmux:yes\n"
                "DOCKER_START\nclide:Up 2 hours\ncloperator:Up 2 hours\nDOCKER_END\n"
            )),
        ]

        args = MagicMock()
        args.dale = "edge"
        args.docker = True

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_health(args)

        output = mock_out.getvalue()
        self.assertIn("Containers (2)", output)
        self.assertIn("clide:Up 2 hours", output)
        self.assertIn("cloperator:Up 2 hours", output)


class TestCmdCat(unittest.TestCase):
    """Functional tests for cmd_cat."""

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_cat_single_file(self, mock_get: MagicMock,
                              mock_ssh: MagicMock,
                              mock_logger: MagicMock) -> None:
        """Single file prints contents without header."""
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, stdout="nameserver 1.1.1.1\n"
        )

        args = MagicMock()
        args.dale = "edge"
        args.paths = ["/etc/resolv.conf"]

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_cat(args)

        output = mock_out.getvalue()
        self.assertIn("nameserver 1.1.1.1", output)
        # No header for single file
        self.assertNotIn("──", output)

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_cat_multiple_files_shows_headers(self, mock_get: MagicMock,
                                               mock_ssh: MagicMock,
                                               mock_logger: MagicMock) -> None:
        """Multiple files show header lines."""
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="127.0.0.1 localhost\n"),
            subprocess.CompletedProcess([], 0, stdout="nameserver 1.1.1.1\n"),
        ]

        args = MagicMock()
        args.dale = "edge"
        args.paths = ["/etc/hosts", "/etc/resolv.conf"]

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_cat(args)

        output = mock_out.getvalue()
        self.assertIn("── /etc/hosts ──", output)
        self.assertIn("── /etc/resolv.conf ──", output)
        self.assertIn("127.0.0.1 localhost", output)
        self.assertIn("nameserver 1.1.1.1", output)

    @patch("sdale.cli.EventLogger")
    @patch("sdale.cli.ssh")
    @patch("sdale.cli.get_dale")
    def test_cat_missing_file_shows_error(self, mock_get: MagicMock,
                                           mock_ssh: MagicMock,
                                           mock_logger: MagicMock) -> None:
        """Missing file prints error to stderr."""
        mock_get.return_value = MagicMock(name="edge")
        mock_ssh.side_effect = subprocess.CalledProcessError(
            1, "ssh", stderr="No such file or directory"
        )

        args = MagicMock()
        args.dale = "edge"
        args.paths = ["/nonexistent"]

        with patch("sys.stdout", new_callable=StringIO):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                cmd_cat(args)

        self.assertIn("No such file", mock_err.getvalue())


class TestMainDispatch(unittest.TestCase):
    """Tests for the main() entry point dispatch."""

    def test_no_args_prints_help(self) -> None:
        """Running with no arguments prints help and exits 0."""
        with patch("sys.argv", ["sdale"]):
            with self.assertRaises(SystemExit) as ctx:
                main()
            self.assertEqual(ctx.exception.code, 0)

    def test_missing_config_shows_error(self) -> None:
        """Running a command with no config shows a friendly error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("sys.argv", ["sdale", "connect", "edge"]):
                with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                    with patch("sdale.config.Path.home", return_value=Path(tmpdir)):
                        with self.assertRaises(SystemExit) as ctx:
                            main()
                        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
