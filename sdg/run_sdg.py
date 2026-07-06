"""run_sdg — config-driven entrypoint for the SDG framework.

    /isaac-sim/python.sh sdg/run_sdg.py --config config/example.yaml [--headless]

Orchestration (see SDG.md §1):
    load config -> start SimulationApp -> build scene -> setup randomizers/sensors
    -> for each frame: randomize -> render/capture annotators -> write -> shutdown.

The pure-Python layers (config, registry, writers) work without Isaac; the Isaac/
Replicator calls are isolated behind `# TODO(6.0.1)` markers and sdg/app.py so this
module imports cleanly for inspection. Fill the TODOs in S1 after verifying the 6.0.1
Replicator API against the install (see references in sdg/app.py).
"""
from __future__ import annotations

import argparse
import os
import sys

# ensure `import sdg...` works when run as a script from the ws root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sdg import registry
from sdg.config import RunConfig, load_config, snapshot_config

# side-effect imports: each package registers its plugins with the registry on import.
import sdg.writers      # noqa: F401  # registers "generic"
import sdg.scene        # noqa: F401  # registers "default"
import sdg.sensors      # noqa: F401  # registers "ideal"
import sdg.randomizers  # noqa: F401  # registers lighting/pose/camera/materials/distractors


class _Ctx:
    """Shared per-run context handed to randomizers (scene prims, sensors, RNG)."""

    def __init__(self, scene, sensors, rng):
        self.scene = scene
        self.sensors = sensors
        self.rng = rng


def build_writer(cfg: RunConfig):
    fmt = cfg.writer.get("format", "generic")
    writer_cls = registry.get("writer", fmt)
    return writer_cls(cfg.run.output_dir, cfg.writer)


def build_randomizers(cfg: RunConfig, ctx: "_Ctx"):
    """Instantiate configured randomizers, tolerating unknown types (warn + skip)."""
    rands = []
    for r in cfg.randomizers:
        rtype = r.get("type")
        try:
            cls = registry.get("randomizer", rtype)
        except KeyError:
            print(f"[sdg] unknown randomizer '{rtype}' — skipping "
                  f"(available: {registry.available('randomizer')})")
            continue
        inst = cls(r, ctx)
        inst.setup()
        rands.append(inst)
    return rands


def run(cfg: RunConfig) -> None:
    from sdg.app import SdgApp

    os.makedirs(cfg.run.output_dir, exist_ok=True)
    snapshot_config(cfg, cfg.run.output_dir)

    writer = build_writer(cfg)
    writer.open()

    with SdgApp(headless=cfg.run.headless, seed=cfg.run.seed) as app:
        # The app uses kit fast-shutdown (os._exit) inside SdgApp.__exit__, which runs
        # when this `with` block closes and terminates the process immediately. So ALL
        # finalization (writer.close, error persistence) MUST happen INSIDE the block —
        # anything after the `with` would never execute.
        try:
            _run_inside_app(cfg, app, writer)
            writer.close({
                "sensors": [s.name for s in cfg.sensors],
                "num_captures": cfg.run.num_frames * len(cfg.sensors),
            })
            print(f"[sdg] done -> {cfg.run.output_dir}", flush=True)
        except Exception:
            _persist_error(cfg.run.output_dir)
            raise


def _run_inside_app(cfg: RunConfig, app, writer) -> None:
    from sdg.annotators import FrameCollector

    rep = app.rep

    # 1) scene -------------------------------------------------------------
    scene = registry.get("scene", cfg.scene.get("builder", "default"))(cfg.scene, cfg.objects)
    scene.build()
    print(f"[sdg] scene built: {len(scene.instances)} object instance(s)", flush=True)

    # 2) sensors + annotator collectors ------------------------------------
    sensors = [registry.get("sensor", s.type)(s) for s in cfg.sensors]
    for s in sensors:
        s.create()
    collectors = [FrameCollector(s, cfg.annotators) for s in sensors]
    for c in collectors:
        c.setup()
    print(f"[sdg] sensors ready: {[s.name for s in sensors]}", flush=True)

    # 3) randomizers -------------------------------------------------------
    ctx = _Ctx(scene, sensors, rep.rng.ReplicatorRNG(seed=cfg.run.seed).generator)
    rands = build_randomizers(cfg, ctx)

    # let referenced assets / render products finish loading before capture
    for _ in range(2):
        app.update()
    # Warm-up: the first orchestrator.step primes the SyntheticData graph so annotators
    # (notably camera_params) return valid data. Readiness is timing-dependent — a single
    # step used to intermittently leave camera_params with a singular view transform / zero
    # aperture, crashing frame 0. Step until every collector reports ready (bounded).
    if rands:
        for r in rands:
            r.apply(0)
    _warm_up(app, collectors)

    # 4) frame loop --------------------------------------------------------
    widx = 0
    for fid in range(cfg.run.num_frames):
        for r in rands:
            r.apply(fid)
        # extra subframes on the first frame to settle large scene/material loads
        app.step(rt_subframes=16 if fid == 0 else -1)
        for c in collectors:
            frame = c.collect(widx, scene.instances)
            if c.want_amodal:
                c.capture_amodal(app, scene, frame)  # extra isolated renders -> amodal masks
            writer.write(frame)
            widx += 1
        if (fid + 1) % 10 == 0 or fid == 0:
            print(f"[sdg] captured frame {fid + 1}/{cfg.run.num_frames}", flush=True)

    for c in collectors:
        c.teardown()


def _warm_up(app, collectors, max_steps: int = 16) -> None:
    """Step the app until every collector's camera_params is ready, or give up (bounded).

    The first step uses extra subframes to settle large scene/material loads; subsequent
    steps use the config default. Prevents the intermittent frame-0 crash where capture ran
    before the SyntheticData graph produced a valid camera view transform / aperture.
    """
    for i in range(max_steps):
        app.step(rt_subframes=16 if i == 0 else -1)
        if all(c.camera_ready() for c in collectors):
            if i:
                print(f"[sdg] camera_params ready after {i + 1} warm-up steps", flush=True)
            return
    print(f"[sdg] warning: camera_params not ready after {max_steps} warm-up steps — "
          "proceeding anyway (intrinsics/pose may be degraded on early frames)", flush=True)


def _persist_error(output_dir: str) -> None:
    import traceback

    tb = traceback.format_exc()
    try:
        with open(os.path.join(output_dir, "error.log"), "w") as f:
            f.write(tb)
    except OSError:
        pass
    print("[sdg][ERROR] run failed:\n" + tb, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Config-driven synthetic data generation (Isaac Sim 6.0.1)")
    ap.add_argument("--config", required=True, help="path to a run config YAML")
    ap.add_argument("--headless", action="store_true", help="override config to run headless")
    ap.add_argument("--frames", type=int, default=None, help="override run.num_frames")
    ap.add_argument("--dry-run", action="store_true",
                    help="load+validate config and exit (no Isaac). Sanity check the scaffolding.")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.headless:
        cfg.run.headless = True
    if args.frames is not None:
        cfg.run.num_frames = args.frames

    if args.dry_run:
        print(f"[dry-run] config OK: run='{cfg.run.name}' frames={cfg.run.num_frames} "
              f"objects={[o.obj_id for o in cfg.objects]} sensors={[s.name for s in cfg.sensors]} "
              f"writer={cfg.writer.get('format')}")
        print(f"[dry-run] registered writers: {registry.available('writer')}")
        return

    run(cfg)


if __name__ == "__main__":
    main()
