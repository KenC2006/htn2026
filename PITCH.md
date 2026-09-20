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
- Tester (not the workers' model) attacks every piece that passed: it invents inputs and runs them through both versions. Anything it finds becomes a permanent test. Agents working against each other, with plain code as the referee.
- Built on Huawei's SwarmFlow engine and openJiuwen agents.

## The demo moment

Python `-1 // 1000` is `-1`. Rust `-1 / 1000` is `0`. Show a worker get this wrong, the checker reject it with that exact input, the expert run the original, write the rule, and the fix go green. Then run the hidden test set once: 300 of 300.

## Numbers we can say out loud (toy example)

- Team: all pieces kept in every one of 20+ runs, about 3 cents a run on small open models.
- A fake that hardcodes the visible test answers: passes the fixed cases, then the tester agent breaks it on its first try (and the hidden test set alone catches it too: 196 of 300).
- A top model one-shot (Fable 5.1): passes, 300 of 300. Do not claim the team beats it on this example. The claim is that Parity tells you which translations are right.

## Words to use when talking (say the left, not the right)

| Say | Not |
|---|---|
| the checker | gate, verifier |
| the expert | contract steward |
| a rule | contract guidance, decision |
| a piece | chunk |
| thrown out and redone | stale, invalidated |
| hidden test set | locked evaluation |
| Python to Rust and C to Rust work end to end from your own file; TypeScript to ArkTS verifies on the HarmonyOS emulator and is in development | profiles, routes |
