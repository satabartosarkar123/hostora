from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hostora.cli import app
from hostora.data.loader import DataError, load_data


def test_real_data_loads() -> None:
    bundle = load_data(Path("data"))
    assert len(bundle.version) == 64
    assert len(bundle.rules) == 8
    assert "meat" in bundle.confirm.get("halal", {})
    assert len(bundle.channels) == 7
    assert len(bundle.semantics.substance_tags) >= 5
    assert len(bundle.tuning) >= 15
    assert "min_backtest_events" in bundle.tuning
    assert len(bundle.concept_map.get("open_food_facts_parents", {})) >= 20
    assert "open_food_facts" in bundle.sources


def test_missing_concept_map_fails(tmp_path: Path) -> None:
    shutil.copytree(Path("data"), tmp_path / "data")
    concept_map_file = tmp_path / "data" / "knowledge" / "concept_map.yaml"
    concept_map_file.unlink()

    with pytest.raises(DataError, match=r"concept_map\.yaml"):
        load_data(tmp_path / "data")


def test_channel_missing_q_placeholder_fails(tmp_path: Path) -> None:
    shutil.copytree(Path("data"), tmp_path / "data")
    channels_file = tmp_path / "data" / "commerce" / "channels.yaml"
    text = channels_file.read_text(encoding="utf-8").replace("{q}", "fixed")
    channels_file.write_text(text, encoding="utf-8")

    with pytest.raises(DataError, match=r"channels\.yaml"):
        load_data(tmp_path / "data")


def test_channel_invalid_eta_or_connect_fails(tmp_path: Path) -> None:
    shutil.copytree(Path("data"), tmp_path / "data")
    channels_file = tmp_path / "data" / "commerce" / "channels.yaml"
    orig = channels_file.read_text(encoding="utf-8")

    channels_file.write_text(
        orig.replace("eta_minutes: [10, 30]", "eta_minutes: [50, 10]"), encoding="utf-8"
    )
    with pytest.raises(DataError, match="eta_minutes"):
        load_data(tmp_path / "data")

    channels_file.write_text(
        orig.replace("connect: zomato_mcp", "connect: unknown_mcp"), encoding="utf-8"
    )
    with pytest.raises(DataError, match="invalid connect"):
        load_data(tmp_path / "data")


def test_cli_data_check_and_gen_keys() -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["data-check"])
    assert res.exit_code == 0
    assert "Rules: 8" in res.stdout
    assert "Channels: 7" in res.stdout

    keys_res = runner.invoke(app, ["gen-keys"])
    assert keys_res.exit_code == 0
    assert len(keys_res.stdout.strip().splitlines()) == 6
