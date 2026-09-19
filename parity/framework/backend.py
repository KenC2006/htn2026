"""OpenRouter-backed AgentBackend for SwarmFlow's engine.

SwarmFlow (openjiuwen.agent_teams.workflow.engine) owns orchestration: parallel,
phases, journal/resume, budgets. This backend is the only place a model is
called. Workers get no tools here on purpose: they return a structured patch
and the Parity gate does all building and execution.
"""
from __future__ import annotations

import json
import os
import re

import httpx
from openjiuwen.agent_teams.workflow.engine import AgentBackend, AgentResult

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class OpenRouterBackend(AgentBackend):
    def __init__(self, *, default_model: str | None = None, max_tokens: int = 4000) -> None:
        super().__init__()
        self._base = os.environ["API_BASE"].rstrip("/")
        self._key = os.environ["API_KEY"]
        self._default_model = default_model or os.environ["MODEL_NAME"]
        self._max_tokens = max_tokens
        self._client = httpx.AsyncClient(timeout=120)
        self.calls: list[dict] = []  # per-call usage, read by the engine owner for cost reports

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        model = opts.get("model") or self._default_model
        messages = [{"role": "user", "content": prompt}]
        if schema_json is not None:
            messages.insert(0, {
                "role": "system",
                "content": "Reply with ONE JSON object and nothing else. It must conform to this JSON Schema:\n"
                + json.dumps(schema_json),
            })
        body = {"model": model, "messages": messages, "max_tokens": self._max_tokens, "temperature": 0}
        if schema_json is not None:
            body["response_format"] = {"type": "json_object"}

        resp = await self._client.post(
            f"{self._base}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json=body,
            timeout=opts.get("timeout") or 120,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0)
        self.calls.append({"label": opts.get("label"), "model": model, "usage": usage})
        # The backend is the ledgers' only writer (see AgentBackend.bind_budget).
        for ledger in (self.budget, self.workflow_budget):
            if ledger is not None and tokens:
                ledger.add(tokens)

        if schema_json is None:
            return AgentResult(text=text, tokens=tokens)
        match = _JSON_BLOCK.search(text)
        try:
            structured = json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            structured = None
        if structured is None:
            return AgentResult(text=text, tokens=tokens, skipped=True)
        return AgentResult(structured=structured, tokens=tokens)

    async def aclose(self) -> None:
        await self._client.aclose()


class TeamBackend(OpenRouterBackend):
    """Runs `agent(..., options={"member": name})` as a turn with that openJiuwen ReActAgent team member.

    Calls without a `member` fall back to a plain one-shot model call. Token billing for
    members is done by SwarmflowBudgetRail inside the agent loop, so it is not repeated here.
    """

    KNOWN_OPTIONS = frozenset({"member"})

    def bind_budget(self, budget) -> None:
        super().bind_budget(budget)
        from .team import TEAM
        TEAM.budget = budget

    def bind_workflow_budget(self, workflow_budget) -> None:
        super().bind_workflow_budget(workflow_budget)
        from .team import TEAM
        TEAM.workflow_budget = workflow_budget

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        member = opts.get("member")
        if not member:
            return await super().run(prompt, opts, schema_json)
        from .team import TEAM
        if schema_json is not None:
            prompt += ("\n\nHand in your result with your submit tool. (Only if you have no submit tool: make your final "
                       "message ONE JSON object conforming to: " + json.dumps(schema_json) + ")")
        text, tokens, submitted = await TEAM.ask(member, prompt)
        # Members hand results in through a submit_* tool; JSON in the final message is only a fallback.
        structured = submitted or _parse_json(text)
        if schema_json is not None and structured is None:
            more, t2, submitted = await TEAM.ask(member, "Nothing was handed in. Call your submit tool now with your final result.")
            text, tokens, structured = more, tokens + t2, submitted or _parse_json(more)
        self.calls.append({"label": opts.get("label"), "member": member, "model": TEAM.specs[member].model or self._default_model,
                           "usage": {"total_tokens": tokens}})
        if schema_json is None:
            return AgentResult(text=text, tokens=tokens)
        if structured is None:
            return AgentResult(text=text, tokens=tokens, skipped=True)
        return AgentResult(structured=structured, tokens=tokens)


def _parse_json(text: str):
    match = _JSON_BLOCK.search(text or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
