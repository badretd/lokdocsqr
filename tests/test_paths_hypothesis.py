"""Hypothesis-driven traversal-resistance tests for render_template."""
from __future__ import annotations

from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
given = hypothesis.given
st = hypothesis.strategies

from lokdocsqr.core.errors import ScopeViolation  # noqa: E402
from lokdocsqr.core.paths import render_template  # noqa: E402


@given(st.text(alphabet=st.characters(blacklist_characters="{}")))
def test_render_template_never_escapes(text: str) -> None:
    """Any template that does not raise must stay relative with no '..' segment."""
    try:
        result = render_template(text, {})
    except ScopeViolation:
        return

    p = Path(result)
    assert not p.is_absolute()
    assert ".." not in p.parts


@given(st.text(alphabet=st.characters(blacklist_characters="{}")))
def test_render_template_rejects_leading_traversal(text: str) -> None:
    """A template starting with '../' can never normalize away the leading '..'."""
    template = "../" + text
    with pytest.raises(ScopeViolation):
        render_template(template, {})
