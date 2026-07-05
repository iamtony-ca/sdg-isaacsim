#!/bin/sh
# fix_perms.sh — restore ownership of Isaac Sim shared caches + this workspace to the
# container's runtime user, undoing pollution from having run Isaac Sim as root.
#
# WHY: this container has no `sudo` and the normal user (`isaac-sim`) is only in its own
# group, so if root ever runs Isaac Sim it leaves root-owned files in shared caches
# (/isaac-sim/kit/cache, kit/logs, kit/data, .nv, exts/omni.pip.*) that the isaac-sim user
# can no longer write. Then `~/runapp.sh` / `~/runheadless.sh` fail to start cleanly.
# See DEPENDENCIES.md "권한 문제".
#
# This script MUST run as root (chown needs it). You cannot fix it from the isaac-sim
# account (no sudo). Run it one of these ways:
#   - from a root shell in the container:   sh tools/fix_perms.sh
#   - from the HOST (container name below):  docker exec -u root <container> sh /isaac-sim/volume/sdg_ws/tools/fix_perms.sh
#   - just check, don't change (any user):   sh tools/fix_perms.sh --check
#
# Override the runtime user with SDG_RUNTIME_USER (default: isaac-sim).
set -eu

OWNER="${SDG_RUNTIME_USER:-isaac-sim}"
ISAAC="${ISAAC_SIM_ROOT:-/isaac-sim}"
WS="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"

# Dirs Isaac writes at runtime (must belong to the runtime user) + this workspace.
DIRS="\
$ISAAC/kit/cache $ISAAC/kit/logs $ISAAC/kit/data \
$ISAAC/.nv $ISAAC/.cache $ISAAC/.nvidia-omniverse \
$ISAAC/exts/omni.pip.cloud $ISAAC/exts/omni.pip.compute \
$WS"

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

echo "[fix_perms] runtime user = '$OWNER' | workspace = $WS"
total=0
for d in $DIRS; do
    [ -e "$d" ] || continue
    n=$(find "$d" -user root 2>/dev/null | wc -l)
    if [ "$n" -gt 0 ]; then
        echo "  $n root-owned under: $d"
        total=$((total + n))
    fi
done

if [ "$CHECK" -eq 1 ]; then
    echo "[fix_perms] $total root-owned path(s) total. Re-run WITHOUT --check as root to fix."
    exit 0
fi

if [ "$(id -u)" != "0" ]; then
    echo "[fix_perms] ERROR: must run as root (no sudo in this container)." >&2
    echo "[fix_perms]   from host: docker exec -u root <container> sh /isaac-sim/volume/sdg_ws/tools/fix_perms.sh" >&2
    exit 1
fi

if [ "$total" -eq 0 ]; then
    echo "[fix_perms] nothing to fix — already clean."
    exit 0
fi

for d in $DIRS; do
    [ -e "$d" ] || continue
    find "$d" -user root -exec chown "$OWNER:$OWNER" {} + 2>/dev/null || true
done
echo "[fix_perms] done — restored $total path(s) to '$OWNER' across Isaac caches + workspace."
