"""Tests for sdale.logger — JSONL event logging and secret scrubbing."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sdale.logger import EventLogger, _scrub_secrets


class TestScrubSecrets(unittest.TestCase):
    """Tests for the _scrub_secrets function."""

    def test_scrubs_known_secret(self) -> None:
        """Replaces a known secret env var value with a redaction marker."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-super-secret-key"}):
            text = "using key sk-ant-super-secret-key here"
            result = _scrub_secrets(text)
            self.assertEqual(result, "using key [REDACTED:ANTHROPIC_API_KEY] here")

    def test_ignores_short_values(self) -> None:
        """Does not scrub values with 4 or fewer characters (too generic)."""
        with patch.dict(os.environ, {"GH_TOKEN": "abc"}):
            text = "token is abc"
            result = _scrub_secrets(text)
            self.assertEqual(result, "token is abc")

    def test_ignores_unset_vars(self) -> None:
        """Does not crash or modify text when env vars are unset."""
        env = {var: "" for var in ["ANTHROPIC_API_KEY", "GH_TOKEN"]}
        with patch.dict(os.environ, env, clear=False):
            text = "no secrets here"
            result = _scrub_secrets(text)
            self.assertEqual(result, "no secrets here")

    def test_scrubs_multiple_secrets(self) -> None:
        """Scrubs multiple different secrets in the same string."""
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "GH_TOKEN": "ghp_tokentokentoken",
        }):
            text = "keys: sk-ant-secret and ghp_tokentokentoken"
            result = _scrub_secrets(text)
            self.assertIn("[REDACTED:ANTHROPIC_API_KEY]", result)
            self.assertIn("[REDACTED:GH_TOKEN]", result)
            self.assertNotIn("sk-ant-secret", result)
            self.assertNotIn("ghp_tokentokentoken", result)


class TestEventLogger(unittest.TestCase):
    """Tests for the EventLogger class."""

    def test_creates_log_directory(self) -> None:
        """Logger creates the log directory on initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SDALE_LOG_DIR": tmpdir}):
                logger = EventLogger("edge")
                self.assertTrue(logger.log_dir.is_dir())
                self.assertEqual(logger.log_dir, Path(tmpdir) / "edge")

    def test_session_id_format(self) -> None:
        """Session ID follows the sdale-<dale>-<timestamp> format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SDALE_LOG_DIR": tmpdir}):
                logger = EventLogger("edge")
                self.assertTrue(logger.session_id.startswith("sdale-edge-"))
                # The timestamp part should be numeric
                ts_part = logger.session_id.split("-", 2)[2]
                self.assertTrue(ts_part.isdigit())

    def test_log_writes_jsonl(self) -> None:
        """log() appends a valid JSONL line to the events file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SDALE_LOG_DIR": tmpdir}):
                logger = EventLogger("edge")
                logger.log("dale_connect", host="203.0.113.10")

                content = logger.log_file.read_text()
                lines = content.strip().splitlines()
                self.assertEqual(len(lines), 1)

                event = json.loads(lines[0])
                self.assertEqual(event["event"], "dale_connect")
                self.assertEqual(event["schema_version"], 1)
                self.assertEqual(event["dale"], "edge")
                self.assertEqual(event["host"], "203.0.113.10")
                self.assertIn("ts", event)
                self.assertIn("session_id", event)

    def test_log_appends_multiple_events(self) -> None:
        """Multiple log() calls append separate JSONL lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SDALE_LOG_DIR": tmpdir}):
                logger = EventLogger("edge")
                logger.log("dale_connect")
                logger.log("dale_run", command="ls")
                logger.log("dale_disconnect")

                lines = logger.log_file.read_text().strip().splitlines()
                self.assertEqual(len(lines), 3)

                events = [json.loads(line) for line in lines]
                self.assertEqual(events[0]["event"], "dale_connect")
                self.assertEqual(events[1]["event"], "dale_run")
                self.assertEqual(events[1]["command"], "ls")
                self.assertEqual(events[2]["event"], "dale_disconnect")

    def test_log_scrubs_secrets(self) -> None:
        """Secrets in extra fields are scrubbed before writing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {
                "SDALE_LOG_DIR": tmpdir,
                "ANTHROPIC_API_KEY": "sk-ant-super-secret",
            }):
                logger = EventLogger("edge")
                logger.log("dale_run", command="curl -H 'key: sk-ant-super-secret' http://api")

                content = logger.log_file.read_text()
                self.assertNotIn("sk-ant-super-secret", content)
                self.assertIn("[REDACTED:ANTHROPIC_API_KEY]", content)

    def test_timestamp_is_utc_iso8601(self) -> None:
        """Timestamps are in UTC ISO 8601 format ending with Z."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SDALE_LOG_DIR": tmpdir}):
                logger = EventLogger("edge")
                logger.log("dale_connect")

                event = json.loads(logger.log_file.read_text().strip())
                ts = event["ts"]
                self.assertTrue(ts.endswith("Z"))
                # Should be parseable
                from datetime import datetime
                parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                self.assertIsNotNone(parsed)

    def test_get_log_path_exists(self) -> None:
        """get_log_path returns the path when logs exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SDALE_LOG_DIR": tmpdir}):
                logger = EventLogger("edge")
                logger.log("dale_connect")

                result = EventLogger.get_log_path("edge")
                self.assertIsNotNone(result)
                self.assertTrue(result.is_file())

    def test_get_log_path_missing(self) -> None:
        """get_log_path returns None when no logs exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SDALE_LOG_DIR": tmpdir}):
                result = EventLogger.get_log_path("nonexistent")
                self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
