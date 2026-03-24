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

## Config resolution
sdale.json is found by (in order):
1. `$SDALE_CONFIG` env var (explicit override)
2. Walk up from cwd to filesystem root (like git finds .git)
3. `~/.config/sdale/sdale.json` (global fallback)

## Running
```bash
python -m sdale <command> <dale> [args...]
# or after pip install:
sdale <command> <dale> [args...]
# or if pip unavailable (common in containers):
PYTHONPATH=/path/to/clide-sdale python3 -m sdale <command> <dale> [args...]
```

## Commands
```bash
sdale connect <dale>              # create/reuse tmux session
sdale exec <dale> "command"       # direct SSH (no tmux)
sdale exec -e <dale> "command"    # same, merge stderr into stdout
sdale multi <dale> "c1" "c2"      # multiple commands, one SSH round-trip
sdale cat <dale> /path [/path2]   # read remote file(s)
sdale write <dale> /remote/path   # write stdin to remote file
sdale write <dale> /path --from f # write local file to remote path
sdale logs <dale> <container>     # view container logs (last 50 lines)
sdale logs <dale> <ctr> -f        # follow container logs (live tail)
sdale logs <dale> <ctr> --since 1h # logs from last hour
sdale health <dale>               # quick connectivity + system check
sdale health -d <dale>            # include Docker container listing
sdale push <dale> <src> <dst>     # scp file to dale
sdale pull <dale> <remote> [local]# scp file from dale
sdale run <dale> "command"        # send via tmux (observable)
sdale run -w <dale> "command"     # send via tmux + wait for output
sdale output <dale> [-n N]        # capture tmux pane output
sdale sync <dale> <src> [dst]     # rsync directory to dale
sdale watch <dale>                # attach to tmux session (live view)
sdale status [dale]               # show dale status
sdale list                        # list configured dales
sdale log <dale>                  # show event log
sdale disconnect <dale>           # kill tmux session
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
