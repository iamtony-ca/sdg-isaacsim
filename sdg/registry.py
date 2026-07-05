"""Minimal plugin registry: name -> class, per extension axis.

Each extension axis (scene builders, randomizers, sensors, writers) keeps its own
registry. Plugins self-register with the @register decorator; run_sdg looks them up
by the string keys used in the config. This is what makes "new task = new config":
config names a registered plugin, no core edits.
"""
from __future__ import annotations

from typing import Dict, Type

# one namespace per extension axis
_REGISTRIES: Dict[str, Dict[str, type]] = {
    "scene": {},
    "randomizer": {},
    "sensor": {},
    "writer": {},
}


def register(axis: str, name: str):
    """Class decorator: register `cls` under `name` in the given axis."""
    if axis not in _REGISTRIES:
        raise KeyError(f"unknown registry axis '{axis}' (have {list(_REGISTRIES)})")

    def _wrap(cls: type) -> type:
        table = _REGISTRIES[axis]
        if name in table:
            raise KeyError(f"'{name}' already registered in axis '{axis}'")
        table[name] = cls
        return cls

    return _wrap


def get(axis: str, name: str) -> type:
    table = _REGISTRIES.get(axis)
    if table is None:
        raise KeyError(f"unknown registry axis '{axis}'")
    if name not in table:
        raise KeyError(f"no '{name}' registered in axis '{axis}' (have {list(table)})")
    return table[name]


def available(axis: str):
    return sorted(_REGISTRIES.get(axis, {}))
