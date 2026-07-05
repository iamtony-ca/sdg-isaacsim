"""Domain randomizers applied per-frame (lighting/materials/pose/camera/distractors).

Importing this package registers all built-in randomizers. Concrete modules import Isaac
only inside apply()/setup(), so importing the package is safe without the simulator
(registration is import-time; Isaac use is call-time).
"""
from . import base  # noqa: F401
from . import lighting, pose, camera, materials, distractors  # noqa: F401
