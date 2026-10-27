"""Compatibility re-export for policy configuration.

The canonical definition lives in decision.py per the Phase 7a spec.
"""
from .decision import PolicyConfig

__all__ = ["PolicyConfig"]
