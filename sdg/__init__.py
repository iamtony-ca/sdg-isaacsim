"""sdg — general, extensible synthetic data generation framework on Isaac Sim 6.0.1.

Object- and task-agnostic. A generation run is fully described by a YAML config
(see config/example.yaml and SDG.md). New objects/tasks/output formats are added as
plugins/config, not core code. Target objects are always referred to generically as
``obj`` / ``obj_id`` — never a specific object name.
"""

__all__ = ["config", "registry"]
