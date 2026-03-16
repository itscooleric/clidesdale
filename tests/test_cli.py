"""Tests for sdale.cli — argument parsing, dispatch, and helpers."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

from sdale.cli import build_parser, _parse_since, cmd_log, main


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
