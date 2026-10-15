# Sample preparation

Run this from the `ai-explainability` directory. Use an isolated environment;
the system Anaconda environment may contain binary packages built for NumPy 1.x.

```bash
python -m venv .venv-sample
.venv-sample/bin/python -m pip install --upgrade pip
.venv-sample/bin/python -m pip install -r scripts/requirements-sample.txt
.venv-sample/bin/python scripts/prepare-sample.py
```

The script downloads the upstream WP3 `raw_energy.csv`, creates a small
tabular dataset, trains a compatible Random Forest model, and writes generated
files under `assets/`. The generated model and data are ignored by Git.