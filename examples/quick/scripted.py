"""The two scripted quick demos. See build.py for what is real and what is staged.

Each takes a real finished run, keeps three functions, and puts a failed first attempt in front of the real accepted code.
The wrong code is in quick/wrong/. It was run through the real checker (runs zz-wrong-*), and the rejection shown, the input
that broke it and both answers are copied from what the checker saved. Only the order of events and the expert's one-line
hint are written by hand.
"""
import json
import shutil
from pathlib import Path

from build import DEMO, HERE, RUNS, copy_run, cut, load, write


def _real_rejection(run: str, chunk: str, dest: str) -> dict:
    """The checker's own rejection event from a zz-wrong-* run, with the files the view reads for it copied across."""
    src = RUNS / run
    event = next(e for e in load(src / "events.jsonl") if e["type"] == "candidate.rejected" and e["chunk_id"] == chunk)
    stem = event["attempt_id"].replace(":", "-")
    for folder in ("verdicts", "observations"):
        (RUNS / dest / folder).mkdir(exist_ok=True)
    shutil.copy(src / "verdicts" / f"{stem}.json", RUNS / dest / "verdicts" / f"{stem}.json")
    if (src / "observations" / stem).exists():
        shutil.copytree(src / "observations" / stem, RUNS / dest / "observations" / stem, dirs_exist_ok=True)
    return event


def _ev(kind: str, chunk: str, actor: str, payload: dict, attempt: str | None = None) -> dict:
    return {"schema_version": 1, "type": kind, "profile": None, "chunk_id": chunk, "attempt_id": attempt, "actor": actor, "payload": payload}


def _failed_first_try(events: list[dict], chunk: str, path: str, wrong: str, rejection: dict, hint: str) -> list[dict]:
    """worker writes the wrong code -> it compiles -> the checker rejects it -> the expert reads the failure -> the real attempt follows."""
    at = next(i for i, e in enumerate(events) if e["type"] == "worker.started" and e["chunk_id"] == chunk)
    staged = [_ev("worker.started", chunk, "scheduler", {"label": f"worker-{chunk}-attempt0", "member": f"worker-{chunk}"}),
              _ev("tool.check_compile", chunk, f"worker-{chunk}", {"result": "BUILD_OK", "path": path, "content": wrong, "error": ""}),
              _ev("worker.wrote", chunk, f"worker-{chunk}", {"path": path, "content": wrong}),
              _ev("candidate.submitted", chunk, "verifier", {"files": [path]}, rejection["attempt_id"]),
              rejection,
              _ev("steward.consulted", chunk, "scheduler", {"why": rejection["payload"]["reason"], "case_id": rejection["payload"].get("case_id")}),
              _ev("expert.hint", chunk, "contract-steward", {"hint": hint})]
    return events[:at] + staged + events[at:]


def _compile_error_first(events: list[dict], chunk: str, path: str, wrong: str, error: str) -> list[dict]:
    """The worker's own compile check fails with the real compiler message, then its real (compiling) code follows."""
    at = next(i for i, e in enumerate(events) if e["type"] == "tool.check_compile" and e["chunk_id"] == chunk)
    staged = [_ev("tool.check_compile", chunk, f"worker-{chunk}", {"result": "REJECTED_BUILD", "path": path, "content": wrong, "error": error})]
    return events[:at] + staged + events[at:]


def c_demo() -> None:
    real = RUNS / "team-002318"
    copy_run(real, "quick-c")
    keep = ["F1", "F4", "F7"]                                   # crc16, intrev32, hashlittle
    events = cut(load(real / "events.jsonl"), keep)
    wrong = HERE / "wrong" / "c" / "target"
    events = _failed_first_try(events, "F1", "target/crc16.rs", (wrong / "crc16.rs").read_text(encoding="utf-8"),
                               _real_rejection("zz-wrong-c", "F1", "quick-c"),
                               "In the C, buf is `char`, which is signed on this compiler, and the C masks with & 0x00FF for that reason. "
                               "`b as i8 as u16` sign-extends every byte over 127 and the table index runs past 255. Keep the mask: ((crc >> 8) ^ (b as u16)) & 0x00FF.")
    built = next(e for e in load(RUNS / "zz-wrong-c" / "events.jsonl") if e["type"] == "candidate.rejected" and e["chunk_id"] == "F7")
    events = _compile_error_first(events, "F7", "target/hashlittle.rs", (wrong / "hashlittle.rs").read_text(encoding="utf-8"), built["payload"]["detail"])
    write("quick-c", events, "scripted replay: real code, real checker results, staged order", DEMO / ".parity" / "projects" / "redis", keep)


ARKTS_ERROR = ("error: 10605074 ArkTS Compiler Error: Destructuring variable declarations are not supported (arkts-no-destruct-decls) "
               "at entry/src/main/ets/core/SummarizeBuckets.ets:14:14\n"
               "> hvigor ERROR: Failed :entry:default@CompileArkTS...\nCOMPILE RESULT:FAIL {ERROR:2 WARN:1}\n> hvigor ERROR: BUILD FAILED in 7 s 756 ms")


def arkts_demo() -> None:
    real = RUNS / "team-000205"
    copy_run(real, "quick-arkts")
    keep = ["T1", "T2", "T3"]                                   # bucketStart, clampValue, summarizeBuckets
    events = cut(load(real / "events.jsonl"), keep)
    project = Path(load(real / "events.jsonl")[0]["payload"]["profile_dir"])
    wrong = HERE / "wrong"
    events = _failed_first_try(events, "T1", "target/BucketStart.ets", (wrong / "arkts-a" / "target" / "BucketStart.ets").read_text(encoding="utf-8"),
                               _real_rejection("zz-wrong-ark-a", "T1", "quick-arkts"),
                               "Math.trunc rounds toward zero, the original uses Math.floor, which rounds down. They only differ for negative "
                               "timestamps: floor(-11 / 10) is -2, trunc is -1. Use Math.floor.")
    events = _compile_error_first(events, "T3", "target/SummarizeBuckets.ets",
                                  (wrong / "arkts-b" / "target" / "SummarizeBuckets.ets").read_text(encoding="utf-8"), ARKTS_ERROR)
    write("quick-arkts", events, "scripted replay: real code, real device results, staged order", project, keep)
