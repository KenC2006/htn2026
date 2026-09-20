# TS onboarding: `parity new file.ts`

Status: implemented and verified on the real emulator (see
`docs/TS_TO_ARKTS_PLAN.md` step 6 for the device proof and the exact bugs it
caught/surfaced). Nothing in the existing `ts-arkts-core` fixture or its gate
integration changed shape. Scope: a single `.ts` file, named top-level
function declarations, numbers/strings/booleans/arrays of those. Folders,
classes, arrow functions and npm packages are out of scope for v1, same as
Python's own scanner started with one file's top-level `def`s.

## Outcome

`python -m parity new legacy/telemetry.ts` behaves like the Python route:
statically scan the file, print which functions qualify and why the rest
don't, settle each parameter's input range, run the real TypeScript file
(under Node, never guessed) on generated inputs to learn what it actually
returns and raises, and write a `projects/<name>/` folder in the exact shape
`CONTRACTS.md` and the gate already expect — `run_source`/`build_target`/
`run_target`, frozen globs, `chunks/*.json`, `contracts/*.json`, compiling
ArkTS placeholders, dev + locked cases. `parity run`/`check`/`evaluate`/
`export` need no changes: they only know profile.json's shape, which stays
the same. `cli.py`'s `new` and `find_source` gain a branch that reads `.ts`
files with this new pipeline instead of the Python one; nothing about the
Python path changes.

## Why this is smaller than Python → Rust looked

Rust needed a hand-written JSON codec (`harness/json.rs`) because Rust has no
native JSON. ArkTS/TypeScript already speaks JSON (`JSON.parse`/
`JSON.stringify`), so the generated harness needs no serializer: the
dispatcher parses the case, calls the function, and stringifies whatever
comes back. Rust also needed one `.rs` file compiled straight into a single
binary (`main.rs`); ArkTS needs a full DevEco app shell, but that shell
(`AppScope/`, `entry/build-profile.json5`, `hvigor/`, resource jsons) is
**identical across every generated project** — it is copied once, verbatim,
the same way Rust's `json.rs` is copied verbatim. Only three files are
generated per project: the dispatcher (`EntryAbility.ets`), the Node oracle
runner, and a `policy.cjs` import allowlist. Everything else (`common.py`,
`build.py`, `target.py`, `signing.cjs`, the app shell) is one more static,
frozen template, exactly like `templates.py` is for Rust today.

## Type mapping

TypeScript's declared types already match ArkTS's, so there is no Python→Rust
style renaming table. The only real decision is `number`, which TypeScript
does not split into int/float the way Python type hints do:

| TS declared type | `input_domain` type | ArkTS placeholder type | notes |
|---|---|---|---|
| `number` | `float` | `number` | a safe superset of int; TS has no int/float split. A per-parameter `int` opt-in (e.g. a JSDoc tag) is a plausible follow-up, not implemented |
| `string` | `str` | `string` | |
| `boolean` | `bool` | `boolean` | |
| `number[]` / `string[]` / `boolean[]` | `list[float\|str\|bool]` | same | one level of nesting for v1 |
| optional (`x?: T`) or `= default` | `nullable: true` unless the default itself narrows the range | `T \| null` in the CaseInput field, function keeps its own default | mirrors Python's `Param.optional`/`has_default` |

Return shapes are inferred from what the oracle actually returns (never from
the annotation alone), the same principle as Python's `shape_of`: run the
real function, look at the JSON values, and pick the narrowest shape tag that
holds all of them — `"number"`, `"string"`, `"boolean"`, or `"<tag>[]"`
nested arbitrarily deep (a general replacement for the fixture's
hand-written "pairs" check, which was really just `number[][]`). If the
outputs don't fit one shape, the function is skipped with that reason, same
as Python.

## Purity and support rules (mirrors `scan_python.py`'s `IMPURE_*`)

A named top-level `function` declaration qualifies when:
- it is not `async`/a generator, has no decorators, is not a class member or
  arrow function (v1 scans `ts.isFunctionDeclaration` nodes only),
- every parameter has an explicit supported type annotation (no `any`,
  `unknown`, generics, union types other than `T | undefined`/`T | null`,
  tuple types, or object/interface types),
- its body never references: `console`, `Math.random`, `Date`/`Date.now`,
  `fetch`, `setTimeout`/`setInterval`, `require`, `import` of anything other
  than a same-directory relative `.ts` file, `globalThis`, `process`,
  `eval`/`Function`, or module-level `let`/`var` it mutates (matches the
  fixture's existing `policy.cjs` forbidden-identifier list, reused instead
  of reinvented),
- it only calls: itself/other qualifying functions in the same file, or
  built-in `Math.*` pure functions (`floor`, `min`, `max`, `abs`, `round`,
  `ceil`, `sqrt`, `pow`), `Map`/`Set`/array/string built-ins.

Any violation produces one human-readable `reason` string, following each
function name it calls transitively (the same closure walk `scan_python.py`
does for helpers and module constants), exactly mirroring today's Python
scan output shape (`ok`/`reason`/`calls`/`needs`).

## Architecture (new files only)

```text
parity/ts_arkts/
  scan.cjs                 # TypeScript compiler API: parses one file, returns
                            # JSON {functions: [...], diagnostics: [...]}
  harness_template/        # static DevEco app shell, byte-identical to
                            # fixtures/telemetry-workbench/ts-arkts-core/harness
                            # minus entryability/EntryAbility.ets and the
                            # bundleName, which get filled in per project
parity/scan_typescript.py  # subprocess wrapper around scan.cjs; TSFunction
                            # dataclass parallel to scan_python.py's Function
parity/templates_arkts.py  # mostly static strings, like templates.py's Rust
                            # ones: common.py (validate_observation reads
                            # dispatch.json instead of hardcoding chunk names),
                            # build.py/target.py/signing.cjs/source.py (already
                            # generic in the hand-authored fixture, copied
                            # verbatim), source_runner.cjs/policy.cjs (now also
                            # static: both read dispatch.json themselves at
                            # runtime instead of being generated per project).
                            # Only two real generator functions:
                            #   entry_ability_ets(functions) -> str
                            #   placeholder_ets(name, params, returns) -> str
parity/newproject_ts.py    # create_ts(): scan -> rules_for (reused from
                            # newproject.py, it is language-agnostic) -> run
                            # the oracle via runners/source.py -> shape_of_ts
                            # -> write project folder
tests/test_scan_typescript.py
tests/test_new_ts.py
```

`dispatch.json` (written into each generated project's `runners/`, frozen) is
the one new piece of shared state: `{module, functions: {<export name>:
{file, params: [{name, type}], returns: "<shape tag>", errors: [codes],
allowedImports}}}`, keyed by **export name** (present on every case per
`CONTRACTS.md`), not `chunk_id` — this sidesteps any ordering dependency
between scanning and chunk-id assignment, unlike the hand-authored fixture's
`T1`/`T2`/`T3` dispatch. `templates_arkts.py` consumes it to generate the
three per-project files; the generic `common.py` copy reads it at runtime to
validate observation shape/error codes — a strict generalization of
`runners/common.py::validate_observation`, not a new mechanism — and
`source_runner.cjs`/`policy.cjs` read it too, so they are static, shared
templates instead of per-project generated files. The existing
`ts-arkts-core` fixture keeps working unchanged since it is hand-authored,
not generated, and its own `common.py` copy is untouched.

## `newproject_ts.create_ts()` steps

1. `scan_typescript.scan(file)` → `TSFunction` list; keep `f.ok`.
2. Reuse `newproject.rules_for`/`default_rule`/`make_value`/`make_cases`/
   `suggest` as-is (they only need `f.params`, `f.source`, `f.needs`; nothing
   Rust-specific lives in them). Add one small shim so `_default_value` reads
   the TS scanner's own reported default/optional flags instead of
   re-parsing Python AST.
3. Write `legacy/<File>.ts` (the original, verbatim, hidden via
   `oracle_paths` exactly like Python's `legacy/`).
4. Write all seven runner files (`source.py`, `common.py`, `build.py`,
   `target.py`, `signing.cjs`, `source_runner.cjs`, `policy.cjs`) verbatim
   from `templates_arkts.py` — all seven are static and identical across
   every generated project; the last two read `runners/dispatch.json`
   (written alongside them) at runtime instead of being generated per project.
5. Copy `harness_template/` verbatim into `harness/`, fill in
   `AppScope/app.json5`'s `bundleName` (fixed across every generated project;
   see "signing" below), and write the generated `EntryAbility.ets`.
6. For each qualifying function, in dependency order (same topological pass
   `newproject.create` already does): write a compiling placeholder
   `target/<Name>.ets` (`export function name(...): T { return <zero value
   for T>; }`, throwing declared errors is not required from a placeholder —
   it only has to compile and be wrong, matching Python's placeholder
   contract), a `chunks/<id>.json` manifest, and a `contracts/behavior-
   <name>.json`.
7. Run the real function through `runners/source.py` (Node + the pinned
   TypeScript compiler, exactly like the fixture's `source_runner.cjs`, but
   now importing whichever functions the project declares instead of a
   hardcoded three) twice on `dev + hidden` cases to confirm determinism,
   collect real outputs, and derive `returns`/`errors` per function the same
   way Python derives `shape_of`/`errors` today.
8. Write `profile.json` matching `ts-arkts-core`'s shape (`run_source`,
   `build_target`, `compile_target` with `--unsigned`, `run_target`,
   `preflight`, `frozen`, `oracle_paths`, `forbid_patterns`, `locked_cases`).
9. Compile-check the generated placeholders with DevEco's `--unsigned` build
   before declaring success, the same sanity check `newproject.create` does
   with `rustc` today (catches a bug in the generator itself, not in agent
   output).

## Two things the design missed until the device proof

- **Signing is bundleName-locked.** The local debug certificate
  (`runners/build.py::signing`) is issued for one fixed bundleName; DevEco's
  signed build fails outright if `AppScope/app.json5`'s bundleName doesn't
  match it exactly. A per-project bundleName (the original plan) doesn't
  work with a single local certificate, so every generated project reuses
  the same one as the hand-authored `ts-arkts-core`/smoke harness
  (`com.ratchet.arkts.smoke`, overridable via `PARITY_ARKTS_BUNDLE`) and
  relies on `target.py`'s existing uninstall-and-retry on `sign info
  inconsistent` for two different generated projects sharing a device.
- **"Number" isn't "whole number".** The fixture's hand-written
  `validate_observation` happened to only ever see integer outputs
  (timestamps, counts), so its numeric-shape check silently required
  `value == int(value)`. Copied naively into the generalized `common.py`,
  it rejected any real fractional return value from a generated project.
  Fixed to "finite JS number" before any generated project could pass a
  differential check with a non-integer result.

## What stays exactly as it is

- The gate (`parity/engine/gate.py`), the workflow (`workflows/migrate.py`),
  `parity run`/`check`/`evaluate`/`export`, and every existing test.
- The hand-authored `ts-arkts-core` fixture and its runners — they are not
  migrated to the generated form; they remain the hand-verified reference
  that proved the route end to end.
- `runners/common.py::device_lock`/`frames`/`read_cases` (already generic).

## Delivery order

1. **Scanner**: `scan.cjs` + `scan_typescript.py`, tested against a small
   hand-written `.ts` fixture with one pure function, one impure function
   (uses `Date.now`), one with an unsupported type (`any`), and a
   caller/helper pair — host-only, no DevEco build needed, just Node + the
   pinned `typescript` package already required for the `ts-arkts-core`
   oracle.
2. **Templates**: `templates_arkts.py` + `harness_template/`; unit-test the
   generator functions directly against a small hand-built `dispatch.json`
   and diff the result against what a human would write (close to today's
   fixture files).
3. **`newproject_ts.create_ts()`**: wire steps 1–9 above; test with
   `use_ai=False` (default ranges only, like `test_new.py`) against a
   two-function fixture (one calling the other), asserting chunk manifests,
   case domains, and that the generated scaffold at least parses.
4. **CLI routing**: `cli.py new`/`find_source` dispatch on `.ts` vs `.py`
   vs folder/module name; `parity new --list` and the qualify/reason
   printout work unchanged for both languages.
5. **Device proof**: generate a real project from a throwaway two-function
   `.ts` file, hand-write one correct and one wrong ArkTS candidate, and run
   `python -m parity check <project> <candidates> --no-tester` against the
   real emulator — proves the generated (not hand-authored) harness/runners
   actually build, sign, install and round-trip on hardware, the TS
   equivalent of `test_new.py`'s `rustc`-backed placeholder/good-candidate
   assertions.

## Non-goals for this pass

Folders/packages, arrow functions and classes, generics, npm dependencies,
async code, `any`/`unknown`/union types beyond optional, tuple/object
parameter types, AI-suggested ranges beyond what `newproject.suggest`
already asks for (no ArkTS-specific prompting yet). Each is a follow-up once
single-file function scanning is proven, matching how Python's own scanner
grew from one file to packages over time.
