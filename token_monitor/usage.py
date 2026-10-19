"""Read Hermes' canonical usage, with compatibility for OpenAI-shaped SDK responses.

Hermes publishes prompt_tokens inclusive of cache reads/writes. Reasoning tokens
are a subset of output_tokens, so neither detail bucket is added to the total.
Provider-specific native responses should be normalized by Hermes before dispatch.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _count(value: Any, *paths: tuple[str, ...]) -> int:
    for path in paths:
        current = value
        for part in path:
            current = _field(current, part)
        if current is not None:
            if type(current) is not int or current < 0:
                raise ValueError(f"{'.'.join(path)} must be a non-negative integer")
            return current
    return 0


@dataclass(frozen=True)
class UsageDelta:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int


def extract_usage(usage: Any, response: Any) -> UsageDelta | None:
    raw = usage if usage is not None else _field(response, "usage")
    if raw is None:
        return None
    inp = _count(raw, ("prompt_tokens",), ("input_tokens",))
    out = _count(raw, ("output_tokens",), ("completion_tokens",))
    total = _count(raw, ("total_tokens",))
    if _field(raw, "total_tokens") is None:
        total = inp + out
    if total <= 0:
        return None
    return UsageDelta(inp, out, total,
        _count(raw, ("cache_read_tokens",), ("cached_tokens",),
               ("prompt_tokens_details", "cached_tokens"), ("input_tokens_details", "cached_tokens")),
        _count(raw, ("cache_write_tokens",)),
        _count(raw, ("reasoning_tokens",), ("thinking_tokens",),
               ("completion_tokens_details", "reasoning_tokens"),
               ("output_tokens_details", "reasoning_tokens")))
