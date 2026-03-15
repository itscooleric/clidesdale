# clide-sdale

```text

   ██████ ██      ██ ██████  ███████ ██ ███████
  ██      ██      ██ ██   ██ ██      ██ ██
  ██      ██      ██ ██   ██ █████   ██ ███████   ───
  ██      ██      ██ ██   ██ ██         ██          \
   ██████ ███████ ██ ██████  ███████    ███████      \    ╱▔▔▔╲
                                                      ╲__╱ ● ● ╲
  ██████   █████  ██      ███████                        │  ▽  │
  ██   ██ ██   ██ ██      ██                              ╲───╱
  ██   ██ ███████ ██      █████                           ╱   ╲
  ██   ██ ██   ██ ██      ██                             ╱ ┃ ┃ ╲
  ██████  ██   ██ ███████ ███████                       ╱  ┃ ┃  ╲

  give your agent a VPS                          v0.1
  ────────────────────────────────────────────────────

  sdale connect edge
  sdale run edge "docker build -t app ."
  sdale output edge
  sdale sync edge ./src /srv/app

  ────────────────────────────────────────────────────
  python 3.10+  ·  zero dependencies  ·  dale! 🐴
```

Give your AI agent SSH access to a disposable VPS. It builds, tests, deploys, and breaks things — you watch via tmux. Dale!

## Why

AI agents in sandboxed containers (like [clide](https://github.com/itscooleric/clide)) can't run Docker, bind ports, or test infrastructure. But you don't want to give them the keys to production either.

**clide-sdale** is the middle ground: a throwaway VPS the agent can SSH into and go a little crazy on. You co-pilot via shared tmux sessions — observable, interruptible, disposable.

## The pattern

```
┌──────────────┐     SSH (ed25519)     ┌──────────────────┐
│  agent        │ ────────────────────▶ │  dale (VPS)      │
│  (sandboxed)  │                       │                  │
│               │  rsync code ────────▶ │  docker build    │
│  write code   │                       │  docker run      │
│  unit tests   │  ◀──── results ────── │  deploy          │
│  git          │                       │  break stuff     │
└──────────────┘                       └──────────────────┘
        │                                       │
        └──────── tmux co-dev session ──────────┘
                  human attaches and watches
```

### Core rules

1. **Always use tmux** — every remote command runs in a named tmux session. The human can attach at any time. No invisible background jobs.
2. **Rsync, don't clone** — code lives in the agent's sandbox. Sync it to the VPS for builds. Single source of truth.
3. **The VPS is disposable** — if the agent bricks it, reprovision. Keep provisioning scripted and repeatable.
4. **SSH key per agent** — each agent gets its own key pair. Revoke by removing the pubkey.

## Install

```bash
pip install .
# or run directly:
python -m sdale
```

Requires Python 3.10+. Zero external dependencies — stdlib only.

## Quick start

### 1. Provision a VPS

Any cheap VPS works. Install Docker and tmux:
```bash
apt-get update && apt-get install -y docker.io tmux
```

Or use a full bootstrap like [forge](https://github.com/itscooleric/forge) for Docker CE, Tailscale, UFW, and SSH hardening.

### 2. Generate + install SSH key

On the agent's machine:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/sdale -N "" -C "agent-sdale"
```

Add the pubkey to the VPS:
```bash
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
      "session": "build"
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
# Connect to a dale (creates tmux session)
sdale connect edge

# Run commands (human watches in tmux)
sdale run edge "docker build -t app ."
sdale run edge "docker run --rm app npm test"

# Read output
sdale output edge

# Sync code to the dale
sdale sync edge ./my-project /srv/app

# Check status
sdale status edge

# View audit log
sdale log edge

# Human attaches from their terminal
ssh deploy@vps-ip -t "tmux attach -t build"
```

## CLI reference

| Command | Description |
|---------|-------------|
| `sdale connect <dale>` | Create/reuse tmux session on a dale |
| `sdale run <dale> "<cmd>"` | Send a command to the dale's tmux session |
| `sdale output <dale> [-n N]` | Capture recent tmux pane output (default: 20 lines) |
| `sdale sync <dale> <src> [dst]` | Rsync local directory to the dale |
| `sdale status [dale]` | Show dale status (or list all) |
| `sdale list` | List configured dales |
| `sdale log <dale> [--full\|--since DUR]` | Show event log for a dale |
| `sdale disconnect <dale>` | Kill the tmux session |

## Logging

Every command is logged as structured JSONL to `~/.sdale/logs/<dale>/events.jsonl`, compatible with the [clide session event schema v1](https://github.com/itscooleric/clide/blob/main/docs/schema/session-events-v1.md).

```json
{"event":"dale_run","ts":"2026-03-15T04:30:12Z","session_id":"sdale-edge-1710473400","schema_version":1,"dale":"edge","command":"docker build -t app ."}
{"event":"dale_sync","ts":"2026-03-15T04:31:02Z","session_id":"sdale-edge-1710473400","schema_version":1,"dale":"edge","src":"./app","dst":"/srv/app","files":"14"}
```

Secret values (API keys, tokens) are automatically scrubbed before writing.

## Roadmap

See [issues](https://github.com/itscooleric/clide-sdale/issues) for the full backlog.

## Name

**clide-sdale** = [clide](https://github.com/itscooleric/clide)'s dale. A horse (Clydesdale → clide-sdale). Also Spanish for "dale!" — *go for it!* Because that's what you're telling your agent: here's a VPS, dale. 🐴
