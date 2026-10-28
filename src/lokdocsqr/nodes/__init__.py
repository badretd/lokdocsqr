"""Node packages.

Importing ``lokdocsqr.nodes`` is the built-in discovery entry point
(``core/plugins.py``). Each subpackage below is imported here so that
``@register_node`` runs at import time and populates ``NodeRegistry``
without requiring an explicit submodule import from callers.

Invariant 1: this module imports ``core.*`` only — never ``gui`` or
``PySide6``. Invariant 8: ``core.executor`` must not import from
here; it resolves nodes through ``NodeRegistry`` at runtime.
"""

from . import fs_ops, access  # noqa: F401  (registers fs.make_dir, fs.write_file)
from . import flow  # noqa: F401

__all__ = ["fs_ops", "access"]
