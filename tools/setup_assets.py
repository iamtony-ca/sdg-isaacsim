"""One-shot asset bootstrap for a fresh clone / new PC.

Everything this repo needs at generation time but does NOT commit (all .gitignore'd — they are
third-party binaries or regenerable) is downloaded/rebuilt here into the SAME local dirs the
configs already reference. After this, offline configs (config/env_offline.yaml,
config/dr_demo.yaml) run with no network.

WHAT IT RESTORES (see DEPENDENCIES.md §3):
  floors   realistic ground textures  -> assets/textures/ground/     (fetch_isaac_assets --floors)
  skies    HDRI sky env maps          -> assets/env/hdri/            (fetch_isaac_assets --skies)
  envs     env-USD backgrounds        -> assets/env/usd/<name>/      (fetch_isaac_assets --envs)
  objects  CAD -> mesh.usd            -> assets/obj/<obj_id>/mesh.usd (import_cad, from tracked CAD)

WHY A SUBPROCESS ORCHESTRATOR (not one process): fetch_isaac_assets.py and import_cad.py each
launch a SimulationApp whose .close() calls os._exit (CLAUDE.md trap #1) — it kills the whole
process. So two SimulationApp tools cannot share a process; this driver runs each as its own
subprocess, in order, and is itself plain (no Isaac import) so it can run under any python.

USAGE (bundle python — it shells out to /isaac-sim/python.sh for the Isaac tools):
    /isaac-sim/python.sh tools/setup_assets.py               # floors + skies + objects (default)
    /isaac-sim/python.sh tools/setup_assets.py --all         # + envs (large: office~680MB, ...)
    /isaac-sim/python.sh tools/setup_assets.py --steps floors,skies
    /isaac-sim/python.sh tools/setup_assets.py --envs warehouse,office --steps envs
    /isaac-sim/python.sh tools/setup_assets.py --force       # re-fetch even if dirs are populated
    /isaac-sim/python.sh tools/setup_assets.py --dry-run     # print the planned commands only

Idempotent: a step whose target dir is already populated is skipped unless --force. Envs are
opt-in (large) — included only with --all or an explicit --steps that names `envs`.
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

WS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GROUND_DIR = os.path.join(WS_ROOT, "assets", "textures", "ground")
HDRI_DIR = os.path.join(WS_ROOT, "assets", "env", "hdri")
ENV_DIR = os.path.join(WS_ROOT, "assets", "env", "usd")
OBJ_DIR = os.path.join(WS_ROOT, "assets", "obj")

DEFAULT_ENVS = ["simple_room", "office"]

# CAD -> obj_id imports to reproduce on a fresh clone. CAD sources are git-tracked under
# assets/cad/; the converted mesh.usd is gitignored (regenerated here). Add a dict per object.
# Keeps object identity generic (referenced only by obj_id — CLAUDE.md principle 2).
OBJECT_IMPORTS = [
    {
        "obj_id": "obj_000",
        "cad": "assets/cad/6-inch-wafer-cassette/Wafer Cassette_6 Inch - 25 Wafer Capacity.stl",
        "units": "mm",
        "up_axis": "Z",
    },
]

ALL_STEPS = ["floors", "skies", "envs", "objects"]


def _isaac_python() -> str:
    return os.environ.get("ISAAC_PYTHON", "/isaac-sim/python.sh")


def _has_files(d: str, *patterns: str) -> bool:
    return any(glob.glob(os.path.join(d, p)) for p in patterns)


def _run(cmd: list[str], dry: bool) -> int:
    printable = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    print(f"\n$ {printable}", flush=True)
    if dry:
        return 0
    return subprocess.run(cmd, cwd=WS_ROOT).returncode


def _fetch_cmd(*flags: str) -> list[str]:
    return [_isaac_python(), os.path.join("tools", "fetch_isaac_assets.py"), *flags]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--steps", default=None,
                    help=f"comma list from {ALL_STEPS} (default: floors,skies,objects). "
                         "envs are opt-in (large).")
    ap.add_argument("--all", action="store_true", help="all steps including envs.")
    ap.add_argument("--envs", default=None,
                    help=f"env presets to localize (default {DEFAULT_ENVS}); implies the envs step.")
    ap.add_argument("--limit", type=int, default=48, help="max floor textures (passed through).")
    ap.add_argument("--force", action="store_true", help="redo a step even if its dir is populated.")
    ap.add_argument("--dry-run", action="store_true", help="print planned commands, download nothing.")
    args = ap.parse_args()

    # Resolve which steps to run.
    if args.steps:
        steps = [s.strip() for s in args.steps.split(",") if s.strip()]
        bad = [s for s in steps if s not in ALL_STEPS]
        if bad:
            sys.exit(f"unknown step(s) {bad}; valid: {ALL_STEPS}")
    elif args.all:
        steps = list(ALL_STEPS)
    else:
        steps = ["floors", "skies", "objects"]  # envs opt-in
    if args.envs is not None and "envs" not in steps:
        steps.append("envs")
    env_names = ([e.strip() for e in args.envs.split(",")] if args.envs else DEFAULT_ENVS)

    print(f"[setup] steps: {steps}" + (f"  envs={env_names}" if "envs" in steps else ""))
    print(f"[setup] isaac python: {_isaac_python()}")
    failures: list[str] = []

    # ---- floors + skies (combine into one fetch invocation when both requested) ----------
    fetch_flags = []
    if "floors" in steps and (args.force or not _has_files(GROUND_DIR, "*.png", "*.jpg")):
        fetch_flags.append("--floors")
    elif "floors" in steps:
        print(f"[setup] floors: already populated ({GROUND_DIR}) — skip (use --force to redo)")
    if "skies" in steps and (args.force or not _has_files(HDRI_DIR, "*.hdr", "*.exr")):
        fetch_flags.append("--skies")
    elif "skies" in steps:
        print(f"[setup] skies: already populated ({HDRI_DIR}) — skip (use --force to redo)")
    if fetch_flags:
        if _run(_fetch_cmd(*fetch_flags, "--limit", str(args.limit)), args.dry_run) != 0:
            failures.append("+".join(fetch_flags))

    # ---- env-USD backgrounds (one fetch --envs per missing preset set) -------------------
    if "envs" in steps:
        need = [n for n in env_names
                if args.force or not os.path.isdir(os.path.join(ENV_DIR, n.lower()))
                or not os.listdir(os.path.join(ENV_DIR, n.lower()))]
        skipped = [n for n in env_names if n not in need]
        for n in skipped:
            print(f"[setup] env '{n}': already localized — skip (use --force to redo)")
        if need:
            if _run(_fetch_cmd("--envs", ",".join(need)), args.dry_run) != 0:
                failures.append(f"envs({','.join(need)})")

    # ---- objects: CAD -> assets/obj/<obj_id>/mesh.usd ------------------------------------
    if "objects" in steps:
        for spec in OBJECT_IMPORTS:
            obj_id = spec["obj_id"]
            dst = os.path.join(OBJ_DIR, obj_id)
            if not args.force and _has_files(dst, "*.usd", "*.usdc", "*.usda", "*.usdz"):
                print(f"[setup] object '{obj_id}': mesh.usd present — skip (use --force to redo)")
                continue
            cad = os.path.join(WS_ROOT, spec["cad"])
            if not os.path.isfile(cad):
                print(f"[setup] object '{obj_id}': CAD source missing ({spec['cad']}) — skip")
                failures.append(f"object({obj_id}):no-cad")
                continue
            cmd = [_isaac_python(), os.path.join("tools", "import_cad.py"), cad,
                   "--obj-id", obj_id, "--input-units", spec["units"], "--up-axis", spec["up_axis"]]
            if _run(cmd, args.dry_run) != 0:
                failures.append(f"object({obj_id})")

    print()
    if failures:
        print(f"[setup] DONE with FAILURES: {failures}")
        sys.exit(1)
    print("[setup] DONE — assets restored. Offline configs (config/env_offline.yaml, "
          "config/dr_demo.yaml) can now run without network.")


if __name__ == "__main__":
    main()
