from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def test_ml_turnout_training_and_export(tmp_path: Path) -> None:
    ml_script = Path("ml/train_turnout.py").resolve()
    spec = importlib.util.spec_from_file_location("train_turnout", ml_script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["train_turnout"] = mod
    spec.loader.exec_module(mod)

    out_file = tmp_path / "turnout_model.json"
    model = mod.train_and_evaluate(total_events=400, seed=42, output_path=out_file)
    assert model.calibrated_pinball_p50 <= model.phase1_pinball_p50
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["version"] == "m9-v1"
    assert data["low_rates"]["yes"] <= data["likely_rates"]["yes"] <= data["high_rates"]["yes"]
