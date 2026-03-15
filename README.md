# clide-sdale

```text

   ██████ ██      ██ ██████  ███████
  ██      ██      ██ ██   ██ ██
  ██      ██      ██ ██   ██ █████    ───
  ██      ██      ██ ██   ██ ██        \
   ██████ ███████ ██ ██████  ███████    \    ╱▔▔▔╲
                                        ╲__╱ ● ● ╲
  ███████ ██████   █████  ██      ███████   │  ▽  │
  ██      ██   ██ ██   ██ ██      ██         ╲───╱
  ███████ ██   ██ ███████ ██      █████      ╱   ╲
       ██ ██   ██ ██   ██ ██      ██        ╱ ┃ ┃ ╲
  ███████ ██████  ██   ██ ███████ ███████  ╱  ┃ ┃  ╲

  give your agent a VPS                    v0.1
  ─────────────────────────────────────────────────
  
  agent (clide) ── SSH ──► throwaway VPS
       │                        │
       ▼                        ▼
  write code              build, test, deploy
  run local tests         break things freely
       │                        │
       ▼                        ▼
  tmux co-dev ◄──────────► human watches

  ─────────────────────────────────────────────────
  dale! 🐴
```

Give your AI agent SSH access to a disposable VPS. It builds, tests, deploys, and breaks things — you watch via tmux. Dale!

## Why

AI agents in sandboxed containers (like [clide](https://github.com/itscooleric/clide)) can't run Docker, bind ports, or test infrastructure. But you don't want to give them the keys to production either.

**clide-sdale** is the middle ground: a throwaway VPS the agent can SSH into and go a little crazy on. You co-pilot via shared tmux sessions — observable, interruptible, disposable.

## The pattern

```
┌──────────────┐     SSH (ed25519)     ┌──────────────────┐
│  agent        │ ────────────────────▶ │  throwaway VPS   │
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
ssh-copy-id -i ~/.ssh/sdale.pub user@vps-ip
```

### 3. Configure `sdale.json`

```json
{
  "targets": {
    "edge": {
      "host": "66.179.138.11",
      "user": "eric",
      "key": "~/.ssh/sdale",
      "session": "sdale"
    }
  }
}
```

### 4. Go

```bash
# Agent creates a co-dev session
sdale connect edge

# Agent runs commands (human watches in tmux)
sdale run edge "docker build -t myapp ."
sdale run edge "docker run --rm myapp npm test"

# Agent syncs code to VPS
sdale sync edge ./my-project /tmp/build

# Human attaches
ssh eric@66.179.138.11 -t "tmux attach -t sdale"
```

## Co-dev tmux workflow

**Agent creates/reuses session:**
```bash
ssh -i ~/.ssh/sdale user@host "tmux new-session -d -s sdale 'bash' 2>/dev/null || true"
```

**Agent sends commands into it:**
```bash
ssh -i ~/.ssh/sdale user@host "tmux send-keys -t sdale 'your-command' Enter"
```

**Agent reads output:**
```bash
ssh -i ~/.ssh/sdale user@host "tmux capture-pane -t sdale -p | tail -20"
```

**Human watches:**
```bash
ssh user@host -t "tmux attach -t sdale"
```

## Roadmap

See [issues](https://github.com/itscooleric/clide-sdale/issues) for the full backlog.

## Name

**clide-sdale** = [clide](https://github.com/itscooleric/clide)'s dale. A horse (Clydesdale → clide-sdale). Also Spanish for "dale!" — *go for it!* Because that's what you're telling your agent: here's a VPS, dale. 🐴
