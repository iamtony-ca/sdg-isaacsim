"""Isaac Sim built-in background/asset library resolution (object-agnostic).

Isaac Sim does NOT ship its HDRI sky + environment library locally — those live on the
NVIDIA cloud assets root (an S3/Nucleus URL resolved at runtime by
`isaacsim.storage.native.get_assets_root_path()`). This module turns config *keywords*
(`isaac_skies`, `isaac_skies:Indoor,Night`, env preset names like `warehouse`) into full
asset URLs, so a config can pull the many varied Isaac backgrounds without hardcoding a
version-fragile cloud path. (Principle: engine background assets are chosen by config
keyword, never baked into code/paths — the target *object* stays the only per-run variable.)

★ 6.0.1 API (verified against the install):
  - isaacsim.storage.native.get_assets_root_path() -> str  (nucleus.py:556)
      returns the cloud/Nucleus root, e.g. ".../Assets/Isaac/5.0"; must be called while
      SimulationApp is running (needs carb settings). We cache the first success.
  - The sky/env relative paths below are exactly those referenced by the shipped
      standalone_examples (scene_based_sdg.py, amr_navigation.py, collect test stages), so
      they are canonical and known to exist under the assets root — not guesses.
"""
from __future__ import annotations

import glob
import os
from typing import Dict, List, Optional, Tuple

# HDRI sky env maps under {assets_root}/NVIDIA/Assets/Skies/<Category>/<file>.
# Grouped so a config can ask for a whole category (isaac_skies:Indoor) or the lot.
ISAAC_SKIES: Dict[str, List[str]] = {
    "Clear": [
        "/NVIDIA/Assets/Skies/Clear/evening_road_01_4k.hdr",
        "/NVIDIA/Assets/Skies/Clear/mealie_road_4k.hdr",
        "/NVIDIA/Assets/Skies/Clear/noon_grass_4k.hdr",
        "/NVIDIA/Assets/Skies/Clear/qwantani_4k.hdr",
        "/NVIDIA/Assets/Skies/Clear/sunflowers_4k.hdr",
    ],
    "Cloudy": [
        "/NVIDIA/Assets/Skies/Cloudy/champagne_castle_1_4k.hdr",
        "/NVIDIA/Assets/Skies/Cloudy/kloofendal_48d_partly_cloudy_4k.hdr",
    ],
    "Evening": [
        "/NVIDIA/Assets/Skies/Evening/evening_road_01_4k.hdr",
    ],
    "Indoor": [
        "/NVIDIA/Assets/Skies/Indoor/autoshop_01_4k.hdr",
        "/NVIDIA/Assets/Skies/Indoor/carpentry_shop_01_4k.hdr",
        "/NVIDIA/Assets/Skies/Indoor/hotel_room_4k.hdr",
        "/NVIDIA/Assets/Skies/Indoor/studio_small_04_4k.hdr",
        "/NVIDIA/Assets/Skies/Indoor/wooden_lounge_4k.hdr",
    ],
    "Night": [
        "/NVIDIA/Assets/Skies/Night/kloppenheim_02_4k.hdr",
        "/NVIDIA/Assets/Skies/Night/moonlit_golf_4k.hdr",
    ],
}

# ---------------------------------------------------------------------------------------
# Realistic floor/ground materials — the "background" the user actually sees (camera looks
# down at the object, so the ground plane fills the frame). Isaac's default GroundPlane and a
# tiny local texture pool read as an unrealistic checkerboard; these are photo-real surfaces
# (wood, gravel, concrete, carpet, stone) from Isaac Sim's cloud material library.
#
# Layout on the assets server (verified references in the install; each Base material folder
# ships <Name>_BaseColor.png alongside <Name>.mdl; vMaterials_2/*/textures ship *_diff.jpg):
#   {assets_root}/NVIDIA/Materials/Base/Wood/Oak/Oak_BaseColor.png
#   {assets_root}/NVIDIA/Materials/vMaterials_2/Ground/textures/gravel_track_ballast_diff.jpg
# tools/fetch_isaac_assets.py ENUMERATES these dirs (omni.client.list) and downloads only the
# diffuse/base-color images that actually exist -> assets/textures/ground/ (self-verifying,
# no guessed filenames). Offline generation then reads that local dir; the cloud is only
# touched by the one-time fetch. Keep this list as the record of *where these came from*.
ISAAC_GROUND_DIRS = [
    "/NVIDIA/Materials/Base/Wood",
    "/NVIDIA/Materials/Base/Stone",
    "/NVIDIA/Materials/Base/Masonry",
    "/NVIDIA/Materials/Base/Carpet",
    "/NVIDIA/Materials/Base/Concrete",
    "/NVIDIA/Materials/vMaterials_2/Ground/textures",
    "/NVIDIA/Materials/vMaterials_2/Concrete/textures",
    "/NVIDIA/Materials/vMaterials_2/Wood/textures",
]

# Verified-to-be-referenced diffuse textures — a safe fallback if enumeration returns little
# (e.g. a dir moved between asset releases). These exact paths appear in the 6.0.1 install.
CURATED_GROUND_TEXTURES = [
    "/NVIDIA/Materials/Base/Wood/Ash/Ash_BaseColor.png",
    "/NVIDIA/Materials/Base/Wood/Oak/Oak_BaseColor.png",
    "/NVIDIA/Materials/Base/Wood/Plywood/Plywood_BaseColor.png",
    "/NVIDIA/Materials/Base/Wood/Timber/Timber_BaseColor.png",
    "/NVIDIA/Materials/vMaterials_2/Ground/textures/aggregate_exposed_diff.jpg",
    "/NVIDIA/Materials/vMaterials_2/Ground/textures/gravel_track_ballast_diff.jpg",
]

# Pick only the base-colour/diffuse image of each material (skip normal/roughness/ao/orm/etc.)
_DIFFUSE_HINTS = ("basecolor", "base_color", "_diff", "diffuse", "albedo", "_color")
_MAP_EXCLUDE = ("normal", "_rough", "roughness", "_ao", "_orm", "metallic", "_disp",
                "height", "opacity", "_mask", "emiss", "_spec", "_nrm")

# Full-3D environment stages under {assets_root}/Isaac/Environments/... (used as `background`).
ISAAC_ENVIRONMENTS: Dict[str, str] = {
    "grid": "/Isaac/Environments/Grid/default_environment.usd",
    "grid_black": "/Isaac/Environments/Grid/gridroom_black.usd",
    "grid_curved": "/Isaac/Environments/Grid/gridroom_curved.usd",
    "warehouse": "/Isaac/Environments/Simple_Warehouse/warehouse.usd",
    "warehouse_full": "/Isaac/Environments/Simple_Warehouse/full_warehouse.usd",
    "warehouse_shelves": "/Isaac/Environments/Simple_Warehouse/warehouse_multiple_shelves.usd",
    "simple_room": "/Isaac/Environments/Simple_Room/simple_room.usd",
    "office": "/Isaac/Environments/Office/office.usd",
    "hospital": "/Isaac/Environments/Hospital/hospital.usd",
}

_URL_SCHEMES = ("omniverse://", "http://", "https://", "file://")
_SKY_KEYWORD = "isaac_skies"

_cached_root: Optional[str] = None


def is_url(s: str) -> bool:
    return isinstance(s, str) and s.lower().startswith(_URL_SCHEMES)


def isaac_assets_root() -> str:
    """Resolve (and cache) the Isaac Sim cloud/Nucleus assets root URL. Requires a running
    SimulationApp. Raises RuntimeError with an actionable message if it can't be reached."""
    global _cached_root
    if _cached_root:
        return _cached_root
    from isaacsim.storage.native import get_assets_root_path

    root = get_assets_root_path()
    if not root:
        raise RuntimeError(
            "Isaac assets root unavailable — get_assets_root_path() returned empty. "
            "Check network reachability to the NVIDIA assets server, or point `hdri`/"
            "`background` at local files instead."
        )
    _cached_root = root.rstrip("/")
    return _cached_root


def is_sky_keyword(s) -> bool:
    return isinstance(s, str) and s.split(":", 1)[0].strip().lower() == _SKY_KEYWORD


def sky_urls(keyword: str) -> List[str]:
    """Expand an `isaac_skies` keyword into full HDRI URLs.

    `isaac_skies`                -> every category. `isaac_skies:Indoor,Night` -> only those
    categories (case-insensitive; unknown categories warn + skip). Returns [] if the assets
    root can't be resolved (caller decides whether that's fatal), so a network hiccup degrades
    to 'no sky pool' rather than crashing the whole run."""
    _, _, rest = keyword.partition(":")
    wanted = [c.strip() for c in rest.split(",") if c.strip()] if rest else list(ISAAC_SKIES)
    rels: List[str] = []
    for cat in wanted:
        match = next((k for k in ISAAC_SKIES if k.lower() == cat.lower()), None)
        if match is None:
            print(f"[sdg][assets] unknown isaac_skies category '{cat}' — "
                  f"available: {list(ISAAC_SKIES)}")
            continue
        rels += ISAAC_SKIES[match]
    if not rels:
        return []
    try:
        root = isaac_assets_root()
    except RuntimeError as e:
        print(f"[sdg][assets] {e}")
        return []
    return [root + r for r in rels]


def resolve_env_preset(name: str) -> Optional[str]:
    """Map a `background` preset name (e.g. 'warehouse') to a full env-USD URL, or None if
    it isn't a known preset (caller then treats `name` as an explicit path)."""
    rel = ISAAC_ENVIRONMENTS.get(name.strip().lower())
    if rel is None:
        return None
    return isaac_assets_root() + rel


# ---------------------------------------------------------------------------------------
# Cloud enumeration + download (used by tools/fetch_isaac_assets.py). All of this needs a
# running SimulationApp (omni.client is a Kit extension), so imports are lazy.

def list_cloud_dir(url: str):
    """List one cloud/Nucleus folder. Returns (files, subdirs) of basenames, or None if the
    folder can't be listed (missing / no permission) — caller skips it."""
    import omni.client

    res, entries = omni.client.list(url)
    if res != omni.client.Result.OK:
        return None
    files, subdirs = [], []
    for e in entries:
        if e.flags & omni.client.ItemFlags.CAN_HAVE_CHILDREN:
            subdirs.append(e.relative_path)
        else:
            files.append(e.relative_path)
    return files, subdirs


def _looks_diffuse(name: str) -> bool:
    n = name.lower()
    if not n.endswith((".png", ".jpg", ".jpeg")):
        return False
    # skip preview thumbnails (material folders keep .thumbs/<size>/<name>.png.png copies)
    if n.endswith((".png.png", ".jpg.png", ".jpeg.png")):
        return False
    if any(x in n for x in _MAP_EXCLUDE):
        return False
    return any(h in n for h in _DIFFUSE_HINTS)


def discover_ground_texture_rels(limit: int = 48, max_depth: int = 3) -> List[str]:
    """Walk ISAAC_GROUND_DIRS on the assets server (bounded), collecting the rel-paths of
    realistic floor diffuse/base-color textures that actually exist. Curated verified paths
    are always appended. Returns a de-duplicated list of rel-paths (leading '/')."""
    root = isaac_assets_root()
    found: List[str] = []
    seen = set()

    def walk(rel: str, depth: int) -> None:
        if len(found) >= limit or depth > max_depth:
            return
        listing = list_cloud_dir(root + rel)
        if not listing:
            return
        files, subdirs = listing
        for f in files:
            if _looks_diffuse(f):
                p = f"{rel}/{f}"
                if p not in seen:
                    seen.add(p)
                    found.append(p)
                    if len(found) >= limit:
                        return
        for d in subdirs:
            if d.startswith("."):  # skip .thumbs and other hidden preview dirs
                continue
            walk(f"{rel}/{d}", depth + 1)

    for base in ISAAC_GROUND_DIRS:
        if len(found) >= limit:
            break
        walk(base, 0)
    for p in CURATED_GROUND_TEXTURES:
        if p not in seen:
            seen.add(p)
            found.append(p)
    return found


def download(src_url: str, dst_path: str) -> str:
    """Download one cloud asset to a local file via omni.client.copy. Idempotent (existing
    file -> 'skip'). Returns 'ok' | 'skip' | 'fail'."""
    import os

    import omni.client

    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if os.path.exists(dst_path):
        return "skip"
    dst_url = omni.client.make_file_url_if_possible(dst_path)
    res = omni.client.copy(src_url, dst_url, omni.client.CopyBehavior.OVERWRITE)
    return "ok" if res == omni.client.Result.OK and os.path.exists(dst_path) else "fail"


def sky_rels() -> List[str]:
    """All Isaac sky HDRI rel-paths (every category), for the fetch tool to localize."""
    rels: List[str] = []
    for paths in ISAAC_SKIES.values():
        rels += paths
    return rels


# ---------------------------------------------------------------------------------------
# Environment USD backgrounds — ONLINE (cloud preset) vs OFFLINE (localized) resolution.
#
# Env stages reference many dependencies (materials, textures, props/sublayers), so localizing
# them needs a dependency-aware collect (tools/fetch_isaac_assets.py --envs, via
# omni.kit.usd.collect.Collector) -> assets/env/usd/<name>/. The two modes are kept SEPARATE
# and a config picks one by what it puts in a `background`/pool entry:
#   - OFFLINE: a local dir (assets/env/usd/<name>) or an explicit .usd path -> no network.
#   - ONLINE : a preset name from ISAAC_ENVIRONMENTS (e.g. 'warehouse') -> cloud URL (network).
LOCAL_ENV_DIR_REL = os.path.join("assets", "env", "usd")
_WS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_USD_EXTS = (".usd", ".usda", ".usdc", ".usdz")


def local_env_dir() -> str:
    return os.path.join(_WS_ROOT, LOCAL_ENV_DIR_REL)


def find_local_env_usd(entry: str) -> Optional[str]:
    """Resolve a local env-USD path from `entry`: an explicit .usd file, or a directory (the
    localized `assets/env/usd/<name>/` collect folder) whose root .usd we locate. Returns the
    absolute .usd path, or None if `entry` isn't a local file/dir."""
    p = entry if os.path.isabs(entry) else os.path.join(_WS_ROOT, entry)
    if os.path.isfile(p) and p.lower().endswith(_USD_EXTS):
        return p
    if os.path.isdir(p):
        # Prefer a top-level .usd matching the folder name, else the shallowest .usd.
        base = os.path.basename(p.rstrip("/"))
        top = [f for f in glob.glob(os.path.join(p, "*")) if f.lower().endswith(_USD_EXTS)]
        for f in top:
            if os.path.splitext(os.path.basename(f))[0].lower() == base.lower():
                return f
        if top:
            return sorted(top)[0]
        deep = sorted(glob.glob(os.path.join(p, "**", "*.usd*"), recursive=True), key=len)
        return deep[0] if deep else None
    return None


def resolve_background(entry: str) -> Tuple[str, str]:
    """Resolve a background pool `entry` to (path_or_url, mode). mode is 'offline' for a local
    USD (dir/file) or 'online' for a cloud preset/URL. Raises ValueError if it's neither a
    local USD nor a known preset/URL — so a typo fails loudly instead of silently skipping."""
    local = find_local_env_usd(entry)
    if local:
        return local, "offline"
    if is_url(entry) or entry.lower().endswith(_USD_EXTS):
        return entry, "online"
    url = resolve_env_preset(entry)
    if url is not None:
        return url, "online"
    raise ValueError(
        f"background '{entry}' is neither a local USD (dir/file under the repo) nor a known "
        f"preset {sorted(ISAAC_ENVIRONMENTS)} nor a .usd URL. For OFFLINE use, localize it "
        f"first: tools/fetch_isaac_assets.py --envs {entry}")
