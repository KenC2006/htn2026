"""Run a SwarmFlow script headless: python -m ratchet.framework.run <script.py> [--args '{"k": 1}']

Uses Huawei's SwarmFlow engine (run_workflow) with the OpenRouter backend.
The journal lives under runs/ so a re-run with --resume replays finished agents for free.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from openjiuwen.agent_teams.workflow.engine import BudgetLedger, run_workflow

from .backend import TeamBackend

ROOT = Path(__file__).resolve().parents[2]


def _spend() -> float | None:
    """Total spend on the key so far, from OpenRouter. Run cost = after - before (exact, includes tool-loop calls)."""
    try:
        import httpx
        r = httpx.get(os.environ["API_BASE"].rstrip("/") + "/key", headers={"Authorization": f"Bearer {os.environ['API_KEY']}"}, timeout=20)
        return float(r.json()["data"]["usage"])
    except Exception:
        return None


async def _main(ns: argparse.Namespace) -> int:
    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    journal = runs / f"{Path(ns.script).stem}.journal.json"
    backend = TeamBackend()
    spend0 = _spend()
    result = await run_workflow(
        ns.script,
        args=json.loads(ns.args) if ns.args else None,
        backend=backend,
        journal_path=str(journal),
        resume=str(journal) if ns.resume and journal.exists() else None,
        log_sink=lambda m: print(m, flush=True),
        workflow_budget=BudgetLedger(total=ns.token_limit) if ns.token_limit else None,
    )
    spend = _spend()
    print(json.dumps({"result": result, "calls": backend.calls,
                      "cost_usd": None if None in (spend, spend0) else round(spend - spend0, 6)}, indent=2, default=str))
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
