"""Verify that CLI/GUI entry points are stubs from later phases."""
from __future__ import annotations

import pytest


def test_cli_main_raises() -> None:
    """`lokdocsqr.cli.main.main` must raise NotImplementedError in Phase 0."""
    from lokdocsqr.cli.main import main

    with pytest.raises(NotImplementedError):
        main()


def test_gui_main_raises() -> None:
    """`lokdocsqr.gui.app.main` must raise NotImplementedError in Phase 0."""
    from lokdocsqr.gui.app import main

    with pytest.raises(NotImplementedError):
        main()


def test_cli_main_accepts_argv_argument() -> None:
    """The stub must accept an optional argv list (signature contract)."""
    from lokdocsqr.cli.main import main

    with pytest.raises(NotImplementedError):
        main(["status"])


def test_gui_main_accepts_argv_argument() -> None:
    """The GUI stub must accept an optional argv list (signature contract)."""
    from lokdocsqr.gui.app import main

    with pytest.raises(NotImplementedError):
        main([])
