"""Download the WP3 energy sample and create a runnable explainability model."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestRegressor


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "assets" / "data"
MODEL_DIR = ROOT / "assets" / "models"
SOURCE_URL = (
    "https://raw.githubusercontent.com/AI-EFFECT/ai-effect-wp3/main/"
    "use-cases/file_based_energy_pipeline/data/raw_energy.csv"
)
FEATURES = ["voltage", "current"]
TARGET = "power_consumption"


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    raw_path = DATA_DIR / "raw_energy.csv"
    with urlopen(SOURCE_URL) as response:
        raw_path.write_bytes(response.read())

    source = pd.read_csv(raw_path)
    missing = [column for column in [*FEATURES, TARGET] if column not in source]
    if missing:
        raise ValueError(f"Upstream sample is missing columns: {missing}")

    dataset = source[FEATURES].copy()
    dataset_path = DATA_DIR / "energy_data.csv"
    dataset.to_csv(dataset_path, index=False)

    model = RandomForestRegressor(n_estimators=32, random_state=42)
    model.fit(dataset, source[TARGET])
    model_path = MODEL_DIR / "energy_model.joblib"
    joblib.dump(model, model_path)

    config = {
        "analysis": "tabular",
        "model_type": "random_forest",
        "model_path": "models/energy_model.joblib",
        "dataset_path": "data/energy_data.csv",
        "feature_names": FEATURES,
        "target_index": 0,
        "save_excel": True,
    }
    (ROOT / "sample-config.json").write_text(json.dumps(config, indent=2) + "\n")
    print(f"Wrote {dataset_path}")
    print(f"Wrote {model_path}")
    print(f"Wrote {ROOT / 'sample-config.json'}")


if __name__ == "__main__":
    main()