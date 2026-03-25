"""Structured event logging for sdale.

Writes JSONL events compatible with the clide session event schema v1.
Each dale gets its own log file at ~/.sdale/logs/<dale>/events.jsonl.

Events are scrubbed for secrets before writing.
"""

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Env vars whose values should be redacted from logs
SECRET_VARS = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GITLAB_TOKEN",
    "TEDDY_API_KEY",
    "TEDDY_WEB_PASSWORD",
    "CLEM_WEB_SECRET",
    "SUPERVISOR_SECRET",
    "TTYD_PASS",
]


def _scrub_secrets(text: str) -> str:
    """Replace known secret values with redaction markers.

    Checks each env var in SECRET_VARS. If the var is set and its value
    appears in the text, replaces it with [REDACTED:<VAR_NAME>].

    Args:
        text: The string to scrub.

    Returns:
        The scrubbed string with secret values replaced.
    """
    for var in SECRET_VARS:
        val = os.environ.get(var, "")
        if val and len(val) > 4:
            text = text.replace(val, f"[REDACTED:{var}]")
    return text


def detect_operator() -> str:
    """Detect the current operator identity.

    Resolution order:
        1. ``$CLIDE_OPERATOR`` env var (set by boss when creating windows)
        2. tmux window name (e.g. ``clem``, ``clide``)
        3. ``"unknown"``

    Returns:
        The operator name string.
    """
    op = os.environ.get("CLIDE_OPERATOR", "").strip()
    if op:
        return op

    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{window_name}"],
            capture_output=True, text=True, timeout=2,
        )
        name = result.stdout.strip()
        if name and result.returncode == 0:
            return name
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "unknown"


class EventLogger:
    """JSONL event logger for a specific dale.

    Each instance is tied to a dale name and maintains a session ID
    for the duration of the CLI invocation. Events are appended to:
        ~/.sdale/logs/<dale>/events.jsonl

    Attributes:
        dale_name:  Name of the dale this logger is for.
        session_id: Unique session identifier (sdale-<dale>-<timestamp>).
        operator:   Detected operator identity.
        log_file:   Path to the JSONL log file.
    """

    def __init__(self, dale_name: str) -> None:
        """Initialize the logger for a dale.

        Creates the log directory if it doesn't exist. Detects the
        operator identity from env var or tmux window name.

        Args:
            dale_name: Name of the dale to log events for.
        """
        self.dale_name = dale_name
        self.session_id = f"sdale-{dale_name}-{int(time.time())}"
        self.operator = detect_operator()

        log_dir = Path(
            os.environ.get("SDALE_LOG_DIR", Path.home() / ".sdale" / "logs")
        )
        self.log_dir = log_dir / dale_name
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "events.jsonl"

    def log(self, event_type: str, **extra: str) -> None:
        """Write a single event to the log file.

        Builds a JSON object with base fields (event, ts, session_id,
        schema_version, dale) plus any extra key-value pairs, scrubs
        secrets, and appends it as a line to the JSONL file.

        Args:
            event_type: The event type string (e.g. "dale_run").
            **extra:    Additional fields to include in the event.
        """
        event = {
            "event": event_type,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": self.session_id,
            "schema_version": 1,
            "dale": self.dale_name,
            "operator": self.operator,
            **extra,
        }

        line = _scrub_secrets(json.dumps(event, separators=(",", ":")))

        with open(self.log_file, "a") as fh:
            fh.write(line + "\n")

    @staticmethod
    def get_log_path(dale_name: str) -> Optional[Path]:
        """Get the log file path for a dale, if it exists.

        Args:
            dale_name: Name of the dale.

        Returns:
            Path to the events.jsonl file, or None if no logs exist.
        """
        log_dir = Path(
            os.environ.get("SDALE_LOG_DIR", Path.home() / ".sdale" / "logs")
        )
        log_file = log_dir / dale_name / "events.jsonl"
        if log_file.is_file():
            return log_file
        return None
