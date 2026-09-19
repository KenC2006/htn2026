"""The bridge between SwarmFlow's engine and the team.

SwarmFlow (openjiuwen.agent_teams.workflow.engine) owns orchestration: parallel, phases, journal, budgets.
A workflow's `agent(..., options={"member": name})` call arrives here and becomes one turn with that
openJiuwen ReActAgent team member (see team.py). Token billing happens inside the agent loop, through
Huawei's SwarmflowBudgetRail, so it is not repeated here.
"""
from __future__ import annotations

import json
import os
import re

from openjiuwen.agent_teams.workflow.engine import AgentBackend, AgentResult

from .team import TEAM

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class TeamBackend(AgentBackend):
    KNOWN_OPTIONS = frozenset({"member"})

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []  # one entry per member turn, for cost reports

    def bind_budget(self, budget) -> None:
        super().bind_budget(budget)
        TEAM.budget = budget

    def bind_workflow_budget(self, workflow_budget) -> None:
        super().bind_workflow_budget(workflow_budget)
        TEAM.workflow_budget = workflow_budget

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        member = opts["member"]
        if schema_json is not None:
            prompt += ("\n\nHand in your result with your submit tool. (Only if you have no submit tool: make your final "
                       "message ONE JSON object conforming to: " + json.dumps(schema_json) + ")")
        text, tokens, submitted = await TEAM.ask(member, prompt)
        # Members hand results in through a submit_* tool; JSON in the final message is only a fallback.
        structured = submitted or _parse_json(text)
        if schema_json is not None and structured is None:
            more, t2, submitted = await TEAM.ask(member, "Nothing was handed in. Call your submit tool now with your final result.")
            text, tokens, structured = more, tokens + t2, submitted or _parse_json(more)
        self.calls.append({"label": opts.get("label"), "member": member,
                           "model": TEAM.specs[member].model or os.environ["MODEL_NAME"], "usage": {"total_tokens": tokens}})
        if schema_json is None:
            return AgentResult(text=text, tokens=tokens)
        if structured is None:
            return AgentResult(text=text, tokens=tokens, skipped=True)
        return AgentResult(structured=structured, tokens=tokens)

    async def aclose(self) -> None:
        pass


def _parse_json(text: str):
    match = _JSON_BLOCK.search(text or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
