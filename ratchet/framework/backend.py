"""OpenRouter-backed AgentBackend for SwarmFlow's engine.

SwarmFlow (openjiuwen.agent_teams.workflow.engine) owns orchestration: parallel,
phases, journal/resume, budgets. This backend is the only place a model is
called. Workers get no tools here on purpose: they return a structured patch
and the Ratchet gate does all building and execution.
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
