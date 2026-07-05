"""Scene builders: assemble background/ground/objects and apply semantic labels.

Importing this package registers the built-in scene builders. `default` imports Isaac only
inside build(), so importing the package is safe without the simulator.
"""
from . import base  # noqa: F401
from . import default  # noqa: F401  # registers "default"
