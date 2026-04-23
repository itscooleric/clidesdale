# clidesdale

```text
   ██████╗██╗     ██╗██████╗ ███████╗ ██╗ ███████╗
  ██╔════╝██║     ██║██╔══██╗██╔════╝ ╚═╝ ██╔════╝
  ██║     ██║     ██║██║  ██║█████╗       ███████╗     ───
  ██║     ██║     ██║██║  ██║██╔══╝       ╚════██║       \
  ╚██████╗███████╗██║██████╔╝███████╗    ███████║         \    ╱▔▔▔╲
   ╚═════╝╚══════╝╚═╝╚═════╝ ╚══════╝    ╚══════╝         ╲__╱  ● ● ╲
  ██████╗  █████╗ ██╗     ███████╗(███████╗)                   │  ▽  │
  ██╔══██╗██╔══██╗██║     ██╔════╝(██╔════╝)                    ╲───╱
  ██║  ██║███████║██║     █████╗  (███████╗)                    ╱   ╲
  ██║  ██║██╔══██║██║     ██╔══╝  (╚════██║)                   ╱ ┃ ┃ ╲
  ██████╔╝██║  ██║███████╗███████╗(███████║)                  ╱  ┃ ┃  ╲
  ╚═════╝ ╚═╝  ╚═╝╚══════╝╚══════╝(╚══════╝)

  give your agent a VPS                              v0.1
  ────────────────────────────────────────────────────────

  sdale connect edge
  sdale exec edge "docker build -t app ."
  sdale watch edge
  sdale push edge .env /srv/.env

  ────────────────────────────────────────────────────────
  python 3.10+  ·  zero dependencies  ·  dale! 🐴
```

Give your AI agent SSH access to a disposable VPS — and capture structured behavioral data about every interaction. Clidesdale is both an operations tool and a research instrument: it lets agents build, test, and deploy on real infrastructure while producing a complete JSONL audit trail of what they did, when, and under what constraints.

## Why this matters for research

AI agents in sandboxed containers (like [clide](https://github.com/itscooleric/clide)) can't run Docker, bind ports, or test infrastructure. Clidesdale gives them a real VPS to work with — and turns every SSH session into a data collection opportunity.

Every `sdale` command produces structured event data: what the agent ran, the exit code, which operator initiated it, the operating mode at the time, and the full output. The SSH boundary is a natural observation point — all agent-infrastructure interaction passes through it, making the data complete by construction.

## The pattern

```
┌───────────────┐     SSH (ed25519)     ┌──────────────────┐
│  agent        │ ────────────────────> │  dale (VPS)      │
│  (sandboxed)  │                       │                  │
│               │  sdale exec ────────> │  docker build    │
│  write code   │  sdale push ────────> │  docker run      │
│  unit tests   │  sdale sync ────────> │  deploy          │
│  git          │  <──── results ────── │  break stuff     │
└───────────────┘                       └──────────────────┘
        │               ▲                       │
        │               │ structured JSONL       │
        │               │ + activity logs        │
        │               │                       │
        └──────── sdale watch / clidestable ─────┘
                  human watches in real time
```

The SSH boundary is the observation point. Everything crossing it — commands, file transfers, mode transitions — is logged as structured events. The VPS is disposable; the data is not.

## Data & behavioral logging

Clidesdale produces two complementary data streams for every dale.

### Activity logs (human-readable)

Every `sdale exec` and `sdale run` appends commands and their output to a per-dale activity file on the remote host:

```
/opt/stacks/.sdale-<dale-name>.log
```

Watch in real time with `sdale watch <dale>` or from the [clidestable](https://github.com/itscooleric/clidestable) dashboard.

### JSONL audit log (structured)

Every interaction is also recorded as a structured event at:

```
~/.sdale/logs/<dale>/events.jsonl
```

Each line is a JSON object following the [clide session event schema v1](https://github.com/itscooleric/clide/blob/main/docs/schema/session-events-v1.md):

```json
{"event":"dale_exec","ts":"2026-03-15T04:30:12Z","session_id":"sdale-edge-1710473400","schema_version":1,"dale":"edge","operator":"amber","command":"docker build -t app .","exit_code":"0"}
{"event":"dale_push","ts":"2026-03-15T04:31:02Z","session_id":"sdale-edge-1710473400","schema_version":1,"dale":"edge","operator":"amber","src":".env","dst":"/srv/app/.env"}
{"event":"dale_mode","ts":"2026-03-15T04:35:00Z","session_id":"sdale-edge-1710473400","schema_version":1,"dale":"edge","operator":"amber","mode":"supervised"}
```

**Fields captured per event:**

| Field | Description |
|-------|-------------|
| `event` | Event type (`dale_exec`, `dale_run`, `dale_push`, `dale_connect`, `dale_mode`, ...) |
| `ts` | UTC timestamp (ISO 8601) |
| `session_id` | Unique session identifier (`sdale-<dale>-<epoch>`) |
| `schema_version` | Schema version (currently `1`) |
| `dale` | Target dale name |
| `operator` | Who initiated the action (detected from `$CLIDE_OPERATOR` or tmux window name) |
| `command` | The command string (for exec/run events) |
| `exit_code` | Process exit code (for exec events) |
| `mode` | Operating mode at time of event |

**Secret scrubbing** is automatic — values from known secret environment variables (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `GITHUB_TOKEN`, etc.) are replaced with `[REDACTED:<VAR_NAME>]` before any event is written to disk.

### Operating modes as experimental conditions

Clidesdale supports three operating modes per dale, creating natural experimental conditions for studying agent behavior under different autonomy levels:

| Mode | Behavior | Research lens |
|------|----------|---------------|
| `unrestricted` | Full shell access, no staging | Agent has complete autonomy — baseline capability measurement |
| `supervised` | Draft/approve cycle for mutations | Human-in-the-loop — how does oversight change agent strategy? |
| `locked` | Read-only, no execution | Observation only — what does the agent attempt when it cannot act? |

Modes can be set manually or auto-detected based on the owner's presence:

```bash
sdale mode edge                  # show current mode
sdale mode edge supervised       # set explicitly
sdale mode edge auto             # auto-detect from owner presence
```

Auto-detection logic: owner attached to tmux = `unrestricted`, owner on Tailscale but detached = `supervised`, owner offline = `locked`. Mode transitions are logged as `dale_mode` events in the JSONL audit trail.

## Install

```bash
pip install .
# or run directly:
python -m sdale
```

Python 3.10+. Zero external dependencies — stdlib only.

## Quick start

### 1. Provision a VPS

Any cheap VPS works. Install Docker and tmux:
```bash
apt-get update && apt-get install -y docker.io tmux
```

### 2. Generate + install SSH key

```bash
ssh-keygen -t ed25519 -f ~/.ssh/sdale -N "" -C "agent-sdale"
ssh-copy-id -i ~/.ssh/sdale.pub deploy@vps-ip
```

### 3. Configure `sdale.json`

```json
{
  "dales": {
    "edge": {
      "host": "203.0.113.10",
      "user": "deploy",
      "key": "~/.ssh/sdale",
      "session": "build",
      "mode": "supervised"
    }
  },
  "defaults": {
    "key": "~/.ssh/sdale",
    "exclude": ["node_modules", ".git"]
  }
}
```

See [`sdale.example.json`](sdale.example.json) for the full format.

### 4. Dale!

```bash
sdale connect edge                          # tmux session + activity log
sdale exec edge "docker build -t app ."     # run command, log everything
sdale run -w edge "make deploy"             # via tmux, wait for result
sdale push edge .env /srv/app/.env          # push a file
sdale sync edge ./my-project /srv/app       # rsync code
sdale watch edge                            # watch activity in real time
sdale log edge                              # view structured audit log
```

## CLI reference

| Command | Description |
|---------|-------------|
| `sdale connect <dale>` | Create/reuse tmux session, set up activity log |
| `sdale watch <dale>` | Tail agent activity in real time |
| `sdale exec <dale> "<cmd>"` | Run command via direct SSH (logged) |
| `sdale exec -e <dale> "<cmd>"` | Same, merging stderr into stdout |
| `sdale multi <dale> "c1" "c2"` | Multiple commands in one SSH round-trip |
| `sdale cat <dale> <path> [path...]` | Read remote files |
| `sdale health <dale>` | Connectivity + system status check |
| `sdale health -d <dale>` | Include Docker container listing |
| `sdale push <dale> <src> <dst>` | Copy file to the dale (scp) |
| `sdale pull <dale> <remote> [local]` | Copy file from the dale |
| `sdale run <dale> "<cmd>"` | Send command to tmux session |
| `sdale run -w <dale> "<cmd>"` | Send via tmux + wait + print output |
| `sdale output <dale> [-n N]` | Capture recent tmux pane output |
| `sdale sync <dale> <src> [dst]` | Rsync local directory to dale |
| `sdale status [dale]` | Show dale status (or list all) |
| `sdale list` | List configured dales |
| `sdale log <dale> [--full\|--since DUR]` | Show structured event log |
| `sdale mode <dale> [mode]` | Get or set operating mode |
| `sdale disconnect <dale>` | Kill the tmux session |

## Core rules

1. **Everything is logged** — commands, output, file transfers, mode changes. Human-readable activity logs on the dale, structured JSONL locally.
2. **Rsync, don't clone** — code lives in the agent's sandbox. Sync to the VPS for builds. Single source of truth.
3. **The VPS is disposable** — if the agent bricks it, reprovision. The data persists locally.
4. **SSH key per agent** — each agent gets its own ed25519 key pair. Revoke by removing the pubkey.

## Ecosystem

Clidesdale is part of the CLIDE ecosystem. The JSONL event data it produces feeds into the broader session event pipeline alongside container-level telemetry from clide itself.

| Project | What |
|---------|------|
| [clide](https://github.com/itscooleric/clide) | CLI Development Environment — sandboxed terminal for AI agents |
| **clidesdale** | SSH access to remote VPSes + structured behavioral logging |
| [clidestable](https://github.com/itscooleric/clidestable) | VPS-side server — dashboard, stall management, split terminal view |

## Name

**clidesdale** = [clide](https://github.com/itscooleric/clide)'s dale. A horse (Clydesdale -> clidesdale). Also Spanish for "dale!" — *go for it!* Because that's what you're telling your agent: here's a VPS, dale. 🐴
