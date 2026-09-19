"""Team members: openJiuwen SDK ReActAgents with narrow, role-specific tools.

A member is a long-lived agent: same instance and conversation for the whole run, so it
remembers its earlier attempts and what it was told. The workflow registers members
(role prompt + model + tools); the SwarmFlow backend and the tools both reach them
through `Team.ask`, which is the single place a model is ever invoked.

Members never get shell or file tools. Everything they can touch is a function in
ratchet/engine/tools.py that the trusted engine executes on their behalf.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from openjiuwen.agent_teams.workflow.backends.budget_rail import SwarmflowBudgetRail
from openjiuwen.agent_teams.workflow.engine import BudgetLedger
from openjiuwen.core.foundation.tool import tool as make_tool
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@dataclass
class ToolSpec:
    name: str
    description: str
    func: Callable  # async or sync; type hints become the tool's schema


@dataclass
class MemberSpec:
    name: str                      # e.g. "worker-P2", "steward"
    role: str                      # "worker" | "steward" | ...
    system_prompt: str
    model: str | None = None       # None -> MODEL_NAME
    tools: list[ToolSpec] = field(default_factory=list)
    max_iterations: int = 8
    serial: bool = False           # one question at a time (the steward)


class Team:
    def __init__(self) -> None:
        self.specs: dict[str, MemberSpec] = {}
        self._agents: dict[str, ReActAgent] = {}
        self._rails: dict[str, SwarmflowBudgetRail] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.budget = BudgetLedger()
        self.workflow_budget: BudgetLedger | None = None
        self.run_tag = "run"
        self.outbox: dict[str, dict] = {}   # member -> result handed in through a submit_* tool

    def register(self, spec: MemberSpec) -> None:
        self.specs[spec.name] = spec

    def reset(self, run_tag: str) -> None:
        self.specs.clear(), self._agents.clear(), self._rails.clear(), self._locks.clear(), self.outbox.clear()
        self.run_tag = run_tag

    def tokens(self, member: str) -> int:
        rail = self._rails.get(member)
        return rail.call_tokens if rail else 0

    async def _agent(self, member: str) -> ReActAgent:
        if member in self._agents:
            return self._agents[member]
        spec = self.specs[member]
        cfg = ReActAgentConfig().configure_model_client(
            provider="OpenAI", api_key=os.environ["API_KEY"], api_base=os.environ["API_BASE"],
            model_name=spec.model or os.environ["MODEL_NAME"]).configure_max_iterations(spec.max_iterations)
        cfg.prompt_template = [{"role": "system", "content": spec.system_prompt}]
        uid = f"{self.run_tag}.{member}"
        agent = ReActAgent(AgentCard(id=uid, name=member, description=spec.role)).configure(cfg)
        for ts in spec.tools:
            t = make_tool(ts.func, name=ts.name, description=ts.description)
            t.card.id = f"{uid}.{ts.name}"
            agent.ability_manager.add(t.card)
            if not Runner.resource_mgr.get_tool(tool_id=t.card.id):
                Runner.resource_mgr.add_tool(t)
        # Huawei's own rail: bills real usage per model call and stops the agent when the run's cap is hit.
        rail = SwarmflowBudgetRail(self.budget, self.workflow_budget)
        await agent.register_rail(rail)
        self._agents[member], self._rails[member] = agent, rail
        return agent

    async def ask(self, member: str, query: str) -> tuple[str, int, dict | None]:
        """One turn with a member. Returns (final text, tokens this turn used, what it handed in via a submit tool).

        Turns with the same member never overlap, so the outbox entry always belongs to this turn.
        """
        agent = await self._agent(member)
        async with self._locks.setdefault(member, asyncio.Lock()):
            self.outbox.pop(member, None)
            before = self.tokens(member)
            out = await agent.invoke({"query": query, "conversation_id": f"{self.run_tag}.{member}"})
            text = out.get("output", "") if isinstance(out, dict) else str(out)
            return text or "", self.tokens(member) - before, self.outbox.pop(member, None)


TEAM = Team()
