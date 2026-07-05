"""Camera/sensor models: render products, intrinsics, optional real-sensor degradation.

Importing this package registers the built-in sensors. `ideal` imports Isaac only inside
create(), so importing the package is safe without the simulator.
"""
from . import base  # noqa: F401
from . import ideal  # noqa: F401  # registers "ideal"
from . import realsense_depth  # noqa: F401  # registers "realsense_depth" (S4 degradation)
