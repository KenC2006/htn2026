# TypeScript to ArkTS in Parity

Status: the three-function `ts-arkts-core` profile (delivery steps 1-5 below)
is implemented and verified end-to-end on the real HarmonyOS 6.1.1 / API 24
wearable emulator, including a full autonomous SwarmFlow run
(`python -m parity run fixtures/telemetry-workbench/ts-arkts-core`): the
planner, workers, a steward clarification and an adversarial tester all ran
for real, all three chunks (`bucketStart`, `clampValue`, `summarizeBuckets`)
were accepted, and the single-use locked evaluation passed 146 of 146 hidden
cases (see `runs/arkts-agent-1/export/report.md`, gitignored). Automatic TS
onboarding (step 6, `python -m parity new <file.ts>`) is now implemented too;
see `docs/TS_ONBOARDING_PLAN.md` for its design and step 6 below for the
device proof.
Reference checkout: `37802f26cd53f6aba783bea41d437d9476bb26b7` (2026-09-19).
Scope: Python-to-Rust is the implementation reference, per the user's direction.

## Outcome

Add `fixtures/telemetry-workbench/ts-arkts-core/` as a Parity profile. Run the
original TypeScript under Node and the converted `.ets` functions inside the
official DevEco wearable emulator. Accept a chunk only when the existing gate
observes matching behavior. Retain Parity's planner, workers, steward, tester,
contract ledger, integration receipts, and final locked evaluation.

The first delivery supports a deliberately small set of synchronous, pure
functions. General package migration, ArkUI conversion, browser/Node API
replacement, npm dependency migration, and asynchronous/stateful APIs follow
only after this route works. The existing smoke test proves ArkTS execution;
it does not yet prove conversion or per-case behavioral parity.

## What the Python-to-Rust implementation actually does

| Reference | Existing behavior | ArkTS adaptation |
|---|---|---|
| `parity/scan_python.py`, `newproject.py` | Scan eligible Python functions, probe behavior, generate a profile | Later add a TS compiler-API scanner and profile generator; first hand-author a fixture |
| `parity/templates.py` | Frozen Python oracle, Rust harness, JSON observations | Frozen Node oracle, typed ArkTS harness, host transport adapter |
| `fixtures/telemetry-workbench/py-rust-batch/` | Three chunks, explicit semantic contracts and bounded inputs | Same directory and contract structure with `.ts` sources and `.ets` candidates |
| `parity/engine/gate.py` | Check provenance, allowlist, bans, hashes, build, inventory and exact observations | Reuse these stages; supply DevEco build and runtime commands |
| `parity/engine/tools.py` | Restricted tools; compile-only feedback; source probes; tester inputs | Supply ArkTS signatures and type definitions in `worker_notes`; preserve tool boundaries |
| `workflows/migrate.py` | Dependency ordering, parallel workers, repairs and serial integration | Reuse workflow; serialize device operations across host processes |
| `parity/engine/evaluate.py` | Single-use hidden evaluation of accepted code | Run hidden inputs on the actual ArkTS runtime too |

This checkout uses standalone `rustc` and JSON runner adapters, not the older
PROJECT.md proposal's PyO3/maturin hybrid application or per-worker git worktrees.
The old Ratchet notes are historical context, not the current implementation spec.
The gate does not implement every metric described in that old pitch (for example
coverage, reduced Python line counts, or benchmark non-regression).

## Migrated foundation

`experiments/arkts-smoke/` contains the active app, official DevEco setup script,
runner and handoff. Keep HarmonyOS 6.1.1 / API 24 and wearable support from the
verified project. Run from this repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\experiments\arkts-smoke\scripts\run-arkts-smoke.ps1
```

Require a fresh `ArkTSSmoke` log containing `ARKTS_SMOKE_PASS:10`. Use an already
running emulator, with `-Target` when more than one is attached. Do not revive
the standalone SDK, custom QEMU, or retired emulator repair scripts.

The local `build-profile.json5` and `signatures/` are ignored. Signing credentials
were not transferred: automatic approval review rejected moving private key
material into the sibling repository. A shareable `build-profile.example.json5`
has no signing material; the local profile starts from that example. Signing
Configs have since been configured in DevEco for this checkout (a fresh debug
cert/profile under `C:\Users\khann\.ohos\config\`); the smoke test and the
`ts-arkts-core` gate both build, sign and install successfully here.
The old checkout is retained as a backup. Existing Parity activation scripts
and dependency requirements remain authoritative; old Conda environments are
not relocatable artifacts and were not moved.

Two bugs surfaced by that first real signed build/install, now fixed:
`runners/signing.cjs` pointed at a `json5` module path
(`tools/hvigor/hvigor/node_modules/json5`) that does not exist in this DevEco
install; it now resolves `tools/hvigor/hvigor-ohos-plugin/node_modules/json5`.
`runners/target.py`'s `hdc install -r` failed with `install sign info
inconsistent` (code 9568332) when a bundle with the same name
(`com.ratchet.arkts.smoke`, shared with the smoke harness) was already
installed under a different debug certificate (e.g. from the old checkout);
it now uninstalls the bundle once and retries on that specific error.

## First profile and behavior contract

Use three small TS exports: `bucketStart(timestampMs, widthMs)`,
`clampValue(value, lower, upper)`, and `summarizeBuckets(timestamps, widthMs)`.
The last must call the accepted `bucketStart`; declare that dependency in its
manifest. Two independent first-level chunks and one dependent chunk exercise
the existing scheduling and integration path without a large library upfront.

Use finite, bounded numeric inputs and homogeneous arrays initially. Keep
integer calculations and sums within JavaScript's safe integer range. Specify
negative-timestamp floor behavior, exact boundary handling, empty arrays,
ordering, and invalid-width error codes before freezing the fixture. Represent
summary results as a declared JSON shape such as arrays of numeric pairs.
Freeze the TS implementation as the oracle; derive expected outputs by running
it, never by manually entering them or asking a model.

Specify explicit ArkTS classes/interfaces for harness DTOs and function
signatures. Include their definitions in worker notes because workers do not
have arbitrary access to the scaffold. Compile every placeholder and import
every candidate module through the frozen harness. A file that is never built
or called cannot count as converted.

Initially exclude undefined values, sparse arrays, NaN/infinities, signed-zero
distinctions, arbitrary object graphs, dynamic imports, filesystem/network
access, eval, and source-language fallback. Ordinary JSON cannot preserve all
of those distinctions. Extending support requires a versioned tagged wire
encoding and tests first; never silently coerce missing values to null or round
numbers to make a comparison pass. Unicode support needs explicit string
domains because the current domain validator defaults to a limited alphabet.

## Files to implement

```text
fixtures/telemetry-workbench/ts-arkts-core/
  profile.json
  chunks/T1.json, T2.json, T3.json
  contracts/numeric-v1.json, bucket-v1.json, clamp-v1.json
  legacy/*.ts
  runners/source.py, target.py, build.py, source_runner.cjs
  harness/                       # frozen DevEco app + DTOs + dispatcher
  target/BucketStart.ets, ClampValue.ets, SummarizeBuckets.ets
  cases.jsonl, named_cases.jsonl, gen_cases.py
  locked/cases.jsonl
  README.md
tests/arkts_known_good/target/*.ets
tests/test_arkts.py
```

Proposed profile commands, following CONTRACTS.md exactly:

```json
{
  "schema_version": 1,
  "profile": "ts-arkts-core",
  "languages": {"source": "TypeScript", "target": "ArkTS"},
  "run_source": ["python", "runners/source.py"],
  "build_target": ["python", "runners/build.py"],
  "run_target": ["python", "runners/target.py"],
  "verify_seconds": 300,
  "locked_cases": "locked/cases.jsonl"
}
```

This is an excerpt, not a runnable profile. Add frozen globs for the profile,
manifests, contracts, sources, harness, runners and every case/generator file.
Hide `legacy`, source build artifacts, visible/locked case sets and generators
using `oracle_paths`. Workers may write only their exact `target/*.ets` file.
Add route-specific import and dynamic-execution restrictions; Rust regexes do
not enforce ArkTS policy. Validate candidate imports structurally against a
small approved set; regex checks alone are not a security sandbox.

Use existing input_domain types (int, float, list[int], etc.) for the first
profile. Nested named object schemas would require domain-validator changes.
Do not reuse the Python fixture's expected answers as the TS oracle.

## Runner design and trust boundaries

1. **Source:** Resolve an explicitly configured Node and pinned TypeScript
   compiler. Compile original TS in an external temporary directory, run a
   frozen dispatcher, and write one standard observation per case. Do not
   depend on Node's version-specific native TS support. Source probes use this
   same runner. Keep compiled source out of candidate workspaces.
2. **Build:** Resolve DevEco's bundled tools by absolute path. Assemble a fresh
   app from the frozen harness and the accepted/candidate `.ets` files. Create
   the DevEco build directory outside the gate's candidate tree; keep only a
   small artifact reference inside it. Supply local signing through trusted
   host configuration, never through candidate files or model prompts.
3. **Execute:** `run_target` accepts `--cases`, `--out`, `--candidate` and uses
   the artifact from that exact candidate build. It locks the selected HDC
   target, installs the HAP, force-stops and launches the app, sends a batch of
   case inputs, and converts the app's replies to observation JSONL.
4. **Transport spike:** Before choosing a final transport, prove host-to-app
   input transfer on API 24. First try launch parameters carrying a small
   batch, consumed by EntryAbility. For larger batches use app-readable input
   files only after verifying HDC permissions; otherwise page launch batches.
   Keep cases out of compiled code so check_compile does not expose tests.
5. **Replies:** Begin with framed HiLog messages using a fresh run nonce,
   case_id, frame sequence and completion count. Collect only this run's
   frames; reject missing, duplicate, truncated, malformed or unexpected
   replies. Probe actual HiLog size limits and split large payloads. If this
   is unreliable, implement file transfer before scaling the fixture.
6. **Failure handling:** Distinguish declared API errors from compiler errors,
   process crashes, transport failures and timeouts. Never fabricate ok values
   after missing output. Record device id, toolchain versions, HAP hash, input
   digest and logs for diagnosis. Preserve artifacts/logs outside gate cleanup
   when needed, without exposing signing secrets or expected answers.

The smoke runner is a worked example of building/installing/launching only.
Its single constant success marker and global HiLog reset are insufficient for
the conversion runner. Only the trusted dispatcher emits observation frames;
candidate core modules must not import logging, platform I/O or source code.

## Existing-engine integration issues to resolve

- `gate.check` recursively copies the fixture, then deletes newly created
  files under its workspace. Never put DevEco module junctions or shared SDK
  links under that tree: traversal/cleanup must not reach the installed tools.
  External per-build staging also avoids copying caches for each verification.
- `stop_after_build` is used by check_compile. It must finish without device
  access or behavioral feedback. Build artifacts need bounded cleanup even
  when the subsequent runtime step never happens.
- Workers can verify concurrently, as can separate CLI processes. Use a
  cross-process lock keyed by HDC target around install/launch/capture, not
  merely the workflow's in-process lock. Bound lock waits and release on error.
- The current subprocess timeout applies separately to each command; it is
  not a total attempt deadline. Measure build/install/run durations before
  setting both profile and chunk limits. Ensure child Hvigor/HDC processes
  are terminated on timeout, with no abandoned device lock or log collector.
- The gate scrubs credential-named environment variables. Signing must work
  through a trusted ignored local file and must never appear in returned
  compiler logs. Do not weaken environment scrubbing to get signing working.
- Environment failures currently become build/test rejection in some paths.
  Add an explicit preflight before launching agents for SDK, signing and
  emulator availability. If structured infrastructure verdicts require engine
  changes, coordinate with its owner and preserve existing schema meanings.
- Reuse exact gate comparison only within the declared wire domain. Its
  Python value equality is not a universal JS equivalence checker (e.g. bool
  vs number). Enforce output shape/type in the trusted adapters, and add
  strict type comparison if extending to mixed-type results.
- ~~CLI `new`/`migrate` and console/export wording still assume Python/Rust.~~
  Resolved in step 6: `parity/cli.py`'s `new`, `find_source` and
  `project_name` route a `.ts` file to `newproject_ts.create_ts`/
  `scan_typescript.scan` instead of the Python pair; `run`/`check`/`watch`/
  `status`/`evaluate`/`export` needed no changes since they only read
  `profile.json`'s shape, which is identical either way.

## Delivery order and acceptance checks

Steps 1-6 are done and reverified in this checkout.

1. **Foundation: done.** Ran the copied smoke project from the new checkout. Recorded
   the real runtime marker (`ARKTS_SMOKE_PASS:10`); validated public template and ignored signing files.
2. **Transport + known-good port: done.** Hand-ported `bucketStart`; proved Node vs
   emulator observations for normal, negative, boundary and declared-error
   cases. Fresh-run correlation (nonce'd HiLog frames) and transport completeness
   verified before any AI involvement.
3. **Three-chunk fixture: done.** All three chunks, contracts, generated
   development cases (131 in `cases.jsonl`) and separately seeded locked cases
   (`locked/cases.jsonl`) are in place and frozen/scanned. The known-good
   candidates in `tests/arkts_known_good` pass on-device (`python -m parity
   check fixtures/telemetry-workbench/ts-arkts-core tests/arkts_known_good
   --no-tester`): 3 of 3 chunks kept, 146 of 146 hidden/locked cases match.
4. **Gate adversarial checks: done.** `tests/test_arkts.py` covers frozen-harness
   tampering, a policy violation from editing the harness, missing/duplicate/
   stale/truncated HiLog frames, an undeclared error code, a bool-vs-number
   mismatch, a malformed summary shape, an unexpected case id, cross-process
   device-lock contention, and that every generated case stays in its declared
   input domain. All existing test modules that do not require `openjiuwen`
   (`test_gate`, `test_new`, `tests.test_arkts`) still pass; `test_flow` needs
   the `openjiuwen`/SwarmFlow install (see step 5) so it was not rerun here.
   Host-only protocol tests run without DevEco; the `parity check` command
   above is the separate integration command that explicitly requires the
   emulator and does not silently report a pass.
5. **Agent workflow: done.** Ran existing SwarmFlow on this profile with a small
   explicit token budget (`--token-limit 300000`, run id `arkts-agent-1`,
   day-to-day cheap models from `env/secrets.env`). The planner ordered T1/T2
   in parallel then T3; a worker asked the steward one real clarification
   about Map iteration in ArkTS, which was recorded as decision `D-001` and
   applied; the adversarial tester ran dozens of extra inputs per chunk and
   found zero mismatches; all three chunks were accepted on first or second
   attempt with no gate rejections. `python -m parity evaluate arkts-agent-1`
   passed the single-use locked evaluation 146/146, and
   `python -m parity export arkts-agent-1` produced `migration.patch`,
   `receipts.json`, `decisions.json` and `report.md`. Needs
   `.venv-swarm` (`pip install workswarm==0.2.6`, brings in `openjiuwen`) and
   `env/secrets.env` with a real OpenRouter key; run `python -m parity doctor`
   first.
6. **TS onboarding: done.** `parity/scan_typescript.py` + `parity/ts_arkts/scan.cjs`
   statically scan one `.ts` file with the TypeScript compiler API (the same
   pinned compiler DevEco builds ArkTS with): named function declarations only,
   conservative purity rules (mirroring `scan_python.py`'s `IMPURE_*`), and a
   dependency closure over same-file helper calls, each with a human-readable
   `reason` when a function can't be migrated. `parity/newproject_ts.py::create_ts`
   reuses `newproject.py`'s language-agnostic range/case machinery
   (`rules_for`, `make_value`, `make_cases`, `suggest`) unchanged, runs the real
   `.ts` file under Node twice to confirm determinism and derive each
   function's real return shape and error codes (never guessed), and writes a
   project in the exact shape `CONTRACTS.md`/the gate expect. Per-project
   generation is down to three files (`runners/dispatch.json`,
   `entryability/EntryAbility.ets`, each `target/<fn>.ets` placeholder); every
   other runner (`common.py`, `build.py`, `target.py`, `signing.cjs`,
   `source.py`, `source_runner.cjs`, `policy.cjs`) and the whole DevEco app
   shell (`parity/ts_arkts/harness_template/`) are static, shared templates,
   generalized from the hand-authored `ts-arkts-core` fixture (see
   `docs/TS_ONBOARDING_PLAN.md`). `parity/cli.py`'s `new`/`find_source`/
   `project_name` now route `.ts` files to this pipeline while the Python
   route is untouched. Verified on the real emulator: generated a two-function
   project from a throwaway `.ts` file (`clampAll`, `shout`), then
   `python -m parity check <project> <candidates> --no-tester` on a
   hand-written correct candidate got both chunks **ACCEPTED** with **200 of
   200 hidden cases** matching, and a wrong candidate was correctly
   **REJECTED** with a real counterexample — proving the *generated* (not
   hand-authored) harness/runners build, sign, install and round-trip real
   observations on hardware. Along the way the differential check caught a
   genuine bug in a hand-written "good" candidate (an eager bounds check that
   fired even for an empty input array, unlike the original's per-element
   check), which is exactly the failure mode this whole route exists to catch.
   Two bugs fixed during that proof: the local debug signing certificate is
   issued for one fixed bundleName, so every generated project's `app.json5`
   reuses it (`com.ratchet.arkts.smoke` by default, `PARITY_ARKTS_BUNDLE` to
   override) instead of a derived per-project one; and the generic
   `common.py::validate_observation`'s numeric-shape check (copied from the
   fixture's `T1`/`T2`/`T3`-specific version, which only ever saw whole
   numbers) rejected legitimate fractional results until it was generalized to
   "finite JS number", not "whole number". Host-only tests:
   `tests/test_scan_typescript.py`, `tests/test_new_ts.py`. Not yet supported
   (see `docs/TS_ONBOARDING_PLAN.md`'s non-goals): folders/packages, arrow
   functions/classes, generics, npm dependencies, async code, `any`/union
   types beyond optional, tuple/object parameters, null/undefined return
   values.

No-model fixture validation commands after implementation:

```powershell
python -m parity scan fixtures/telemetry-workbench/ts-arkts-core
python -m parity check fixtures/telemetry-workbench/ts-arkts-core tests/arkts_known_good --no-tester --run-id arkts-known-good
python -m unittest tests.test_gate tests.test_flow tests.test_new tests.test_telemetry tests.test_arkts
```

The profile is done when all three functions execute as ArkTS on the official
emulator, preserve declared behavior across development and locked cases,
reject the deliberately wrong candidate, and export receipts plus the accepted
`.ets` files. Report actual counts and timings; do not promise a runtime speedup
or general TS compatibility from this small fixture.

## Language reference

The official [OpenHarmony TS-to-ArkTS migration guide](https://gitee.com/openharmony/docs/blob/master/zh-cn/application-dev/quick-start/typescript-to-arkts-migration-guide.md)
describes stricter typing and version-dependent interop rules. Use explicit
types and declared object shapes in the initial scaffold. Treat the installed
DevEco API 24 compiler and emulator as the authority for supported syntax;
do not mix experimental ArkTS language versions into this route. Recheck any
specific restriction before turning it into an automated rewrite rule.
