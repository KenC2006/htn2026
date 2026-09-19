#!/usr/bin/env bash
# Source this (". env/activate-swarm.sh") before any jiuwenswarm-* command.
# Each line below fixes a trap we hit during setup.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -W 2>/dev/null || pwd)"
export JIUWENSWARM_HOME="$ROOT"   # parent dir; WorkSwarm creates $ROOT/.jiuwenswarm. Unset => writes to ~/.jiuwenswarm
export PYTHONUTF8=1               # WorkSwarm prints Chinese; Windows cp1252 console crashes without this
export PYTHONIOENCODING=utf-8
[ -f "$ROOT/env/secrets.env" ] && set -a && . "$ROOT/env/secrets.env" && set +a
. "$ROOT/.venv-swarm/Scripts/activate"
echo "swarm env ready. JIUWENSWARM_HOME=$JIUWENSWARM_HOME  MODEL_NAME=${MODEL_NAME:-<unset>}"
