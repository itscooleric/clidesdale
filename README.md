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

Give your AI agent SSH access to a disposable VPS. It builds, tests, deploys, and breaks things — you watch via activity logs. Dale!

## Why

AI agents in sandboxed containers (like [clide](https://github.com/itscooleric/clide)) can't run Docker, bind ports, or test infrastructure. But you don't want to give them the keys to production either.

**clidesdale** is the middle ground: a throwaway VPS the agent can SSH into and go a little crazy on. You co-pilot via activity logs and shared tmux sessions — observable, interruptible, disposable.

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
        │                                       │
        │          activity logs (.sdale-*.log)  │
        └──────── sdale watch / clidestable ─────┘
                  human watches in real time
```

### Core rules

1. **Everything is logged** — `sdale exec` and `sdale run` log all commands + output to activity files. The human can `sdale watch` or use [clidestable](https://github.com/itscooleric/clidestable) to see everything in real time.
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
# Connect to a dale (creates tmux session + activity log)
sdale connect edge

# Run commands directly (captured in activity log)
sdale exec edge "docker build -t app ."
sdale exec edge "docker run --rm app npm test"

# Or via tmux (observable + wait for completion)
sdale run -w edge "make deploy"

# Push a config file
sdale push edge .env /srv/app/.env

# Sync code to the dale
sdale sync edge ./my-project /srv/app

# Watch agent activity in real time
sdale watch edge

# Check status / view audit log
sdale status edge
sdale log edge
```

## CLI reference

| Command | Description |
|---------|-------------|
| `sdale connect <dale>` | Create/reuse tmux session, set up activity log |
| `sdale watch <dale>` | Watch agent activity in real time (tails activity log) |
| `sdale exec <dale> "<cmd>"` | Run command via direct SSH (no tmux, good for scripting) |
| `sdale push <dale> <src> <dst>` | Copy a single file to the dale via scp |
| `sdale run <dale> "<cmd>"` | Send command to the dale's tmux session (observable) |
| `sdale run -w <dale> "<cmd>"` | Send via tmux + wait for completion, print output |
| `sdale output <dale> [-n N]` | Capture recent tmux pane output (default: 20 lines) |
| `sdale sync <dale> <src> [dst]` | Rsync local directory to the dale |
| `sdale status [dale]` | Show dale status (or list all) |
| `sdale list` | List configured dales |
| `sdale log <dale> [--full\|--since DUR]` | Show event log for a dale |
| `sdale disconnect <dale>` | Kill the tmux session |

## Observability

### Activity logs
`sdale exec` and `sdale run` write all commands and output to activity log files on the dale:

```
/opt/stacks/.sdale-<dale-name>.log
```

Watch them in real time with `sdale watch <dale>` or from [clidestable](https://github.com/itscooleric/clidestable)'s web dashboard.

### Audit log (JSONL)
Every command is also logged as structured JSONL to `~/.sdale/logs/<dale>/events.jsonl`, compatible with the [clide session event schema v1](https://github.com/itscooleric/clide/blob/main/docs/schema/session-events-v1.md).

```json
{"event":"dale_exec","ts":"2026-03-15T04:30:12Z","session_id":"sdale-edge-1710473400","schema_version":1,"dale":"edge","command":"docker build -t app .","exit_code":"0"}
{"event":"dale_push","ts":"2026-03-15T04:31:02Z","session_id":"sdale-edge-1710473400","schema_version":1,"dale":"edge","src":".env","dst":"/srv/app/.env"}
```

Secret values (API keys, tokens) are automatically scrubbed before writing.

## Ecosystem

| Project | What |
|---------|------|
| **clidesdale** | This CLI — gives agents VPS access |
| [clidestable](https://github.com/itscooleric/clidestable) | VPS-side server — dashboard, stall management, split terminal view |
| [clide](https://github.com/itscooleric/clide) | CLI Development Environment — sandboxed terminal for AI agents |

## Roadmap

See [issues](https://github.com/itscooleric/clidesdale/issues) for the full backlog.

## Name

**clidesdale** = [clide](https://github.com/itscooleric/clide)'s dale. A horse (Clydesdale → clidesdale). Also Spanish for "dale!" — *go for it!* Because that's what you're telling your agent: here's a VPS, dale. 🐴
