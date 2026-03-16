"""Tests for sdale.config — configuration loading and DaleConfig."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sdale.config import DaleConfig, find_config_path, get_dale, list_dales, load_config


class TestDaleConfig(unittest.TestCase):
    """Tests for the DaleConfig dataclass."""

    def test_basic_creation(self) -> None:
        """DaleConfig stores all provided fields."""
        dale = DaleConfig(name="edge", host="203.0.113.10", user="deploy")
        self.assertEqual(dale.name, "edge")
        self.assertEqual(dale.host, "203.0.113.10")
        self.assertEqual(dale.user, "deploy")

    def test_default_session_name(self) -> None:
        """Session defaults to 'sdale-<name>' when not provided."""
        dale = DaleConfig(name="edge", host="203.0.113.10")
        self.assertEqual(dale.session, "sdale-edge")

    def test_custom_session_name(self) -> None:
        """Explicit session name is preserved."""
        dale = DaleConfig(name="edge", host="203.0.113.10", session="work")
        self.assertEqual(dale.session, "work")

    def test_ssh_dest_with_user(self) -> None:
        """ssh_dest returns 'user@host' when user is set."""
        dale = DaleConfig(name="edge", host="203.0.113.10", user="deploy")
        self.assertEqual(dale.ssh_dest, "deploy@203.0.113.10")

    def test_ssh_dest_without_user(self) -> None:
        """ssh_dest returns just the host when no user is set."""
        dale = DaleConfig(name="edge", host="203.0.113.10")
        self.assertEqual(dale.ssh_dest, "203.0.113.10")

    def test_ssh_args_with_key(self) -> None:
        """ssh_args includes -i flag when a key is configured."""
        dale = DaleConfig(name="edge", host="203.0.113.10", key="/tmp/test-key")
        args = dale.ssh_args
        self.assertIn("-i", args)
        self.assertIn("/tmp/test-key", args)
        self.assertIn("StrictHostKeyChecking=accept-new", " ".join(args))

    def test_ssh_args_without_key(self) -> None:
        """ssh_args omits -i when no key is configured."""
        dale = DaleConfig(name="edge", host="203.0.113.10")
        args = dale.ssh_args
        self.assertNotIn("-i", args)
        self.assertIn("StrictHostKeyChecking=accept-new", " ".join(args))

    def test_key_tilde_expansion(self) -> None:
        """Tilde in key path is expanded to home directory."""
        dale = DaleConfig(name="edge", host="203.0.113.10", key="~/.ssh/test")
        self.assertTrue(dale.key.startswith("/"))
        self.assertNotIn("~", dale.key)
        self.assertTrue(dale.key.endswith(".ssh/test"))

    def test_default_exclude_patterns(self) -> None:
        """Default exclude list contains node_modules and .git."""
        dale = DaleConfig(name="edge", host="203.0.113.10")
        self.assertEqual(dale.exclude, ["node_modules", ".git"])

    def test_custom_exclude_patterns(self) -> None:
        """Custom exclude patterns override the defaults."""
        dale = DaleConfig(
            name="edge", host="203.0.113.10", exclude=["dist", "*.pyc"]
        )
        self.assertEqual(dale.exclude, ["dist", "*.pyc"])


class TestFindConfigPath(unittest.TestCase):
    """Tests for find_config_path — config file resolution."""

    def test_finds_cwd_config(self) -> None:
        """Finds sdale.json in the current working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "sdale.json"
            config_path.write_text('{"dales": {}}')
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                result = find_config_path()
                self.assertEqual(result, config_path)

    def test_returns_none_when_missing(self) -> None:
        """Returns None when no config file exists anywhere."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                with patch("sdale.config.Path.home", return_value=Path(tmpdir)):
                    result = find_config_path()
                    self.assertIsNone(result)

    def test_cwd_takes_priority(self) -> None:
        """CWD config is preferred over user-global config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd_dir = Path(tmpdir) / "project"
            cwd_dir.mkdir()
            global_dir = Path(tmpdir) / "home" / ".config" / "sdale"
            global_dir.mkdir(parents=True)

            cwd_config = cwd_dir / "sdale.json"
            cwd_config.write_text('{"dales": {"cwd": {}}}')
            global_config = global_dir / "sdale.json"
            global_config.write_text('{"dales": {"global": {}}}')

            with patch("sdale.config.Path.cwd", return_value=cwd_dir):
                with patch("sdale.config.Path.home", return_value=Path(tmpdir) / "home"):
                    result = find_config_path()
                    self.assertEqual(result, cwd_config)


class TestLoadConfig(unittest.TestCase):
    """Tests for load_config — JSON parsing."""

    def test_raises_on_missing_config(self) -> None:
        """Raises FileNotFoundError when no config exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                with patch("sdale.config.Path.home", return_value=Path(tmpdir)):
                    with self.assertRaises(FileNotFoundError):
                        load_config()

    def test_parses_valid_json(self) -> None:
        """Parses a valid sdale.json and returns a dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "sdale.json"
            config_path.write_text(json.dumps({
                "dales": {"edge": {"host": "203.0.113.10"}},
                "defaults": {"key": "~/.ssh/sdale"},
            }))
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                result = load_config()
                self.assertIn("dales", result)
                self.assertIn("edge", result["dales"])

    def test_raises_on_invalid_json(self) -> None:
        """Raises JSONDecodeError on malformed config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "sdale.json"
            config_path.write_text("not valid json {{{")
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                with self.assertRaises(json.JSONDecodeError):
                    load_config()


class TestGetDale(unittest.TestCase):
    """Tests for get_dale — dale resolution with defaults and overrides."""

    def _write_config(self, tmpdir: str, config: dict) -> None:
        """Helper to write a config file in the given directory."""
        config_path = Path(tmpdir) / "sdale.json"
        config_path.write_text(json.dumps(config))

    def test_loads_basic_dale(self) -> None:
        """Loads a dale with minimal config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_config(tmpdir, {
                "dales": {"edge": {"host": "203.0.113.10", "user": "deploy"}}
            })
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                dale = get_dale("edge")
                self.assertEqual(dale.host, "203.0.113.10")
                self.assertEqual(dale.user, "deploy")
                self.assertEqual(dale.name, "edge")

    def test_merges_defaults(self) -> None:
        """Dale inherits values from defaults section."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_config(tmpdir, {
                "dales": {"edge": {"host": "203.0.113.10"}},
                "defaults": {"user": "deploy", "key": "/tmp/default-key"},
            })
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                dale = get_dale("edge")
                self.assertEqual(dale.user, "deploy")
                self.assertEqual(dale.key, "/tmp/default-key")

    def test_dale_overrides_defaults(self) -> None:
        """Dale-specific values take priority over defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_config(tmpdir, {
                "dales": {"edge": {"host": "203.0.113.10", "user": "admin"}},
                "defaults": {"user": "deploy"},
            })
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                dale = get_dale("edge")
                self.assertEqual(dale.user, "admin")

    def test_env_overrides_config(self) -> None:
        """Environment variables override file config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_config(tmpdir, {
                "dales": {"edge": {"host": "203.0.113.10", "user": "deploy"}}
            })
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                with patch.dict(os.environ, {"SDALE_HOST": "10.0.0.1", "SDALE_USER": "root"}):
                    dale = get_dale("edge")
                    self.assertEqual(dale.host, "10.0.0.1")
                    self.assertEqual(dale.user, "root")

    def test_raises_on_unknown_dale(self) -> None:
        """Raises KeyError for a dale name not in config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_config(tmpdir, {"dales": {"edge": {"host": "203.0.113.10"}}})
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                with self.assertRaises(KeyError) as ctx:
                    get_dale("nonexistent")
                self.assertIn("nonexistent", str(ctx.exception))
                self.assertIn("edge", str(ctx.exception))

    def test_raises_on_missing_host(self) -> None:
        """Raises ValueError when a dale has no host."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_config(tmpdir, {"dales": {"edge": {}}})
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                with self.assertRaises(ValueError):
                    get_dale("edge")

    def test_custom_exclude_from_sync(self) -> None:
        """Reads exclude patterns from dale's sync.exclude config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_config(tmpdir, {
                "dales": {"edge": {
                    "host": "203.0.113.10",
                    "sync": {"exclude": ["dist", "*.pyc"]},
                }}
            })
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                dale = get_dale("edge")
                self.assertEqual(dale.exclude, ["dist", "*.pyc"])


class TestListDales(unittest.TestCase):
    """Tests for list_dales — listing all configured dales."""

    def test_lists_all_dales(self) -> None:
        """Returns the raw dales dictionary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "sdale.json"
            config_path.write_text(json.dumps({
                "dales": {
                    "edge": {"host": "203.0.113.10"},
                    "staging": {"host": "203.0.113.20"},
                }
            }))
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                dales = list_dales()
                self.assertEqual(len(dales), 2)
                self.assertIn("edge", dales)
                self.assertIn("staging", dales)

    def test_returns_empty_when_no_dales(self) -> None:
        """Returns empty dict when config has no dales section."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "sdale.json"
            config_path.write_text(json.dumps({}))
            with patch("sdale.config.Path.cwd", return_value=Path(tmpdir)):
                dales = list_dales()
                self.assertEqual(dales, {})


if __name__ == "__main__":
    unittest.main()
