"""Config loading/validation: YAML -> RunConfig dataclasses.

Pure Python (no Isaac import), so it can be loaded/tested without the simulator.
Keep this declarative and object-agnostic — objects are referenced only by `obj_id`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml  # PyYAML ships with Isaac's python; also common in venvs
except ImportError as e:  # pragma: no cover
    raise ImportError("PyYAML required: pip install pyyaml (or use /isaac-sim/python.sh)") from e


@dataclass
class RunMeta:
    name: str = "run"
    num_frames: int = 10
    seed: int = 0
    headless: bool = True
    output_dir: str = "datasets/run"


@dataclass
class ObjectSpec:
    obj_id: str
    count: int = 1
    physics: Dict[str, Any] = field(default_factory=dict)
    semantic: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SensorSpec:
    name: str = "cam0"
    type: str = "ideal"
    resolution: List[int] = field(default_factory=lambda: [1280, 720])
    intrinsics: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)  # e.g. realsense_depth params


@dataclass
class RunConfig:
    run: RunMeta
    scene: Dict[str, Any]
    objects: List[ObjectSpec]
    randomizers: List[Dict[str, Any]]
    sensors: List[SensorSpec]
    annotators: Dict[str, Any]
    writer: Dict[str, Any]
    raw: Dict[str, Any] = field(default_factory=dict)  # original dict, snapshotted into output


def _expand_vars(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve ${run.name} style references in string values (shallow, one pass)."""
    name = cfg.get("run", {}).get("name", "run")
    mapping = {"run.name": name}

    def sub(v):
        if isinstance(v, str):
            for k, val in mapping.items():
                v = v.replace("${" + k + "}", str(val))
            return v
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [sub(x) for x in v]
        return v

    return sub(cfg)


def load_config(path: str) -> RunConfig:
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    raw = _expand_vars(raw)

    run = RunMeta(**raw.get("run", {}))
    objects = [ObjectSpec(**o) for o in raw.get("objects", [])]
    sensors = []
    for s in raw.get("sensors", []):
        known = {k: s[k] for k in ("name", "type", "resolution", "intrinsics") if k in s}
        extra = {k: v for k, v in s.items() if k not in known}
        sensors.append(SensorSpec(**known, extra=extra))

    cfg = RunConfig(
        run=run,
        scene=raw.get("scene", {}),
        objects=objects,
        randomizers=raw.get("randomizers", []),
        sensors=sensors,
        annotators=raw.get("annotators", {}),
        writer=raw.get("writer", {"format": "generic"}),
        raw=raw,
    )
    _validate(cfg)
    return cfg


def _validate(cfg: RunConfig) -> None:
    if cfg.run.num_frames < 1:
        raise ValueError("run.num_frames must be >= 1")
    if not cfg.sensors:
        raise ValueError("at least one sensor is required")
    for o in cfg.objects:
        if not o.obj_id:
            raise ValueError("each object needs an obj_id")


def snapshot_config(cfg: RunConfig, output_dir: str) -> None:
    """Write the resolved config into the output dir for reproducibility."""
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "config_snapshot.yaml"), "w") as f:
        yaml.safe_dump(cfg.raw, f, sort_keys=False)
