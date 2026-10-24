"""Verify that CLI/GUI entry points are stubs from later phases."""
from __future__ import annotations

import pytest


def test_cli_main_raises() -> None:
    """cli.main must raise NotImplementedError in Phase 0."""
    from lokdocsqr.cli.main import main

    with pytest.raises(NotImplementedError):
        main()
    with pytest.raises(NotImplementedError):
        main(["status"])


def test_gui_main_raises() -> None:
    """gui.app.main must raise NotImplementedError in Phase 0."""
    from lokdocsqr.gui.app import main

    with pytest.raises(NotImplementedError):
        main()
    with pytest.raises(NotImplementedError):
        main([])
