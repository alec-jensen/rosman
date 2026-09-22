from unittest.mock import patch

from rosman import __main__ as entrypoint


def test_version_fast_path_does_not_import_cli(capsys):
    with patch.dict("sys.modules", {"rosman.cli": None}):
        assert entrypoint.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("rosman ")


def test_completion_fast_path_does_not_import_cli(monkeypatch):
    monkeypatch.setattr(entrypoint, "_complete", lambda words: 17)
    with patch.dict("sys.modules", {"rosman.cli": None}):
        assert entrypoint.main(["__complete", "topic", "ec"]) == 17


def test_normal_command_delegates_to_cli(monkeypatch):
    import rosman.cli

    monkeypatch.setattr(rosman.cli, "main", lambda argv: 23)
    assert entrypoint.main(["help"]) == 23


def test_passthrough_uses_fast_path_without_importing_cli(monkeypatch):
    import rosman.fast_dispatch

    monkeypatch.setattr(rosman.fast_dispatch, "try_fast_passthrough", lambda argv: 31)
    with patch.dict("sys.modules", {"rosman.cli": None}):
        assert entrypoint.main(["topic", "list"]) == 31
