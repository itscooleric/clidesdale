# sdale conventions — drop-in for project CLAUDE.md files

> Copy the relevant sections into your project's CLAUDE.md so agents know how to work with VPSes.

## VPS work

All VPS operations use `sdale` (clidesdale CLI). Never run raw SSH or tmux commands directly.

### Quick reference

```bash
sdale connect <dale>               # create/reuse tmux session
sdale exec <dale> "command"        # direct SSH (no tmux, captures output)
sdale exec -e <dale> "command"     # same but merge stderr into stdout
sdale multi <dale> "c1" "c2"      # multiple commands, one SSH round-trip
sdale cat <dale> /path [/path2]    # read remote file(s)
sdale write <dale> /remote/path    # write stdin to remote file (no quoting issues)
sdale write <dale> /path --from f  # write local file to remote path
sdale logs <dale> <container>      # container logs (last 50 lines)
sdale logs <dale> <ctr> -f         # follow container logs
sdale info <dale>                  # system overview (OS, CPU, memory, disk)
sdale info <dale> -a               # include Docker, tools, network
sdale probe <dale>                 # network/DNS diagnostics
sdale health <dale> -d             # connectivity + Docker status
sdale push <dale> <src> <dst>      # scp file to dale
sdale pull <dale> <remote> [local] # scp file from dale
sdale sync <dale> <src> [dst]      # rsync directory to dale
sdale run <dale> "command"         # send via tmux (human can watch)
sdale output <dale> [-n N]         # read tmux output
```

### Command preferences

- **Prefer `sdale multi`** over chaining with `;` or `&&` — one SSH round-trip, formatted output.
- **Prefer `sdale exec -e`** over appending `2>&1` — cleaner allowlist patterns.
- **Prefer `sdale cat`** over `sdale exec "cat ..."` — handles multiple files, better errors.
- **Prefer `sdale write`** over heredocs or `echo |` piped through exec — avoids all quoting issues.
- **Prefer `sdale logs`** over `sdale exec "docker logs ..."` — proper stderr handling, --follow support.

### Key principles

1. **VPSes are disposable.** They can be rebuilt from scratch. Don't store irreplaceable state on them. Data that matters gets synced back or lives in persistent volumes.
2. **The human can always watch.** Every sdale session uses a named tmux session. The human can `sdale watch <dale>` at any time to see what the agent is doing.
3. **No invisible remote work.** All commands are logged as JSONL events with operator identity, timestamps, and dale name.
4. **Never SSH directly.** Use sdale for all remote operations. It handles SSH keys, known hosts, and connection parameters from `sdale.json`.
5. **Never store secrets on VPSes.** VPSes run untrusted agent code. Private keys, CA material, and signing keys stay on the trust anchor (bernard). VPSes get proxied for TLS.

### Config

sdale finds `sdale.json` by walking up directories (like git finds `.git`). Override with `SDALE_CONFIG=/path/to/sdale.json`.

### Activation

```bash
source /workspace/clide-sdale/bin/activate.sh
# Now `sdale` is on PATH and PYTHONPATH is set
```

### Dale naming

A **dale** is a named VPS configuration in `sdale.json`. Common dales:

| Dale | Purpose |
|------|---------|
| `edge` | Forge-edge VPS — clide, clem, cloperator development |
| `edge2` | Same host, separate tmux session — clem development |
| `core` | Forge-core VPS — HA, llamastable, production services |

### Operator identity

Every sdale command logs an `operator` field. Detection order:
1. `$CLIDE_OPERATOR` env var
2. tmux window name
3. `"unknown"`

This lets the boss window and dashboards track which operator ran what.
