"""LLM node family (Phase 9).

Importing this package registers ``llm.local_llm``. The optional ``ollama``
dependency is imported lazily inside the node, never here.
"""

from . import local_llm

__all__ = ["local_llm"]
