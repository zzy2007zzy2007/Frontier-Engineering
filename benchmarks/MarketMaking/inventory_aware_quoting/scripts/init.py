# EVOLVE-BLOCK-START
from __future__ import annotations

from typing import Any, Mapping


def reset_policy() -> None:
    """Reset optional policy state between scenarios."""


def decide_quotes(observation: Mapping[str, Any]) -> dict[str, dict[str, float | int]]:
    """Return DUAL-market quote offsets and sizes for all three assets.

    The baseline uses a simple inventory skew: it makes additional buying less attractive
    when inventory is already positive, and makes selling more attractive to reduce it.
    """
    actions: dict[str, dict[str, float | int]] = {}
    for symbol, asset in observation["assets"].items():
        inventory = float(asset["dual_inventory"])
        base = 16.0
        skew = 0.35 * inventory
        bid_offset = min(60.0, max(2.0, base + skew))
        ask_offset = min(60.0, max(2.0, base - skew))
        actions[symbol] = {
            "bid_offset_bps": bid_offset,
            "ask_offset_bps": ask_offset,
            "bid_size": 2 if inventory < 45 else 0,
            "ask_size": 2 if inventory > -45 else 0,
        }
    return actions
# EVOLVE-BLOCK-END

