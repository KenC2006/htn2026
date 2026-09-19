# Parity pitch

## One sentence

**Parity is a team of AI agents that rewrites your code in a new language and proves, piece by piece, that it still does exactly what the old code did.**

On a slide: *AI rewrites your code in a new language. Parity proves it still works the same.*

## If they ask "why not just ask Claude or GPT?"

You can, and Parity will check that too (`python -m parity check`). A top model one-shot passed: 300 of 300 on the hidden test set. A cheater that hardcoded the visible test answers looked identical and was caught: 196 of 300. The point is not who writes the code. It is that you know whether it is right.

## Ten seconds more (if they are still listening)

AI can translate code, but you cannot trust it: it looks right and is wrong on the edge cases. In Parity the agents never get to say "done". A checker that is plain code, not AI, runs the old and new versions on the same inputs and compares every output. Pass and it is kept for good. Fail and the agent gets the exact input that broke.

## Why it is a team and not one agent (Huawei, 30 seconds)

- Planner reads the code, decides what can be done in parallel, and flags where the two languages behave differently.
- Workers each rewrite one piece at the same time. They can compile, and they can ask the expert a question. They cannot see the tests.
- Expert (a different model) answers by running the original code on inputs it picks, then writes a rule every worker must follow.
- When the expert writes a new rule, work done under the old rule is thrown out and redone automatically. One agent's finding changes what the others do.
- Built on Huawei's SwarmFlow engine and openJiuwen agents.

## The demo moment

Python `-1 // 1000` is `-1`. Rust `-1 / 1000` is `0`. Show a worker get this wrong, the checker reject it with that exact input, the expert run the original, write the rule, and the fix go green. Then run the hidden test set once: 300 of 300.

## Numbers we can say out loud (toy example, 11 team runs, 8 single-agent runs)

- Team: all pieces accepted in 11 of 11 runs.
- Single agent doing the same job with the same tools: about half its runs had a wrong answer caught by the checker, and it used 2 to 3 times the tokens.
- Do not claim the team is "more correct" in the end. Both get there. The team gets there cheaper, and one bad piece never blocks the others.

## Words to use when talking (say the left, not the right)

| Say | Not |
|---|---|
| the checker | gate, verifier |
| the expert | contract steward |
| a rule | contract guidance, decision |
| a piece | chunk |
| thrown out and redone | stale, invalidated |
| hidden test set | locked evaluation |
| three language pairs: Python to Rust, C to Rust, TypeScript to ArkTS | profiles, routes |
