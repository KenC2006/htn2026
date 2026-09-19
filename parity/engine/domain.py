"""Is an input inside a piece's declared input range? Plain code: used for generated cases and to refuse the tester's out-of-range inputs."""
from __future__ import annotations

ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789      _-.,'"


def in_domain(rule: dict, x) -> str:
    """'' if x is allowed by the rule, else why not."""
    if x is None:
        return "" if rule.get("nullable") else "null is not allowed"
    kind = rule.get("type")
    if kind and kind.startswith("list["):
        if not isinstance(x, list) or len(x) > rule.get("max_items", 8):
            return f"must be a list of at most {rule.get('max_items', 8)} items"
        return next((w for w in (in_domain({**rule, "type": kind[5:-1], "nullable": False}, v) for v in x) if w), "")
    if kind == "bool":
        return "" if isinstance(x, bool) else "must be true or false"
    if kind == "str" or "max_len" in rule or (rule.get("choices") and isinstance(rule["choices"][0], str)):
        if not isinstance(x, str):
            return "must be text"
        if rule.get("choices") and x not in rule["choices"] and "max_len" not in rule:
            return f"must be one of {rule['choices']}"
        if len(x) > rule.get("max_len", 10**9):
            return f"is longer than {rule['max_len']}"
        if any(ch not in (rule.get("alphabet") or ALPHABET) for ch in x) and x not in (rule.get("choices") or []):
            return "uses characters outside the allowed alphabet"
        return ""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return "must be a number"
    if kind == "int" and not isinstance(x, int):
        return "must be a whole number"
    if "min" in rule and x < rule["min"] or "max" in rule and x > rule["max"]:
        return f"is outside [{rule.get('min')}, {rule.get('max')}]"
    return ""
