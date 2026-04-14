"""Configuration loading for sdale.

Reads dale definitions from sdale.json, resolving in order:
  1. ./sdale.json (project root)
  2. ~/.config/sdale/sdale.json (user global)

Environment variables (SDALE_HOST, SDALE_USER, SDALE_KEY) override file config.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DaleConfig:
    """Configuration for a single dale (remote VPS workhorse).

    Attributes:
        name:     Friendly name for this dale (e.g. "edge").
        host:     Hostname or IP address of the remote machine.
        user:     SSH username.
        key:      Path to the SSH private key.
        session:  tmux session name on the remote machine.
        exclude:  List of rsync exclude patterns for sync operations.
    """

    name: str
    host: str
    user: str = ""
    key: str = ""
    session: str = ""
    exclude: list[str] = field(default_factory=lambda: ["node_modules", ".git"])
    log_dir: str = ""

    def __post_init__(self) -> None:
        """Apply defaults and expand paths after initialization."""
        if not self.session:
            self.session = f"sdale-{self.name}"
        # Expand ~ in key path
        if self.key:
            self.key = str(Path(self.key).expanduser())

    @property
    def activity_log_path(self) -> str:
        """Build the path for the remote activity log file.

        Uses log_dir from config if set, otherwise defaults to /tmp.
        The log file name is .sdale-<dale-name>.log.
        """
        base = self.log_dir or "/tmp"
        return f"{base}/.sdale-{self.name}.log"

    @property
    def ssh_dest(self) -> str:
        """Build the SSH destination string (e.g. 'user@host' or just 'host')."""
        if self.user:
            return f"{self.user}@{self.host}"
        return self.host

    @property
    def ssh_args(self) -> list[str]:
        """Build the base SSH argument list including the key flag."""
        args = ["-o", "StrictHostKeyChecking=accept-new"]
        if self.key:
            args.extend(["-i", self.key])
        # Use sdale's own known_hosts file
        kh = Path.home() / ".config" / "sdale" / "known_hosts"
        if kh.exists():
            args.extend(["-o", f"UserKnownHostsFile={kh}"])
        return args


def find_config_path() -> Optional[Path]:
    """Locate the sdale.json config file.

    Searches in order:
      1. $SDALE_CONFIG environment variable (explicit override)
      2. Walk up from cwd to filesystem root looking for sdale.json
      3. ~/.config/sdale/sdale.json (user global config)

    The walk-up search mimics how git finds .git — the agent doesn't
    need to ``cd`` to the exact project root.

    Returns:
        Path to the config file, or None if not found.
    """
    # Explicit override via env var
    env_config = os.environ.get("SDALE_CONFIG")
    if env_config:
        p = Path(env_config)
        if p.is_file():
            return p

    # Walk up from cwd
    current = Path.cwd().resolve()
    while True:
        candidate = current / "sdale.json"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break  # reached filesystem root
        current = parent

    # Global fallback
    global_config = Path.home() / ".config" / "sdale" / "sdale.json"
    if global_config.is_file():
        return global_config

    return None


def load_config() -> dict:
    """Load and parse the sdale.json config file.

    Returns:
        Parsed JSON as a dictionary.

    Raises:
        FileNotFoundError: If no sdale.json is found in any search path.
        json.JSONDecodeError: If the config file contains invalid JSON.
    """
    path = find_config_path()
    if path is None:
        raise FileNotFoundError(
            "No sdale.json found (searched cwd → root + ~/.config/sdale/sdale.json). "
            "Set SDALE_CONFIG=/path/to/sdale.json to override."
        )
    with open(path) as fh:
        return json.load(fh)


def get_dale(name: str) -> DaleConfig:
    """Load configuration for a specific dale by name.

    Merges dale-specific config with defaults, then applies
    environment variable overrides (SDALE_HOST, SDALE_USER, SDALE_KEY).

    Args:
        name: The dale name as defined in sdale.json under "dales".

    Returns:
        A fully resolved DaleConfig instance.

    Raises:
        FileNotFoundError: If no sdale.json is found.
        KeyError: If the named dale doesn't exist in the config.
    """
    raw = load_config()
    defaults = raw.get("defaults", {})
    dales = raw.get("dales", {})

    if name not in dales:
        available = ", ".join(dales.keys()) if dales else "(none)"
        raise KeyError(
            f"Dale '{name}' not found in config. Available: {available}"
        )

    dale_raw = dales[name]

    # Merge: dale-specific overrides defaults
    exclude = dale_raw.get("sync", {}).get(
        "exclude", defaults.get("exclude", ["node_modules", ".git"])
    )

    config = DaleConfig(
        name=name,
        host=dale_raw.get("host", ""),
        user=dale_raw.get("user", defaults.get("user", "")),
        key=dale_raw.get("key", defaults.get("key", "")),
        session=dale_raw.get("session", ""),
        exclude=exclude,
        log_dir=dale_raw.get("log_dir", defaults.get("log_dir", "")),
    )

    # Environment variable overrides (highest priority)
    if os.environ.get("SDALE_HOST"):
        config.host = os.environ["SDALE_HOST"]
    if os.environ.get("SDALE_USER"):
        config.user = os.environ["SDALE_USER"]
    if os.environ.get("SDALE_KEY"):
        config.key = os.environ["SDALE_KEY"]

    if not config.host:
        raise ValueError(f"Dale '{name}' has no host configured")

    return config


def list_dales() -> dict[str, dict]:
    """List all configured dales.

    Returns:
        The raw "dales" dictionary from sdale.json.

    Raises:
        FileNotFoundError: If no sdale.json is found.
    """
    raw = load_config()
    return raw.get("dales", {})
