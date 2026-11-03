"""Verify that CLI/GUI entry points are stubs from later phases."""
from __future__ import annotations

import pytest


def test_cli_version_entrypoint(capsys) -> None:
    """F-09: the entry point exposes --version."""
    from lokdocsqr import __version__
    from lokdocsqr.cli.main import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_cli_help_is_available() -> None:
    from lokdocsqr.cli.main import main

    assert main([]) == 0


def test_gui_main_raises() -> None:
    """gui.app.main must raise NotImplementedError in Phase 0."""
    from lokdocsqr.gui.app import main

    with pytest.raises(NotImplementedError):
        main()
    with pytest.raises(NotImplementedError):
        main([])
