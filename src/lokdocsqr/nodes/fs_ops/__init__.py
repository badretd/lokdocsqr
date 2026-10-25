"""fs_ops nodes — filesystem-mutating operations.

Every node in this package goes through ``core.safe_fs`` and
``core.scope``; direct filesystem mutation is forbidden by the
architecture contract (invariants 3, 4, 7, 9).

Importing this package triggers ``@register_node`` on each module below
and therefore populates the ``NodeRegistry`` with:
  * ``fs.make_dir``
  * ``fs.write_file``
"""
from . import make_dir, write_file  # noqa: F401  (registers nodes)
from .make_dir import MakeDirNode
from .write_file import WriteFileNode
from .copy import CopyNode
from .move import MoveNode
from .rename import RenameNode

__all__ = ["MakeDirNode", "WriteFileNode", "CopyNode", "MoveNode", "RenameNode"]
