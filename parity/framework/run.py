"""Run a SwarmFlow script headless: python -m parity.framework.run <script.py> [--args '{"k": 1}']

Uses Huawei's SwarmFlow engine (run_workflow) with the OpenRouter backend.
The journal lives under runs/ so a re-run with --resume replays finished agents for free.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
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
    from ..paths import RUNS as runs
    runs.mkdir(parents=True, exist_ok=True)
    wf_args = json.loads(ns.args) if ns.args else None
    tag = (wf_args or {}).get("run_id") if isinstance(wf_args, dict) else None
    (runs / "_journals").mkdir(exist_ok=True)
    journal = runs / "_journals" / f"{Path(ns.script).stem}.{tag or 'run'}.{int(time.time())}.journal.json"  # fresh per process; resume is done by the engine from receipts
    backend = TeamBackend()
    spend0 = _spend()
    from openjiuwen.agent_teams.workflow.engine.errors import BudgetExhausted
    from .team import TEAM
    stopped = None
    try:
        result = await run_workflow(
            ns.script,
            args=wf_args,
            backend=backend,
            journal_path=str(journal),
            resume=str(journal) if ns.resume and journal.exists() else None,
            log_sink=lambda m: print(m, flush=True),
            workflow_budget=BudgetLedger(total=ns.token_limit) if ns.token_limit else None,
        )
    except (Exception, BudgetExhausted) as e:  # BudgetExhausted is a BaseException; most often the token limit; kept pieces are on disk and `--resume` continues from them
        result, stopped = None, f"{type(e).__name__}: {e}"
        print(f"run stopped early: {stopped}", flush=True)
    await asyncio.sleep(6)  # OpenRouter's usage counter lags the last calls by a few seconds
    spend = _spend()
    tokens = {m: TEAM.tokens(m) for m in TEAM.specs if TEAM.tokens(m)}
    print(json.dumps({"result": result, "stopped": stopped, "tokens_by_member": tokens, "calls": backend.calls, "member_restarts": TEAM.restarts,
                      "cost_usd": None if None in (spend, spend0) else round(spend - spend0, 6)}, indent=2, default=str))
    return 1 if stopped else 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("script")
    p.add_argument("--args")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--token-limit", type=int)
    sys.exit(asyncio.run(_main(p.parse_args())))


if __name__ == "__main__":
    main()
