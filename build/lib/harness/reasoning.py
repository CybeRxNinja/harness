"""Canonical reasoning levels + provider mapping + capability normalization."""
from __future__ import annotations

LEVELS = ["off", "low", "medium", "high", "max", "xhigh"]
_ORDER = {v: i for i, v in enumerate(LEVELS)}

# o-series style caps, gpt-5 style sets — normalized, never hard-fail.
MODEL_CAPS: dict[str, list[str]] = {
    "default": LEVELS,
}


def normalize(requested: str, model_id: str) -> str:
    requested = (requested or "medium").lower()
    if requested not in _ORDER:
        return "medium"
    low = model_id.lower()
    if any(k in low for k in ("gpt-4.1", "haiku-4-5", "fast", "nano", "flash")) and requested in ("max", "xhigh"):
        return "high"  # cheap/fast models: cap reasoning
    if "o1" in low or "o3" in low or "o4" in low:
        # o-series supports off..high
        return "high" if requested in ("max", "xhigh") else requested
    return requested


def provider_params(provider: str, level: str, model_id: str) -> dict:
    """Extra JSON params to merge into chat request. Best-effort per family."""
    lvl = normalize(level, model_id)
    p = provider.lower()
    if p in ("anthropic",):
        if lvl == "off":
            return {}
        budget = {"low": 1024, "medium": 4096, "high": 8192, "max": 16000, "xhigh": 32000}[lvl]
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}
    if p in ("openai", "openrouter", "opencode"):
        if lvl == "off":
            return {"reasoning_effort": "none"}
        return {"reasoning_effort": {"low": "low", "medium": "medium", "high": "high", "max": "high", "xhigh": "high"}[lvl]}
    if p in ("google", "gemini"):
        return {"thinking_level": lvl} if lvl != "off" else {}
    return {"reasoning": lvl} if lvl != "off" else {}
