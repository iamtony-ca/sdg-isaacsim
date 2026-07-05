"""Isaac Sim SimulationApp lifecycle wrapper (6.0.1, verified against the install).

ALL Isaac Sim startup is isolated here so the rest of the framework (config/registry/
writers) stays import-clean and testable without the simulator. `import sdg.app` is safe
without Isaac; the Isaac imports happen inside __enter__ (i.e. only when a run starts).

★ 6.0.1 API — verified against /isaac-sim (VERSION 6.0.1-rc.7), omni.replicator.core
1.13.27. Reference examples cross-checked:
  standalone_examples/api/isaacsim.replicator.examples/sdg_getting_started_01..05.py
  standalone_examples/api/isaacsim.replicator.examples/simulation_get_data.py

Verified deltas vs 5.1.0:
  - `from isaacsim import SimulationApp`; ctor takes `launch_config={...}` (kwarg).
  - New functional API `rep.functional.create.*` / `rep.functional.modify.*`.
  - Data capture is driven by `rep.orchestrator.step(...)` with capture-on-play disabled.
"""
from __future__ import annotations

import random
from typing import Any, Dict, Optional


class SdgApp:
    """Owns the SimulationApp; `with SdgApp(...) as app:` manages startup/shutdown.

    After __enter__ the following are attached for convenience:
      app.rep   -> omni.replicator.core
      app.usd   -> omni.usd
    and a fresh stage exists with capture-on-play disabled and the seed applied.
    """

    def __init__(
        self,
        headless: bool = True,
        seed: int = 0,
        dlss_exec_mode: int = 2,  # 0 Performance, 1 Balanced, 2 Quality, 3 Auto (2 = best SDG)
        extra_launch_config: Optional[Dict[str, Any]] = None,
    ):
        self.headless = headless
        self.seed = seed
        self.dlss_exec_mode = dlss_exec_mode
        self.extra_launch_config = extra_launch_config or {}
        self._app = None
        self.rep = None
        self.usd = None

    def __enter__(self) -> "SdgApp":
        # SimulationApp MUST be constructed before any other Isaac/omni import.
        from isaacsim import SimulationApp  # verified: sdg_getting_started_01.py:20

        launch_config = {"headless": self.headless, **self.extra_launch_config}
        self._app = SimulationApp(launch_config=launch_config)

        # Safe to import the rest only after the app exists.
        import carb.settings
        import omni.replicator.core as rep
        import omni.usd

        self.rep = rep
        self.usd = omni.usd

        # Fresh stage + SDG-friendly capture settings (see sdg_getting_started_01.py:31-36).
        omni.usd.get_context().new_stage()
        rep.orchestrator.set_capture_on_play(False)
        carb.settings.get_settings().set("rtx/post/dlss/execMode", self.dlss_exec_mode)

        # Reproducibility: seed both python.random and replicator's global RNG.
        random.seed(self.seed)
        rep.set_global_seed(self.seed)  # verified: sdg_getting_started_03.py:43

        return self

    def step(self, rt_subframes: int = -1, wait_for_render: bool = True) -> None:
        """Render + trigger a capture for all attached annotators/writers.

        rep.orchestrator.step(rt_subframes=-1, pause_timeline=True, delta_time=None,
        wait_for_render=True) — orchestrator.py:1745. rt_subframes>0 renders extra
        subframes to settle large scene/material changes (use after big randomizations).
        """
        self.rep.orchestrator.step(rt_subframes=rt_subframes, wait_for_render=wait_for_render)

    def update(self) -> None:
        """Advance the app without forcing a capture (UI/asset-load pump)."""
        if self._app is not None:
            self._app.update()

    def wait_until_complete(self) -> None:
        """Block until all in-flight writer/annotator data is flushed to disk."""
        if self.rep is not None:
            self.rep.orchestrator.wait_until_complete()

    def is_running(self) -> bool:
        return self._app is not None and self._app.is_running()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._app is not None:
            try:
                if self.rep is not None:
                    self.rep.orchestrator.wait_until_complete()
            finally:
                self._app.close()
                self._app = None
