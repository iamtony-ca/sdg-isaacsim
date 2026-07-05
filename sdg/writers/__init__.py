"""Output writers: turn the normalized per-frame dict into an on-disk dataset format."""
from . import base            # noqa: F401
from . import generic_writer  # noqa: F401  # registers "generic"
from . import bop_writer      # noqa: F401  # registers "bop"
from . import coco_writer     # noqa: F401  # registers "coco"
from . import yolo_writer     # noqa: F401  # registers "yolo"
