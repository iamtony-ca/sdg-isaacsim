"""Annotators collect GT channels into a normalized per-frame dict.

Which channels are produced is driven by the config `annotators` map (rgb/depth/
semantic/instance/bbox_2d/bbox_3d/normals/pose_6d). FrameCollector attaches the matching
Replicator annotators to a sensor's render product and normalizes their get_data() into the
Frame dict consumed by writers (shape documented in sdg/writers/base.py).

FrameCollector imports Isaac only inside setup()/collect(), so importing this package is
safe without the simulator.
"""
from .collector import FrameCollector  # noqa: F401
