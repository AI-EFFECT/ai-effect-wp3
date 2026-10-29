"""KPI calculator: the target metrics of every run, computed from the run's logged time series.

    experiment_runs.json  ──►  kpi.py  ──►  experiment_results_<id>.json
    (the runner's manifest)                 (study definition + the target metrics of every run)

Sits between whatever ran the experiments (the simulator, the lab test, or the synthetic stand-in) and the
analysis. It knows nothing about how the data came about: it reads the manifest (see synthetic_runner.py for
its layout), opens each run's time series, and for every metric in the study's ``target_metrics`` looks up
the KPI in the catalogue (kpi_catalogue.yaml) and applies its statistic to its signal. The catalogue is the
set of metrics a study can select from; a name that is not in it is an error, not a guess.

Statistics (STATISTICS): min, max, mean (of the samples), std (population), first, last, and integral
(trapezoid rule over the time column, so signal unit x s; an entry's divide_by turns it into the unit it wants).

Runs are kept apart by run_id: the metrics of run_07 come from the series the manifest lists for run_07. A
run that the runner reports as failed, or whose series cannot be used (missing column, gaps, non-finite
values, time not increasing), gets status "failed" with the reason in "error" and no metrics; the analysis
refuses a study with failed runs. Missing samples are not skipped over, because a gap in a log is something
to know about.

Output: experiment_results_<experiment_id>.json, the document the analysis reads
{
  "schema_version": "0.2", "experiment_id": ...,
  "source": { ...the manifest's source: where the data came from... },
  "kpis": {"<metric>": {"signal": ..., "statistic": ..., "unit": ..., "description": ...}, ...},   # the catalogue entries used
  "study_definition": { ...experiment_design.json, unchanged... },
  "results": [{"run_id": "run_00", "execution_id": "...", "status": "ok", "metrics": {"<metric>": 1.23}}, ...],
  "notes": [...]
}
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Callable

import numpy as np

SCHEMA_VERSION = "0.2"
OUTPUT_FILENAME = "experiment_results.json"
DEFAULT_CATALOGUE = pathlib.Path(__file__).with_name("kpi_catalogue.yaml")
ENTRY_KEYS = {"signal", "statistic", "divide_by", "unit", "description"}


def _integral(y: np.ndarray, t: np.ndarray) -> float:
    return float(np.sum((y[1:] + y[:-1]) / 2.0 * np.diff(t)))


# statistic -> function of (the signal, the time column)
STATISTICS: dict[str, Callable[[np.ndarray, np.ndarray], float]] = {
    "min": lambda y, t: float(y.min()),
    "max": lambda y, t: float(y.max()),
    "mean": lambda y, t: float(y.mean()),
    "std": lambda y, t: float(y.std()),
    "first": lambda y, t: float(y[0]),
    "last": lambda y, t: float(y[-1]),
    "integral": _integral,
}


def load_catalogue(path: str | pathlib.Path | None = None) -> dict[str, dict]:
    """The KPI catalogue: metric name -> its definition. Raises ValueError for a malformed entry."""
    import yaml

    catalogue = yaml.safe_load(pathlib.Path(path or DEFAULT_CATALOGUE).read_text(encoding="utf-8"))
    if not isinstance(catalogue, dict) or not catalogue:
        raise ValueError("the KPI catalogue is empty or not a mapping of metric names to definitions")
    for name, entry in catalogue.items():
        if not isinstance(entry, dict):
            raise ValueError(f"KPI {name!r}: expected a mapping")
        if unknown := set(entry) - ENTRY_KEYS:
            raise ValueError(f"KPI {name!r}: unknown keys {sorted(unknown)}; allowed: {sorted(ENTRY_KEYS)}")
        if not isinstance(entry.get("signal"), str):
            raise ValueError(f"KPI {name!r}: 'signal' (the column of the time series) is required")
        if entry.get("statistic") not in STATISTICS:
            raise ValueError(f"KPI {name!r}: 'statistic' must be one of {sorted(STATISTICS)}, got {entry.get('statistic')!r}")
        divisor = entry.get("divide_by", 1)
        if isinstance(divisor, bool) or not isinstance(divisor, (int, float)) or divisor == 0:
            raise ValueError(f"KPI {name!r}: 'divide_by' must be a non-zero number")
    return catalogue


def calculate(manifest: dict, base_dir: str | pathlib.Path, catalogue: dict[str, dict]) -> dict:
    """The experiment_results document for ``manifest`` (experiment_runs.json), whose series are found relative to ``base_dir``.

    Raises ValueError for a manifest that cannot be used at all or a metric that is not in the catalogue."""
    study, entries = manifest.get("study_definition"), manifest.get("runs")
    if not isinstance(study, dict) or not isinstance(entries, list) or not entries:
        raise ValueError("expected 'study_definition' and a non-empty 'runs' (experiment_runs.json from the runner)")
    metrics = list(study.get("target_metrics") or [])
    if not metrics:
        raise ValueError("the study names no target_metrics: the KPI calculator computes only what is asked for")
    if len(set(metrics)) != len(metrics):
        raise ValueError(f"target_metrics has duplicates: {metrics}")
    if missing := [m for m in metrics if m not in catalogue]:
        raise ValueError(f"not in the KPI catalogue: {missing}; available: {sorted(catalogue)}")
    run_ids = [r.get("run_id") for r in entries]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("the manifest lists a run_id more than once")
    if unknown := set(run_ids) - {r["run_id"] for r in study.get("runs", [])}:
        raise ValueError(f"the manifest has runs that the study does not: {sorted(unknown)}")

    base_dir = pathlib.Path(base_dir)
    results, notes = [], []
    for entry in entries:
        result = {"run_id": entry["run_id"], "execution_id": entry.get("execution_id"), "status": "ok", "metrics": {}}
        try:
            if entry.get("status", "ok") != "ok":
                raise ValueError(entry.get("error") or f"the runner reports status {entry['status']!r}")
            result["metrics"] = _metrics_of(entry, base_dir, {m: catalogue[m] for m in metrics})
        except (ValueError, OSError) as e:
            result["status"], result["error"] = "failed", str(e)
            notes.append(f"{entry['run_id']}: {e}")
        results.append(result)

    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": study.get("experiment_id"),
        "source": manifest.get("source"),
        "kpis": {m: dict(catalogue[m]) for m in metrics},
        "study_definition": study,
        "results": results,
        "notes": notes,
    }


def _metrics_of(entry: dict, base_dir: pathlib.Path, kpis: dict[str, dict]) -> dict[str, float]:
    """The metrics of one run from its time series. Raises ValueError (or OSError for a file that cannot be read)."""
    series = entry.get("timeseries")
    if not isinstance(series, dict) or series.get("format") != "csv" or not series.get("uri"):
        raise ValueError("no usable time series in the manifest (expected format 'csv' and a uri)")
    path = pathlib.Path(series["uri"])
    columns = _read_csv(path if path.is_absolute() else base_dir / path)

    time_column = series.get("time_column", "time_s")
    if time_column not in columns:
        raise ValueError(f"time column {time_column!r} not in the time series (columns: {sorted(columns)})")
    t = columns[time_column]
    if len(t) < 2 or not np.all(np.diff(t) > 0):
        raise ValueError("the time column has fewer than two rows or does not increase")

    values = {}
    for name, kpi in kpis.items():
        signal = kpi["signal"]
        if signal not in columns:
            raise ValueError(f"{name}: signal {signal!r} not in the time series (columns: {sorted(columns)})")
        y = columns[signal]
        if not np.all(np.isfinite(y)):
            raise ValueError(f"{name}: signal {signal!r} has {int((~np.isfinite(y)).sum())} missing or non-finite samples of {len(y)}")
        value = STATISTICS[kpi["statistic"]](y, t) / kpi.get("divide_by", 1)
        if not np.isfinite(value):
            raise ValueError(f"{name}: the {kpi['statistic']} of {signal!r} is not finite")
        values[name] = value
    return values


def _read_csv(path: pathlib.Path) -> dict[str, np.ndarray]:
    """A CSV with a header row, as columns of floats. 'nan' or empty cells become NaN."""
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    if len(set(header)) != len(header) or not all(header):
        raise ValueError(f"{path.name}: the header has empty or repeated column names")
    try:
        data = np.genfromtxt(path, delimiter=",", skip_header=1, dtype=float, ndmin=2)
    except ValueError as e:
        raise ValueError(f"{path.name}: cannot read the numbers ({e})") from e
    if data.size == 0 or data.shape[1] != len(header):
        raise ValueError(f"{path.name}: no rows, or rows with a different number of columns than the header ({len(header)})")
    return {name: data[:, i] for i, name in enumerate(header)}


def output_filename(experiment_id: Any) -> str:
    """experiment_results_<experiment_id>.json, with the id made safe for a file name (as in the DoE service)."""
    if experiment_id is None:
        return OUTPUT_FILENAME
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_.") else "_" for c in str(experiment_id))
    safe = safe.strip(".")[:100]
    return f"experiment_results_{safe}.json" if safe else OUTPUT_FILENAME


def run(manifest_path: str | pathlib.Path, out_dir: str | pathlib.Path, catalogue_path: str | pathlib.Path | None = None) -> pathlib.Path:
    """Read experiment_runs.json, write the results document into ``out_dir``; return its path."""
    manifest_path = pathlib.Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    document = calculate(manifest, manifest_path.parent, load_catalogue(catalogue_path))
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / output_filename(document["experiment_id"])
    path.write_text(json.dumps(document, indent=2, allow_nan=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("manifest", help="experiment_runs.json")
    parser.add_argument("--out-dir", default=".", help="where experiment_results_<id>.json goes")
    parser.add_argument("--catalogue", default=None, help=f"the KPI catalogue (default {DEFAULT_CATALOGUE.name} next to this file)")
    args = parser.parse_args()
    print(run(args.manifest, args.out_dir, args.catalogue))
