"""Synthetic stand-in for the experiment runner and simulator: made-up time series for every run of a study.

THIS IS NOT A SIMULATION. Every number is a made-up function of the run's factor values;
this should be replaced with the real runner, which will run the recipe and log the signals over time.

    experiment_design.json  ──►  synthetic_runner.py  ──►  experiment_runs.json  +  runs/<run_id>/timeseries.csv
    (the study definition)                                 (the manifest)            (one log per run)

Emulates a real runner by running every recipe and recording what happened, as time
series. It does not compute metrics; that is the KPI calculator's job (kpi_calculator).

Where the data is
-----------------
Everything sits in one folder (output/<workflow_id>/), and the manifest links the data to the run ids with
paths relative to itself, so the folder can be moved or archived as it is:

  experiment_runs_<experiment_id>.json
  runs/run_00/timeseries.csv
  runs/run_01/timeseries.csv
  ...

timeseries.csv has one row per time step: a time column in seconds from the start of the run, then one
column per logged signal (the real runner's logger writes one row per tick in the same way).

The manifest (experiment_runs.json):

{
  "schema_version": "0.1", "experiment_id": ...,
  "source": {"kind": "synthetic", "note": "...", "signal_models": {"<signal>": "<the formula>", ...}},
  "study_definition": { ...experiment_design.json, unchanged... },
  "runs": [
    {"run_id": "run_00", "execution_id": "3f9c...", "status": "ok", "error": null,
     "timeseries": {"uri": "runs/run_00/timeseries.csv", "format": "csv", "time_column": "time_s",
                    "signals": ["return_temp_c", ...], "n_rows": 1201}},
    ...
  ]
}

A run that did not finish has status "failed", an error text and no timeseries. execution_id tells one
execution of a run from another (the same run_id can be executed again).

The signals and the time axis
-----------------------------
The time axis is the recipe's runner_parameters: dt_s and sim_duration_h (DEFAULT_DT_S and
DEFAULT_DURATION_H if it has none). The signals are fixed: SIGNALS. Each is built from one model of the
run's factor values, so that the truth is known. Each factor is first scaled to u in [0, 1] over its range
(min..max; mean +- 3 standard deviations for a normal factor; the rank among the values for a discrete
one). The signals take the models in turn:

  return_temp_c   a profile whose lowest value is  sum_i (10 / (i + 1)) u_i  (additive)
               min(return_temp_c) = the model; S1_i = ST_i = a_i^2 / sum(a^2) for uniform factors
  heat_loss_kw    a profile whose integral over the run is  product over the factors of (1 + (4 / i) u_i)  kWh  (multiplicative)
               increasing in every factor, with interactions (ST > S1). With E_i = 1 + b_i / 2, b_i = 4 / i and
               V_i = b_i^2 / 12: V = prod(E_i^2 + V_i) - prod(E_i^2),
               S1_i = V_i prod_{j != i}(E_j^2) / V, ST_i = V_i prod_{j != i}(E_j^2 + V_j) / V
  mass_flow_kg_s  a profile whose highest value is  20 (u1 - 1/2)(u2 - 1/2) + 2 u3  (interaction)
               S1 = 0, 0, 0.11 and ST = 0.89, 0.89, 0.11; further factors have no effect

so a KPI that takes the minimum of return_temp_c, the integral of heat_loss_kw (in kWh) or the maximum of
mass_flow_kg_s gets back exactly the model value, and the analysis can be checked against the indices above
(the interaction model needs three factors: with fewer, the factor numbers wrap around, and the values above
no longer hold). Any other KPI of these signals (a mean, a peak) depends on the profiles, which are fixed
shapes and carry no further information.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from typing import Any, Callable

import numpy as np

SCHEMA_VERSION = "0.1"
MANIFEST_FILENAME = "experiment_runs.json"
TIME_COLUMN = "time_s"
RUNS_DIR = "runs"
SERIES_FILENAME = "timeseries.csv"
DEFAULT_DT_S = 60.0
DEFAULT_DURATION_H = 6.0


def _additive(u: np.ndarray) -> float:
    return float(sum(10.0 / (i + 1) * ui for i, ui in enumerate(u)))


def _multiplicative(u: np.ndarray) -> float:
    value = 1.0
    for i, ui in enumerate(u):
        value *= 1 + 4.0 / (i + 1) * ui
    return float(value)


def _interaction(u: np.ndarray) -> float:
    a, b, c = (u[i % len(u)] for i in range(3))
    return float(20 * (a - 0.5) * (b - 0.5) + 2 * c)


# signal -> (the formula, the model of the factor values in [0, 1])
SIGNALS: dict[str, tuple[str, Callable[[np.ndarray], float]]] = {
    "return_temp_c": ("lowest value = additive: sum_i (10 / (i + 1)) u_i", _additive),
    "heat_loss_kw": ("integral in kWh = multiplicative: product over the factors of (1 + (4 / i) u_i)", _multiplicative),
    "mass_flow_kg_s": ("highest value = interaction: 20 (u1 - 1/2)(u2 - 1/2) + 2 u3", _interaction),
}


def _profiles(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Two fixed shapes over the run: a bowl that is exactly 0 at the start and at most 1, and a bump whose maximum is exactly 1."""
    x = np.linspace(0.0, 1.0, n)
    bowl = 0.5 * (1.0 - np.cos(2.0 * np.pi * x))
    bowl[0] = 0.0
    bowl /= bowl.max()
    bump = np.exp(-(((x - 0.4) / 0.15) ** 2))
    bump /= bump.max()
    return bowl, bump


def _time_axis(recipe: dict) -> np.ndarray:
    runner = recipe.get("runner_parameters") or {}
    dt = float(runner.get("dt_s") or DEFAULT_DT_S)
    duration = float(runner.get("sim_duration_h") or DEFAULT_DURATION_H) * 3600.0
    if dt <= 0 or duration < dt:
        raise ValueError(f"runner_parameters: dt_s={dt} and sim_duration_h={duration / 3600} do not give a time axis")
    return np.arange(round(duration / dt) + 1) * dt


def _signals(t: np.ndarray, u: np.ndarray) -> dict[str, np.ndarray]:
    """The logged signals over the time axis ``t`` for the factor values ``u`` on [0, 1]."""
    bowl, bump = _profiles(len(t))
    temp, loss, flow = (model(u) for _, model in SIGNALS.values())

    # lowest value = the model, at the start (the bowl is 0 there and above it elsewhere)
    return_temp = temp + 12.0 * bowl
    # highest value = the model, at the bump's peak
    mass_flow = flow - 0.3 * (1.0 - bump)
    # integral over the run = the model (in kWh): the mean power plus a swing whose integral is zero
    duration = t[-1] - t[0]
    mean_kw = loss * 3600.0 / duration
    swing = 0.4 * abs(mean_kw) * (bump - _trapezoid(bump, t) / duration)
    return {"return_temp_c": return_temp, "heat_loss_kw": mean_kw + swing, "mass_flow_kg_s": mass_flow}


def _trapezoid(y: np.ndarray, t: np.ndarray) -> float:
    return float(np.sum((y[1:] + y[:-1]) / 2.0 * np.diff(t)))


def _safe(name: Any) -> str:
    """A file or folder name from an id (as in the DoE service)."""
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_.") else "_" for c in str(name))
    return safe.strip(".")[:100]


def manifest_filename(experiment_id: Any) -> str:
    """experiment_runs_<experiment_id>.json, with the id made safe for a file name."""
    safe = _safe(experiment_id) if experiment_id is not None else ""
    return f"experiment_runs_{safe}.json" if safe else MANIFEST_FILENAME


def run(design_path: str | pathlib.Path, out_dir: str | pathlib.Path) -> pathlib.Path:
    """Read experiment_design.json, write the time series of every run and the manifest into ``out_dir``; return the manifest's path.

    Raises ValueError for a bad study."""
    study = json.loads(pathlib.Path(design_path).read_text(encoding="utf-8"))
    runs, factors = study.get("runs"), study.get("factors")
    if not runs or not factors:
        raise ValueError("the study has no runs or factors: expected experiment_design.json from the design-of-experiments service")
    folders = [_safe(r["run_id"]) for r in runs]
    if len(set(folders)) != len(folders) or not all(folders):
        raise ValueError("the run ids are not unique as folder names")

    out_dir = pathlib.Path(out_dir)
    entries = []
    for folder, run_ in zip(folders, runs):
        u = np.array([_unit(f, run_["factor_values"][f["name"]]) for f in factors])
        t = _time_axis(run_.get("recipe") or {})
        signals = _signals(t, u)

        relative = f"{RUNS_DIR}/{folder}/{SERIES_FILENAME}"
        (out_dir / RUNS_DIR / folder).mkdir(parents=True, exist_ok=True)
        np.savetxt(out_dir / relative, np.column_stack([t, *signals.values()]), delimiter=",", fmt="%.17g",
                   header=",".join([TIME_COLUMN, *signals]), comments="")
        entries.append({
            "run_id": run_["run_id"],
            "execution_id": uuid.uuid4().hex[:12],
            "status": "ok",
            "error": None,
            "timeseries": {"uri": relative, "format": "csv", "time_column": TIME_COLUMN, "signals": list(signals), "n_rows": len(t)},
        })

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": study.get("experiment_id"),
        "source": {
            "kind": "synthetic",
            "note": "made-up time series, not a simulation",
            "signal_models": {name: formula for name, (formula, _) in SIGNALS.items()},
        },
        "study_definition": study,
        "runs": entries,
    }
    path = out_dir / manifest_filename(study.get("experiment_id"))
    path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    return path


def _unit(factor: dict, value: float) -> float:
    """The factor value on [0, 1] over the factor's range."""
    kind = factor["distribution"]
    if kind == "uniform":
        low, high = factor["min"], factor["max"]
    elif kind == "norm":
        low, high = factor["mean"] - 3 * factor["stdvs"], factor["mean"] + 3 * factor["stdvs"]
    elif kind == "discrete":
        values = sorted(factor["set"])
        return values.index(value) / (len(values) - 1) if len(values) > 1 else 0.0
    else:
        raise ValueError(f"{factor['name']}: unknown distribution {kind!r}")
    return (value - low) / (high - low)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("design", help="experiment_design.json")
    parser.add_argument("--out-dir", default=".", help="where experiment_runs_<id>.json and runs/ go")
    args = parser.parse_args()
    print(run(args.design, args.out_dir))
