# Parity: environment

Everything lives inside this folder. Nothing is installed globally or written to your home directory.

| Path | What | Python |
|---|---|---|
| `.venv-swarm/` | WorkSwarm 0.2.6 (orchestrator) + ast-grep 0.45 | 3.11 |
| `.venv-target/` | mlem + its test deps, Pydantic 1.10.26, bump-pydantic | 3.10 |
| `.overlay-pydantic2/` | Pydantic 2.13 + FastAPI, put in front via PYTHONPATH for "v2" runs | - |
| `.jiuwenswarm/` | WorkSwarm workspace, config, built-in skills, Swarm Skill validator | - |
| `target/mlem/` | pristine clone of iterative/mlem (the migration target) | - |
| `target/wt-codemod/` | git worktree: mlem after `bump-pydantic` only (ablation column 1) | - |
| `env/` | activate script, test runner, dependency lock, known baseline failures | - |

## Use
    . env/activate-swarm.sh                    # always first; sets JIUWENSWARM_HOME + UTF-8
    cp env/secrets.env.example env/secrets.env # then fill in API_BASE / API_KEY / MODEL_NAME
    jiuwenswarm-start                          # web UI on http://localhost:5173

    env/run-target-tests.sh target/mlem v1         # baseline
    env/run-target-tests.sh target/wt-codemod v2   # any worktree against Pydantic 2

## Measured numbers (2026-09-18)
| Tree | Pydantic | Result |
|---|---|---|
| target/mlem | 1.10.26 | 244 passed, 21 known env failures (deselected), 9 xfailed, 106 s |
| target/mlem | 2.13.5 | 0 run. Package fails to import: `BaseSettings` moved |
| target/wt-codemod | 2.13.5 | 0 run. Still fails to import: `pydantic.env_settings.InitSettingsSource` removed |

bump-pydantic refactored 38 of 118 files and left 7 TODO markers. Residual v1 API it did not touch:
`.dict(` 23, `.json(` 15, `.copy(` 35 (not all Pydantic), `parse_obj_as` 32, `__fields__` 16, `BaseSettings` 3, `root_validator` 2, `update_forward_refs` 2.

## Traps we already hit
1. WorkSwarm needs Python 3.11+. The default `python` here is 3.10. Use `.venv-swarm`.
2. Without `PYTHONUTF8=1`, `jiuwenswarm-init` crashes on Windows (prints Chinese to a cp1252 console).
3. Without `JIUWENSWARM_HOME`, merely importing `jiuwenswarm` creates `~/.jiuwenswarm`. The variable is the PARENT dir.
4. mlem is from 2023. Required pins: `isort<6`, `numpy<2`, `pandas<2`, `fastapi<0.100`, `pytest<8`, `boto3` matched to s3fs's botocore. Exact versions in `env/target-requirements.lock.txt`.
5. mlem's `tests/conftest.py` imports boto3, numpy, pandas, fastapi, sklearn, docker, s3fs, gcsfs, nbformat at top level. No test collects without them.
6. The `swarmflow` module is injected by the engine when it runs a script. It is not importable from a plain Python shell. Real code: `openjiuwen/agent_teams/workflow/engine/primitives.py`.

## SwarmFlow facts confirmed from the installed source
- Primitives: `agent`, `parallel`, `pipeline`, `map_parallel`, `phase`, `compact`, `human`, `agent_session`, `log`.
- `agent(..., options={"isolation": "worktree"})` is supported natively. Other option keys: label, phase, schema, model, timeout, agent_type.
- Template: `.jiuwenswarm/agent/workspace/skills/swarmskill-creator/templates/scripts/workflow.py.template`
- Validator: `.jiuwenswarm/agent/workspace/skills/swarmskill-creator/scripts/validate_swarmskill.py`

## Models and budget (OpenRouter, $40 hard cap, expires 2026-09-26)
The Huawei-issued key is an OpenRouter key. Base URL `https://openrouter.ai/api/v1`, OpenAI-compatible.
Check spend any time, free: `curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $API_KEY"`

| Role | Model | $/M in | $/M out | Note |
|---|---|---|---|---|
| Workers | `qwen/qwen3-coder-next` | 0.12 | 0.80 | Smoke-tested: wrote valid-looking PyO3 for $0.00015 |
| Reviewer, distiller | `moonshotai/kimi-k2.7-code` | 0.71 | 3.21 | Different family on purpose. Reasoning model: max_tokens >= 2000 |
| Worker fallback | `deepseek/deepseek-v4.1-flash` | 0.15 | 0.60 | Untested |
| Do NOT use on this key | `anthropic/claude-fable-5.1`, `openai/gpt-6-astra` | 10 | 50 | One full run would drain the cap |

Rough cost of one full run at worker prices (100 files, 2 attempts each, 20k in / 5k out per attempt): about $1.30. Same run on Fable: about $90.
The frontier single-agent baseline should run through someone's own Claude Code subscription, not this key.
Cache every model response to disk. It protects the budget and lets the demo replay offline.
