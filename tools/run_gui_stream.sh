#!/bin/sh
# run_gui_stream.sh — view the Isaac Sim GUI from this HEADLESS container (no X display).
#
# This container has no X server, so ~/runapp.sh (native GUI) can't open a window.
# The correct way to see the GUI is NVIDIA's WebRTC livestream (~/runheadless.sh), which
# renders on the GPU headlessly and streams to a client. This wrapper just launches it with
# the right public IP and prints the connection info.
#
# Usage (run as the isaac-sim user):
#   ~/volume/sdg_ws/tools/run_gui_stream.sh
#   ISAACSIM_HOST=<ip> ~/volume/sdg_ws/tools/run_gui_stream.sh   # override advertised IP
#
# Then on a machine that can reach this host, open the "Isaac Sim WebRTC Streaming Client"
# and connect to the printed IP. Ports (must be reachable): 49100/tcp (signal), 47998/udp
# (media). This container uses host networking, so they are on the host IP directly.
set -eu

IP="${ISAACSIM_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
: "${IP:=127.0.0.1}"

echo "=================================================================="
echo " Isaac Sim GUI (WebRTC stream)"
echo "  connect the Isaac Sim WebRTC Streaming Client to:  $IP"
echo "  ports: 49100/tcp (signal), 47998/udp (media)"
echo "  (Ctrl+C here to stop the stream server.)"
echo "=================================================================="

ISAACSIM_HOST="$IP" exec /isaac-sim/runheadless.sh "$@"
