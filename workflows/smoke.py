"""Hour-2 framework check: two parallel workers, structured output, one dependent step."""
from swarmflow import agent, log, parallel, phase

META = {
    "name": "parity-smoke",
    "description": "Two parallel workers return structured patches; a third step depends on both.",
    "phases": [
        {"title": "Port", "detail": "Two independent workers"},
        {"title": "Compose", "detail": "Dependent step"},
    ],
}

PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "rust_code": {"type": "string"},
        "question": {"type": "string", "description": "A contract question for the steward, or empty"},
    },
    "required": ["rust_code", "question"],
}

P1 = "Port to safe Rust. Python: def bucket(ts: int, width: int) -> int: return ts // width * width. ts may be negative, width > 0."
P2 = "Port to safe Rust. Python: def checksum8(data: bytes) -> int: return sum(data) % 256."


async def run(args):
    phase("Port")
    a, b = await parallel([
        lambda: agent(P1, schema=PATCH_SCHEMA, label="worker-A"),
        lambda: agent(P2, schema=PATCH_SCHEMA, label="worker-B"),
    ])
    log(f"worker-A ok={a is not None} worker-B ok={b is not None}")
    phase("Compose")
    summary = await agent(
        "In one sentence, say whether this Rust handles negative ts like Python floor division:\n"
        + (a or {}).get("rust_code", "<missing>"),
        label="steward",
    )
    return {"A": a, "B": b, "steward": summary}
