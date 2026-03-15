# CLAUDE.md — clide-sdale

## What is clide-sdale
CLI for giving AI agents SSH access to throwaway VPSes (dales) with tmux co-dev sessions. The human can always watch and intervene.

A **dale** is a remote VPS workhorse. You configure dales, connect to dales, run commands on dales.

## Language
Python 3.10+ — stdlib only, zero dependencies.

## Structure
- `sdale/` — Python package
  - `cli.py` — argparse entry point, subcommand dispatch
  - `config.py` — sdale.json loading, DaleConfig dataclass
  - `remote.py` — SSH/tmux/rsync wrappers
  - `logger.py` — JSONL event logging (clide schema v1 compatible)
  - `__main__.py` — `python -m sdale` support
- `sdale.example.json` — example config (safe for public)
- `pyproject.toml` — packaging metadata

## Running
```bash
python -m sdale <command> <dale> [args...]
# or after pip install:
sdale <command> <dale> [args...]
```

## Rules
1. **No invisible remote work** — every remote command goes through a named tmux session
2. **Zero dependencies** — stdlib only (subprocess, json, argparse, pathlib, dataclasses)
3. **Will be made public** — keep the repo clean, no secrets, no hardcoded IPs, no personal details
4. **Full docstrings** — every module, class, and function gets a docstring
5. **Event logging** — every command is logged as JSONL, compatible with clide session-events-v1

## Repo
- GitHub: itscooleric/clide-sdale (private, will go public)
- Git email: itscooleric@users.noreply.github.com
