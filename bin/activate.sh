#!/bin/bash
# clidesdale activate — self-injects into the shell environment.
# Sourced automatically by clide .bashrc if present at /workspace/clide-sdale/bin/activate.sh

_SDALE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export PYTHONPATH="${_SDALE_ROOT}:${PYTHONPATH:-}"

# Put bin/ on PATH so bare `sdale` command works
case ":${PATH}:" in
  *":${_SDALE_ROOT}/bin:"*) ;;
  *) export PATH="${_SDALE_ROOT}/bin:${PATH}" ;;
esac

# Set default sdale config so it works from any cwd
if [[ -z "${SDALE_CONFIG:-}" && -f "${_SDALE_ROOT}/sdale.json" ]]; then
  export SDALE_CONFIG="${_SDALE_ROOT}/sdale.json"
fi

unset _SDALE_ROOT
