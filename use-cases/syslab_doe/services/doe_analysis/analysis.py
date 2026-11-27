"""Analysis of the results of a design-of-experiments study.

Based on toolbox_analysis.py of the ERIGrid 2.0 "Toolbox with DoE and SA methods",
https://github.com/ERIGrid2/toolbox_doe_sa, MIT License, Copyright (c) 2023 ERIGrid 2.0
(license text: LICENSE-ERIGrid-toolbox in the use case folder). The toolbox in turn uses code from
ERIGrid/NA4-SummerSchool-DTU-2018 v2.0 (https://zenodo.org/record/2837928),
Copyright (c) 2019 Tue Vissing Jensen, on behalf of the Technical University of Denmark.
Modified for the AI-Effect orchestrator, as a single self-contained module that reads and
writes JSON files: the Sobol, FAST, OAT and ANOVA calculations follow the original; added are
a MANOVA, a second meta-model (the original is kept as meta_model="original") and the report files.

    experiment_results.json  ──►  analysis.py  ──►  analysis_report_<id>.json  (+ analysis_summary.csv, figures)

Input: experiment_results.json
------------------------------
The study definition (the experiment_design.json of the design-of-experiments service,
unchanged) plus one result per run, from whatever executed and evaluated the runs
(the KPI calculator, from the time series of the simulator, the lab test, or the synthetic stand-in):

{
  "study_definition": { ...experiment_design.json... },
  "results": [
    {"run_id": "run_00", "execution_id": "3f9c...", "status": "ok",
     "metrics": {"min_return_temp_c": 44.8, "heat_loss_kwh": 12.1}},
    ...
  ]
}

``results`` are matched to the study's runs by ``run_id``, not by position. The metrics
analysed are the study's ``target_metrics``, or every numeric metric of the results (in their order) if the
study names none. Every run must have status "ok".

The analysis is chosen by the design's doe_type, as in the original toolbox:

  doe_type         analysis        what it reports, per metric
  sobol_indices    sobol_indices   S1 and ST per factor with 95% confidence intervals (SALib)
  fast             fast            S1 and ST per factor with 95% confidence intervals (SALib, eFAST)
  OAT              oat_variance    metric at min / baseline / max per factor, its variance, and a
                                   ranking of the factors over all metrics
  extreme_points   anova           one-way ANOVA per factor, metric at min vs at max: F and p; with more
                                   than one metric also the MANOVA (statsmodels, as in the original):
                                   rows with metric "all" and the four test statistics per factor
  sobol, LHS       meta_model      Gaussian-process fit on all factors: R2 on the runs and
                                   cross-validated, kernel length scales, and a figure of the
                                   metric over the first two factors
  anything else    not supported (ValueError)

For sobol and LHS, analyze(..., meta_model="original") runs the original toolbox's meta-model instead
(_meta_model_original): its fit on the first two factors only, with its kernel, its optimizer settings
and its 50 x 50 surface. The default, "current", is the fit described above.

Output: analysis_report_<experiment_id>.json
{
  "schema_version": "0.2", "experiment_id": ..., "doe_type": ..., "analysis": "sobol_indices",
  "n_runs": 80, "factors": [...], "metrics": [...],
  "summary": ["min_return_temp_c: ...", ...],   # the findings in words
  "results": [ {"metric": ..., "factor": ..., <numbers for this analysis>}, ... ],
  "figures": ["analysis/....png"],              # relative to the report
  "notes": [...]
}
"""

from __future__ import annotations

import csv
import json
import math
import pathlib
import statistics
import warnings
from typing import Any

import numpy as np

SCHEMA_VERSION = "0.2"
REPORT_FILENAME = "analysis_report.json"
SUMMARY_FILENAME = "analysis_summary.csv"
FIGURE_DIR = "analysis"
ALPHA = 0.05
# Fixed, so the confidence intervals (bootstrapped by SALib) are the same on every run.
# Must be non-zero: SALib treats seed=0 like no seed at all.
BOOTSTRAP_SEED = 1


def analyze(study: dict, results: list[dict], out_dir: str | pathlib.Path, meta_model: str = "current") -> dict:
    """Analyse ``results`` for the ``study`` (an experiment design); figures go into ``out_dir``/analysis.

    ``meta_model`` picks the fit for doe_type sobol and LHS: "current", or "original" for the original toolbox's.
    Raises ValueError for bad input.
    """
    method = study.get("doe_type")
    if method not in ANALYSES:
        raise ValueError(f"no analysis is defined for doe_type {method!r} (supported: {list(ANALYSES)})")
    if meta_model not in ("current", "original"):
        raise ValueError(f"meta_model must be 'current' or 'original', got {meta_model!r}")
    runs, factors = study.get("runs"), study.get("factors")
    if not runs or not factors:
        raise ValueError("the study has no runs or factors: expected experiment_design.json from the design-of-experiments service")
    if not isinstance(results, list) or not results:
        raise ValueError("the results are empty: expected one result per run")

    names = [f["name"] for f in factors]
    metrics, y = _metric_table(study, results)
    x = np.array([[run["factor_values"][n] for n in names] for run in runs], dtype=float)

    out_dir = pathlib.Path(out_dir)
    notes: list[str] = []
    analysis_function = _meta_model_original if meta_model == "original" and ANALYSES[method] is _meta_model else ANALYSES[method]
    analysis_name, rows, figures = analysis_function(study, factors, x, metrics, y, out_dir, notes)
    figures += _plot_scatter(out_dir, factors, x, metrics, y)
    rows = _clean(rows)
    constant = [m for j, m in enumerate(metrics) if _is_constant(y[:, j])]
    if constant:
        notes.append(f"{constant} has the same value in every run (to rounding): no factor affects it, and its "
                     "indices, F and p are null or meaningless.")

    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": study.get("experiment_id"),
        "doe_type": method,
        "analysis": analysis_name,
        "n_runs": len(runs),
        "factors": names,
        "metrics": metrics,
        "summary": _summarise(analysis_name, rows, metrics, constant),
        "results": rows,
        "figures": figures,
        "notes": notes,
    }


def report_filename(experiment_id: Any) -> str:
    """analysis_report_<experiment_id>.json, with the id made safe for a file name (as in the DoE service)."""
    if experiment_id is None:
        return REPORT_FILENAME
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_.") else "_" for c in str(experiment_id))
    safe = safe.strip(".")[:100]
    return f"analysis_report_{safe}.json" if safe else REPORT_FILENAME


def run(results_path: str | pathlib.Path, out_dir: str | pathlib.Path, meta_model: str = "current") -> pathlib.Path:
    """Read experiment_results.json, write the report and analysis_summary.csv into ``out_dir``; return the report path.

    ``meta_model`` is passed on to analyze()."""
    document = json.loads(pathlib.Path(results_path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or "study_definition" not in document or "results" not in document:
        raise ValueError("expected an object with 'study_definition' and 'results' (experiment_results.json)")
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = analyze(document["study_definition"], document["results"], out_dir, meta_model=meta_model)

    path = out_dir / report_filename(report["experiment_id"])
    path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    columns = list(dict.fromkeys(key for row in report["results"] for key in row))
    with open(out_dir / SUMMARY_FILENAME, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(report["results"])
    return path


# --------------------------------------------------------------------------- #
# analyses: each returns (name, result rows, figure paths)
# --------------------------------------------------------------------------- #
def _sobol_indices(study, factors, x, metrics, y, out_dir, notes):
    from SALib.analyze import sobol

    k = len(factors)
    if len(x) % (k + 2):
        raise ValueError(f"sobol_indices needs N (k + 2) runs, got {len(x)} runs for {k} factors")
    problem = _salib_problem(factors)
    rows = []
    for j, metric in enumerate(metrics):
        if _is_constant(y[:, j]):
            rows.extend(_no_indices(metric, problem["names"]))
            continue
        si = sobol.analyze(problem, y[:, j], calc_second_order=False, conf_level=0.95, print_to_console=False,
                           seed=BOOTSTRAP_SEED)
        for i, name in enumerate(problem["names"]):
            rows.append({"metric": metric, "factor": name, "S1": si["S1"][i], "S1_conf": si["S1_conf"][i],
                         "ST": si["ST"][i], "ST_conf": si["ST_conf"][i]})
    notes.append("Run order matters: rows must be in the design's run order (the Saltelli sample structure).")
    return "sobol_indices", rows, _plot_indices(out_dir, metrics, rows)


def _fast(study, factors, x, metrics, y, out_dir, notes):
    from SALib.analyze import fast

    problem = _salib_problem(factors)
    if len(x) % len(factors):
        raise ValueError(f"fast needs N k runs, got {len(x)} runs for {len(factors)} factors")
    rows = []
    for j, metric in enumerate(metrics):
        if _is_constant(y[:, j]):
            rows.extend(_no_indices(metric, problem["names"]))
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)  # SALib's note on the intervals, passed on in the report notes
            si = fast.analyze(problem, y[:, j], M=4, print_to_console=False, seed=BOOTSTRAP_SEED)
        for i, name in enumerate(problem["names"]):
            rows.append({"metric": metric, "factor": name, "S1": si["S1"][i], "S1_conf": si["S1_conf"][i],
                         "ST": si["ST"][i], "ST_conf": si["ST_conf"][i]})
    notes.append("SALib warns that the FAST confidence intervals (S1_conf, ST_conf) come from bootstrap resampling, "
                 "which is unreliable for this method: treat them as indicative only.")
    return "fast", rows, _plot_indices(out_dir, metrics, rows)


def _oat_variance(study, factors, x, metrics, y, out_dir, notes):
    k = len(factors)
    if len(x) != 1 + 2 * k:
        raise ValueError(f"OAT needs 1 + 2k runs, got {len(x)} runs for {k} factors")
    rows, rank_sum = [], {f["name"]: 0 for f in factors}
    for j, metric in enumerate(metrics):
        base = y[0, j]
        per_factor = []
        for i, f in enumerate(factors):
            low, high = y[1 + 2 * i, j], y[2 + 2 * i, j]
            per_factor.append({"metric": metric, "factor": f["name"], "at_min": low, "baseline": base, "at_max": high,
                               "effect": high - low, "variance": statistics.variance([low, base, high])})
        for rank, row in enumerate(sorted(per_factor, key=lambda r: -r["variance"])):
            rank_sum[row["factor"]] += rank
        rows.extend(per_factor)
    ranking = [{"metric": "all", "factor": name, "rank_sum": total} for name, total in sorted(rank_sum.items(), key=lambda kv: kv[1])]
    notes.append("rank_sum: for each metric the factors are ranked by variance (0 = largest); the ranks are added up over "
                 "all metrics, so the smallest rank_sum is the most influential factor overall.")
    return "oat_variance", rows + ranking, _plot_oat(out_dir, metrics, rows)


def _anova(study, factors, x, metrics, y, out_dir, notes):
    from scipy import stats

    rows = []
    for i, f in enumerate(factors):
        low, high = x[:, i] == f["min"], x[:, i] == f["max"]
        for j, metric in enumerate(metrics):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                f_value, p_value = stats.f_oneway(y[low, j], y[high, j])
            if _is_constant(y[:, j]):
                f_value = p_value = float("nan")
            mean_low, mean_high = y[low, j].mean(), y[high, j].mean()
            rows.append({"metric": metric, "factor": f["name"], "mean_at_min": mean_low, "mean_at_max": mean_high,
                         "effect": mean_high - mean_low, "F": f_value, "p": p_value,
                         "significant": bool(p_value < ALPHA) if math.isfinite(p_value) else None})
    notes.append(f"p < {ALPHA}: the metric differs between the factor at its min and at its max, i.e. the factor has a "
                 "significant effect. F and p are null where the metric does not vary at all.")
    notes.append("effect: mean of the metric over the runs with the factor at its max minus at its min.")
    figures = _plot_effects(out_dir, metrics, rows)
    if len(metrics) > 1:
        rows += _manova(factors, x, metrics, y, notes)
    return "anova", rows, figures


def _manova(factors, x, metrics, y, notes):
    """MANOVA as in the original toolbox (statsmodels): do the factors have a joint effect on several metrics?

    Same call as the original: the metrics are the endog, the factor values the exog (no intercept). Metrics that
    do not vary are left out, since they make the covariance singular. Returns rows with metric "all", one per
    factor and test (Wilks' lambda, Pillai's trace, Hotelling-Lawley trace, Roy's greatest root).
    """
    import pandas as pd
    from statsmodels.multivariate.manova import MANOVA

    varying = [j for j in range(len(metrics)) if not _is_constant(y[:, j])]
    left_out = [metrics[j] for j in range(len(metrics)) if j not in varying]
    if left_out:
        notes.append(f"MANOVA: {left_out} left out, because it has the same value in every run.")
    if len(varying) < 2:
        notes.append("MANOVA skipped: it needs at least two metrics that vary.")
        return []
    names = [f["name"] for f in factors]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fitted = MANOVA(endog=pd.DataFrame(y[:, varying], columns=[metrics[j] for j in varying]),
                            exog=pd.DataFrame(x, columns=names)).mv_test()
    except (ValueError, np.linalg.LinAlgError) as e:
        notes.append(f"MANOVA could not be computed: {e}")
        return []
    rows = []
    for i, factor in enumerate(names):
        if f"x{i}" not in fitted.results:  # statsmodels keys the factors x0, x1, ... in the order of the exog columns
            continue
        stat = fitted[f"x{i}"]["stat"]
        for test in stat.index:
            p_value = float(stat.loc[test, "Pr > F"])
            rows.append({"metric": "all", "factor": factor, "test": test, "value": float(stat.loc[test, "Value"]),
                         "num_df": float(stat.loc[test, "Num DF"]), "den_df": float(stat.loc[test, "Den DF"]),
                         "F": float(stat.loc[test, "F Value"]), "p": p_value,
                         "significant": bool(p_value < ALPHA) if math.isfinite(p_value) else None})
    notes.append(f"MANOVA (rows with metric 'all'): tests whether a factor has a joint effect on the metrics together "
                 f"(p < {ALPHA}), over {[metrics[j] for j in varying]}.")
    return rows


def _meta_model(study, factors, x, metrics, y, out_dir, notes):
    plt = _pyplot()
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C, WhiteKernel
    from sklearn.metrics import r2_score
    from sklearn.model_selection import KFold, cross_val_predict

    k, n = len(factors), len(x)
    if n < 4:
        raise ValueError(f"the meta-model needs at least 4 runs, got {n}")
    lows = np.array([_bounds(f)[0] for f in factors])
    spans = np.array([_bounds(f)[1] - _bounds(f)[0] for f in factors])
    unit = (x - lows) / spans  # every factor on [0, 1], so the kernel length scales are comparable

    def make_model():
        kernel = C(1.0, (1e-3, 1e3)) * RBF([1.0] * k, (1e-2, 1e2)) + WhiteKernel(1e-6, (1e-10, 1e-1))
        return GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=3, random_state=0)

    figure_dir = out_dir / FIGURE_DIR
    figure_dir.mkdir(parents=True, exist_ok=True)
    rows, figures = [], []
    for j, metric in enumerate(metrics):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            model = make_model().fit(unit, y[:, j])
            r2_cv = None
            if n >= 10:
                predicted = cross_val_predict(make_model(), unit, y[:, j], cv=KFold(5, shuffle=True, random_state=0))
                r2_cv = r2_score(y[:, j], predicted)
        scales = np.atleast_1d(model.kernel_.k1.k2.length_scale)
        rows.append({"metric": metric, "r2_train": r2_score(y[:, j], model.predict(unit)), "r2_cv": r2_cv,
                     **{f"length_scale[{f['name']}]": float(s) for f, s in zip(factors, scales)}})

        fig_path = figure_dir / f"{_safe(metric)}_meta_model.png"
        _plot_meta_model(plt, model, unit, y[:, j], factors, lows, spans, metric, fig_path)
        figures.append(f"{FIGURE_DIR}/{fig_path.name}")
    notes.append("Gaussian-process fit of each metric on all factors (inputs scaled to [0, 1]). r2_cv is 5-fold "
                 "cross-validated (null below 10 runs). length_scale: shorter = the metric changes faster along that factor.")
    if k > 2:
        notes.append(f"Figures show the first two factors with the other {k - 2} held at the middle of their range.")
    return "meta_model", rows, figures


def _meta_model_original(study, factors, x, metrics, y, out_dir, notes, random_state=None):
    """The original toolbox's meta-model (toolbox_analysis.do_sobol_analysis), with its choices kept as they are.

    Only the first two factors, on their raw values; Gaussian process with the kernel
    C(1, (1e-3, 1e3)) * RBF(10, (1e-2, 1e2)) and 9 optimizer restarts; the surface on a 50 x 50 grid from each
    factor's min to max (mean +- 3 standard deviations for a normal factor), with the runs drawn as red stars.
    With a single factor it only plots the metric against it, as the original does. The original has no seed,
    so ``random_state=None`` gives a slightly different fit on every call; pass an integer to fix it.
    """
    plt = _pyplot()
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C

    figure_dir = out_dir / FIGURE_DIR
    figure_dir.mkdir(parents=True, exist_ok=True)
    rows, figures = [], []
    for j, metric in enumerate(metrics):
        path = figure_dir / f"{_safe(metric)}_meta_model_original.png"
        if len(factors) == 1:
            order = np.argsort(x[:, 0])
            fig, ax = plt.subplots()
            ax.plot(x[order, 0], y[order, j])
            ax.set_xlabel(factors[0]["name"])
            ax.set_ylabel(metric)
            kernel_text = None
        else:
            exog, endog = x[:, :2], y[:, [j]]
            kernel = C(1.0, (1e-3, 1e3)) * RBF(10, (1e-2, 1e2))
            gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=9, random_state=random_state)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                gp.fit(exog, endog)
            (low1, high1), (low2, high2) = _bounds(factors[0]), _bounds(factors[1])
            x_vec, y_vec = np.meshgrid(np.linspace(low1, high1, 50), np.linspace(low2, high2, 50))
            evalgrid = np.array([x_vec.flatten(), y_vec.flatten()]).T
            scipred, sigma = gp.predict(evalgrid, return_std=True)
            fig = plt.figure()
            ax = plt.axes(projection="3d")
            ax.set_xlabel(factors[0]["name"])
            ax.set_ylabel(factors[1]["name"])
            ax.set_zlabel(metric)
            ax.plot_surface(x_vec, y_vec, np.reshape(scipred, (50, 50)))
            ax.plot(exog[:, 0], exog[:, 1], endog.ravel(), "r*")
            kernel_text = str(gp.kernel_)
        fig.savefig(path, dpi=100)
        plt.close(fig)
        figures.append(f"{FIGURE_DIR}/{path.name}")
        rows.append({"metric": metric, "factors": [f["name"] for f in factors[:2]], "kernel": kernel_text})
    notes.append("Original toolbox meta-model: a Gaussian process on the first two factors only, on their raw values. "
                 "Without a seed the fit changes slightly from call to call. With a single factor it is only a plot of the data.")
    if len(factors) > 2:
        notes.append(f"The other {len(factors) - 2} factors are not used.")
    return "meta_model_original", rows, figures


ANALYSES = {
    "sobol_indices": _sobol_indices,
    "fast": _fast,
    "OAT": _oat_variance,
    "extreme_points": _anova,
    "sobol": _meta_model,
    "LHS": _meta_model,
}


# --------------------------------------------------------------------------- #
# the findings in words
# --------------------------------------------------------------------------- #
def _summarise(analysis: str, rows: list[dict], metrics: list[str], constant: list[str]) -> list[str]:
    lines = []
    for metric in metrics:
        mine = [r for r in rows if r["metric"] == metric]
        if metric in constant:
            lines.append(f"{metric}: the same value in every run, so no factor affects it.")
        elif analysis in ("sobol_indices", "fast"):
            top = max(mine, key=lambda r: r["ST"])
            first_order = sum(r["S1"] for r in mine)
            others = [r for r in sorted(mine, key=lambda r: -r["ST"]) if r is not top and r["ST"] >= 0.05]
            line = f"{metric}: {top['factor']} is the most influential factor (S1 {top['S1']:.2f}, ST {top['ST']:.2f})"
            if others:
                line += "; also " + ", ".join(f"{r['factor']} (ST {r['ST']:.2f})" for r in others)
            line += ". " + (f"Interactions between factors account for about {1 - first_order:.0%} of the variance."
                            if first_order < 0.95 else "The effects are almost purely additive (no noticeable interactions).")
            lines.append(line)
        elif analysis == "oat_variance":
            top = max(mine, key=lambda r: r["variance"])
            lines.append(f"{metric}: {top['factor']} moves it most ({top['at_min']:.4g} at its min, "
                         f"{top['baseline']:.4g} at baseline, {top['at_max']:.4g} at its max).")
        elif analysis == "anova":
            significant = [r for r in mine if r["significant"]]
            if significant:
                lines.append(f"{metric}: significant effect (p < {ALPHA}) from " + ", ".join(
                    f"{r['factor']} ({r['effect']:+.4g} from min to max)" for r in sorted(significant, key=lambda r: r["p"])) + ".")
            else:
                lines.append(f"{metric}: no factor has a significant effect on its own (p < {ALPHA}); this test does not see "
                             "effects that only appear in combination with other factors.")
        elif analysis == "meta_model":
            (row,) = mine
            scales = {k[len("length_scale["):-1]: v for k, v in row.items() if k.startswith("length_scale[")}
            fit = f"R2 {row['r2_cv']:.2f} cross-validated" if row["r2_cv"] is not None else f"R2 {row['r2_train']:.2f} on the runs"
            lines.append(f"{metric}: the Gaussian-process fit has {fit}; it changes fastest along {min(scales, key=scales.get)}.")
        elif analysis == "meta_model_original":
            (row,) = mine
            lines.append(f"{metric}: original-toolbox Gaussian-process surface over {' and '.join(row['factors'])} (see the figure).")
    if analysis == "oat_variance":
        ranking = [r["factor"] for r in rows if r["metric"] == "all"]
        lines.append("Overall ranking over all metrics, most to least influential: " + ", ".join(ranking) + ".")
    if analysis == "anova":
        joint = [r for r in rows if r["metric"] == "all" and r.get("test") == "Pillai's trace"]
        if joint:
            hits = [r["factor"] for r in joint if r["significant"]]
            lines.append("All metrics together (MANOVA, Pillai's trace): " + (
                f"significant joint effect (p < {ALPHA}) from " + ", ".join(hits) if hits
                else f"no factor has a significant joint effect (p < {ALPHA})") + ".")
    return lines


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _metric_table(study: dict, results: list[dict]) -> tuple[list[str], np.ndarray]:
    """Metric names and a (runs x metrics) array in the study's run order."""
    by_id = {}
    for row in results:
        if not isinstance(row, dict) or "run_id" not in row:
            raise ValueError("every result must be an object with a 'run_id'")
        by_id[str(row["run_id"])] = row

    ids = [str(run["run_id"]) for run in study["runs"]]
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ValueError(f"the results have no entry for {len(missing)} of {len(ids)} runs ({missing[:5]}...)")
    failed = [i for i in ids if by_id[i].get("status", "ok") != "ok"]
    if failed:
        raise ValueError(f"{len(failed)} of {len(ids)} runs did not finish ok ({failed[:5]}...): the designs need every run")

    def metrics_of(run_id: str) -> dict:
        found = by_id[run_id].get("metrics")
        if not isinstance(found, dict):
            raise ValueError(f"run {run_id}: the result has no 'metrics' object")
        return found

    metrics = study.get("target_metrics") or list(dict.fromkeys(  # in the order the results list them
        key for i in ids for key, v in metrics_of(i).items() if _is_number(v)))
    if not metrics:
        raise ValueError("no metrics to analyse: the study has no target_metrics and the results have no numeric metrics")
    absent = [m for m in metrics if any(m not in metrics_of(i) for i in ids)]
    if absent:
        raise ValueError(f"the results are missing the metric(s) {absent}")

    y = np.empty((len(ids), len(metrics)))
    for r, run_id in enumerate(ids):
        for c, metric in enumerate(metrics):
            value = metrics_of(run_id)[metric]
            if not _is_number(value) or not math.isfinite(value):
                raise ValueError(f"run {run_id}: metric {metric!r} is not a finite number ({value!r})")
            y[r, c] = value
    return list(metrics), y


def _bounds(factor: dict) -> tuple[float, float]:
    """Range of a factor; for a normal distribution, mean +- 3 standard deviations (as in the original toolbox)."""
    if factor["distribution"] == "uniform":
        return factor["min"], factor["max"]
    if factor["distribution"] == "norm":
        return factor["mean"] - 3 * factor["stdvs"], factor["mean"] + 3 * factor["stdvs"]
    raise ValueError(f"{factor['name']}: {factor['distribution']} factors cannot be analysed")


def _salib_problem(factors: list[dict]) -> dict:
    return {
        "num_vars": len(factors),
        "names": [f["name"] for f in factors],
        "dists": ["unif" if f["distribution"] == "uniform" else "norm" for f in factors],
        "bounds": [[f["min"], f["max"]] if f["distribution"] == "uniform" else [f["mean"], f["stdvs"]] for f in factors],
    }


# --------------------------------------------------------------------------- #
# figures (PNG, written into out_dir/analysis; the functions return paths relative to out_dir)
# --------------------------------------------------------------------------- #
def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _save(plt, fig, out_dir: pathlib.Path, name: str) -> str:
    folder = out_dir / FIGURE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(folder / name, dpi=100)
    plt.close(fig)
    return f"{FIGURE_DIR}/{name}"


def _plot_scatter(out_dir, factors, x, metrics, y) -> list[str]:
    """Every run as a point: one panel per factor, the metric on the vertical axis."""
    plt = _pyplot()
    k = len(factors)
    cols = min(k, 3)
    rows_n = math.ceil(k / cols)
    paths = []
    for j, metric in enumerate(metrics):
        fig, axes = plt.subplots(rows_n, cols, figsize=(4.2 * cols, 3.4 * rows_n), squeeze=False, sharey=True)
        for i, f in enumerate(factors):
            ax = axes[i // cols][i % cols]
            ax.plot(x[:, i], y[:, j], "o", ms=3.5, alpha=0.6)
            ax.set_xlabel(f["name"])
            if i % cols == 0:
                ax.set_ylabel(metric)
        for i in range(k, rows_n * cols):
            axes[i // cols][i % cols].set_visible(False)
        fig.suptitle(f"{metric}: every run against each factor")
        paths.append(_save(plt, fig, out_dir, f"{_safe(metric)}_vs_factors.png"))
    return paths


def _plot_indices(out_dir, metrics, rows) -> list[str]:
    """S1 and ST per factor, with the confidence intervals, one figure per metric."""
    plt = _pyplot()
    paths = []
    for metric in metrics:
        mine = [r for r in rows if r["metric"] == metric]
        if mine[0]["S1"] is None:  # the metric does not vary: no indices
            continue
        pos = np.arange(len(mine))
        fig, ax = plt.subplots(figsize=(max(5, 1.3 * len(mine) + 2), 4))
        ax.bar(pos - 0.2, [r["S1"] for r in mine], 0.4, yerr=[r["S1_conf"] for r in mine], capsize=3, label="S1 (first order)")
        ax.bar(pos + 0.2, [r["ST"] for r in mine], 0.4, yerr=[r["ST_conf"] for r in mine], capsize=3, label="ST (total)")
        ax.set_xticks(pos, [r["factor"] for r in mine], rotation=30, ha="right")
        ax.set_ylabel("share of the variance")
        ax.set_title(f"{metric}: Sobol indices")
        ax.legend()
        paths.append(_save(plt, fig, out_dir, f"{_safe(metric)}_indices.png"))
    return paths


def _plot_oat(out_dir, metrics, rows) -> list[str]:
    """Tornado chart: the metric at each factor's min and max, around the baseline run."""
    plt = _pyplot()
    paths = []
    for metric in metrics:
        mine = sorted((r for r in rows if r["metric"] == metric), key=lambda r: r["variance"])
        pos = np.arange(len(mine))
        base = mine[0]["baseline"]
        fig, ax = plt.subplots(figsize=(7, max(3, 0.6 * len(mine) + 1.5)))
        ax.barh(pos, [r["at_max"] - base for r in mine], left=base, height=0.4, label="factor at its max")
        ax.barh(pos, [r["at_min"] - base for r in mine], left=base, height=0.4, alpha=0.6, label="factor at its min")
        ax.axvline(base, color="0.3", lw=1)
        ax.set_yticks(pos, [r["factor"] for r in mine])
        ax.set_xlabel(f"{metric} (line: baseline run)")
        ax.set_title(f"{metric}: one factor at a time")
        ax.legend()
        paths.append(_save(plt, fig, out_dir, f"{_safe(metric)}_oat.png"))
    return paths


def _plot_effects(out_dir, metrics, rows) -> list[str]:
    """Main effect per factor: the mean of the metric with the factor at its min and at its max."""
    plt = _pyplot()
    paths = []
    for metric in metrics:
        mine = [r for r in rows if r["metric"] == metric]
        fig, ax = plt.subplots(figsize=(max(5, 1.3 * len(mine) + 2), 4))
        for r in mine:
            ax.plot([0, 1], [r["mean_at_min"], r["mean_at_max"]], "o-", label=r["factor"])
        ax.set_xticks([0, 1], ["factor at min", "factor at max"])
        ax.set_ylabel(f"mean {metric}")
        ax.set_title(f"{metric}: main effects")
        ax.legend(fontsize=8)
        paths.append(_save(plt, fig, out_dir, f"{_safe(metric)}_effects.png"))
    return paths


def _plot_meta_model(plt, model, unit, values, factors, lows, spans, metric, path) -> None:
    k = len(factors)
    grid = np.linspace(0, 1, 40)
    fig = plt.figure(figsize=(6, 5))
    if k == 1:
        ax = fig.add_subplot()
        ax.plot(lows[0] + grid * spans[0], model.predict(grid[:, None]), label="meta-model")
        ax.plot(lows[0] + unit[:, 0] * spans[0], values, "r*", label="runs")
        ax.set_xlabel(factors[0]["name"])
        ax.set_ylabel(metric)
        ax.legend()
    else:
        gx, gy = np.meshgrid(grid, grid)
        points = np.full((gx.size, k), 0.5)
        points[:, 0], points[:, 1] = gx.ravel(), gy.ravel()
        surface = model.predict(points).reshape(gx.shape)
        ax = fig.add_subplot(projection="3d")
        ax.plot_surface(lows[0] + gx * spans[0], lows[1] + gy * spans[1], surface, alpha=0.8)
        ax.plot(lows[0] + unit[:, 0] * spans[0], lows[1] + unit[:, 1] * spans[1], values, "r*")
        ax.set_xlabel(factors[0]["name"])
        ax.set_ylabel(factors[1]["name"])
        ax.set_zlabel(metric)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _no_indices(metric: str, names: list[str]) -> list[dict]:
    """Index rows of a metric that does not vary: nothing to attribute."""
    return [{"metric": metric, "factor": name, "S1": None, "S1_conf": None, "ST": None, "ST_conf": None} for name in names]


def _is_constant(values: np.ndarray) -> bool:
    """True if every value is the same up to rounding (relative to the size of the values)."""
    return bool(np.ptp(values) <= 1e-9 * max(1.0, float(np.abs(values).max())))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clean(value: Any) -> Any:
    """Plain JSON types: numpy scalars to Python, NaN and infinity to None."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
