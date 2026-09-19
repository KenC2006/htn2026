"""Run a SwarmFlow script headless: python -m ratchet.framework.run <script.py> [--args '{"k": 1}']

Uses Huawei's SwarmFlow engine (run_workflow) with the OpenRouter backend.
The journal lives under runs/ so a re-run with --resume replays finished agents for free.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from openjiuwen.agent_teams.workflow.engine import BudgetLedger, run_workflow

from .backend import OpenRouterBackend

ROOT = Path(__file__).resolve().parents[2]


async def _main(ns: argparse.Namespace) -> int:
    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    journal = runs / f"{Path(ns.script).stem}.journal.json"
    backend = OpenRouterBackend()
    result = await run_workflow(
        ns.script,
        args=json.loads(ns.args) if ns.args else None,
        backend=backend,
        journal_path=str(journal),
        resume=str(journal) if ns.resume and journal.exists() else None,
        log_sink=lambda m: print(m, flush=True),
        workflow_budget=BudgetLedger(total=ns.token_limit) if ns.token_limit else None,
    )
    print(json.dumps({"result": result, "calls": backend.calls}, indent=2, default=str))
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("script")
    p.add_argument("--args")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--token-limit", type=int)
    sys.exit(asyncio.run(_main(p.parse_args())))


if __name__ == "__main__":
    main()
