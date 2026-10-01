"""Launcher refusals happen before hidden key input, database or HTTP activity."""

from unittest.mock import Mock

import pytest
from app import controlled_smoke


def test_without_explicit_live_flag_no_hidden_key_or_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No key, DB or HTTP allowed"))
    monkeypatch.setattr(controlled_smoke, "preflight", forbidden)
    monkeypatch.setattr(controlled_smoke, "_hidden_key", forbidden)
    monkeypatch.setattr("sys.argv", ["controlled_smoke", "run"])
    with pytest.raises(SystemExit, match="Explicit --approve-live"):
        controlled_smoke.main()
    forbidden.assert_not_called()


def test_output_outside_local_refused_before_reading_key(monkeypatch, tmp_path):
    forbidden = Mock(side_effect=AssertionError("No key, DB or HTTP allowed"))
    monkeypatch.setattr(controlled_smoke, "preflight", forbidden)
    monkeypatch.setattr(controlled_smoke, "_hidden_key", forbidden)
    with pytest.raises(RuntimeError, match="output_must_be_new_project_local_file"):
        controlled_smoke.smoke(tmp_path / "unapproved-output.json")
    forbidden.assert_not_called()
