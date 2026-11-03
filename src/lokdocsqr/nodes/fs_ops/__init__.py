"""fs_ops nodes — filesystem-mutating operations.

Every node in this package goes through ``core.safe_fs`` and
``core.scope``; direct filesystem mutation is forbidden by the
architecture contract (invariants 3, 4, 7, 9).

Importing this package triggers ``@register_node`` on each module below
and therefore populates the ``NodeRegistry`` with:
  * ``fs.make_dir``
  * ``fs.write_file``
  * ``fs.copy``
  * ``fs.move``
  * ``fs.rename``
  * ``fs.tag``
  * ``fs.trash``
  * ``fs.archive``
  * ``fs.convert``
"""
from . import archive, convert, make_dir, tag, trash, write_file  # noqa: F401  (registers nodes)  # noqa: F401  (registers nodes)
from .copy import CopyNode
from .make_dir import MakeDirNode
from .move import MoveNode
from .rename import RenameNode
from .write_file import WriteFileNode

__all__ = ["CopyNode", "MakeDirNode", "MoveNode", "RenameNode", "WriteFileNode"]
