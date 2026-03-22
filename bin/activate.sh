#!/bin/bash
# clidesdale activate — self-injects into the shell environment.
# Sourced automatically by clide .bashrc if present at /workspace/clide-sdale/bin/activate.sh

export PYTHONPATH="/workspace/clide-sdale:${PYTHONPATH:-}"

# Set default sdale config so it works from any cwd
if [[ -z "${SDALE_CONFIG:-}" && -f "/workspace/clide-sdale/sdale.json" ]]; then
  export SDALE_CONFIG="/workspace/clide-sdale/sdale.json"
fi
