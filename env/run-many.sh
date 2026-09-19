#!/usr/bin/env bash
# Run a workflow N times in parallel: env/run-many.sh <workflow.py> <profile_dir> <run_prefix> <n> [token_limit]
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" && . env/activate-swarm.sh >/dev/null
mkdir -p runs
for i in $(seq 1 "$4"); do
  id="$3-$i"; rm -rf "runs/$id" "runs/$id.out"
  python -m ratchet.framework.run "$1" --args "{\"profile_dir\": \"$2\", \"run_id\": \"$id\"}" --token-limit "${5:-400000}" > "runs/$id.out" 2>&1 &
done
wait
for i in $(seq 1 "$4"); do echo "== $3-$i"; python env/show-run.py "$3-$i" | head -2; done
