"""retry_safe default and fs_ops spot-check (SPEC §5)."""
import pytest


def test_node_retry_safe_default_false():
    from lokdocsqr.core.node_api.base import Node
    assert Node.retry_safe is False


def test_fs_move_retry_safe_false():
    from lokdocsqr.nodes.fs_ops.move import MoveNode
    assert MoveNode.retry_safe is False


def test_fs_copy_retry_safe_false():
    from lokdocsqr.nodes.fs_ops.copy import CopyNode
    assert CopyNode.retry_safe is False
