"""Tests for the synthetic runner and the KPI calculator, and for the chain design -> runner -> KPIs -> analysis.

    pip install -r requirements.txt pytest pyDOE SALib scipy scikit-learn statsmodels matplotlib
    pytest test_kpi.py

The designs are made with ../design_of_experiments/doe.py.
"""

import copy
import json
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).parent
SERVICES = HERE.parent
sys.path[:0] = [str(HERE), str(SERVICES / "synthetic_runner"), str(SERVICES / "design_of_experiments"), str(SERVICES / "doe_analysis")]

import analysis  # noqa: E402
import doe  # noqa: E402
import kpi  # noqa: E402
import synthetic_runner  # noqa: E402

THREE = {"p": {"a": [0.0, 1.0], "b": [0.0, 1.0], "c": [0.0, 1.0]}}
ALL_METRICS = ("min_return_temp_c", "heat_loss_kwh", "max_mass_flow_kg_s")
RUNNER = {"dt_s": 600.0, "sim_duration_h": 2}   # 13 rows per run


def design_of(doe_type="OAT", variations=THREE, metrics=ALL_METRICS, runner=RUNNER, **extra):
    baseline = {g: {k: sum(spec) / 2 for k, spec in params.items()} for g, params in variations.items()}
    if runner is not None:
        baseline["runner_parameters"] = dict(runner)
    return doe.design_experiment({"doe_type": doe_type, "variations_dict": variations, "entities_parameters": baseline,
                                  "target_metrics": list(metrics), **extra})


def write_design(tmp_path, design):
    path = tmp_path / "experiment_design.json"
    path.write_text(json.dumps(design), encoding="utf-8")
    return path


def run_chain(tmp_path, design, **kwargs):
    """The design through the runner and the KPI calculator: (the manifest path, the results document)."""
    manifest_path = synthetic_runner.run(write_design(tmp_path, design), tmp_path / "wf")
    results_path = kpi.run(manifest_path, tmp_path / "wf", **kwargs)
    return manifest_path, json.loads(results_path.read_text(encoding="utf-8"))


def u_of(design, run):
    return np.array([synthetic_runner._unit(f, run["factor_values"][f["name"]]) for f in design["factors"]])


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #
def test_the_runner_writes_one_series_per_run_and_a_manifest_that_links_them(tmp_path):
    design = design_of("OAT")
    manifest_path = synthetic_runner.run(write_design(tmp_path, design), tmp_path / "wf")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_path.name == synthetic_runner.manifest_filename(design["experiment_id"])
    assert manifest["source"]["kind"] == "synthetic"
    assert manifest["study_definition"] == design
    assert [r["run_id"] for r in manifest["runs"]] == [r["run_id"] for r in design["runs"]]
    for entry in manifest["runs"]:
        series = entry["timeseries"]
        assert series["uri"] == f"runs/{entry['run_id']}/timeseries.csv"       # relative to the manifest
        path = manifest_path.parent / series["uri"]
        header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
        assert header == ["time_s", *series["signals"]] == ["time_s", *synthetic_runner.SIGNALS]
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1 + series["n_rows"] == 14
    assert len({e["execution_id"] for e in manifest["runs"]}) == len(manifest["runs"])


def test_the_time_axis_is_the_recipes_runner_parameters(tmp_path):
    design = design_of("OAT", runner={"dt_s": 30.0, "sim_duration_h": 10})
    manifest_path = synthetic_runner.run(write_design(tmp_path, design), tmp_path)
    series = json.loads(manifest_path.read_text(encoding="utf-8"))["runs"][0]["timeseries"]
    t = np.loadtxt(manifest_path.parent / series["uri"], delimiter=",", skiprows=1)[:, 0]
    assert series["n_rows"] == 1201 and t[0] == 0 and t[-1] == 36000 and np.all(np.diff(t) == 30)


def test_without_runner_parameters_the_documented_defaults_are_used(tmp_path):
    design = design_of("OAT", runner=None)
    manifest_path = synthetic_runner.run(write_design(tmp_path, design), tmp_path)
    series = json.loads(manifest_path.read_text(encoding="utf-8"))["runs"][0]["timeseries"]
    assert series["n_rows"] == int(synthetic_runner.DEFAULT_DURATION_H * 3600 / synthetic_runner.DEFAULT_DT_S) + 1


def test_a_design_without_runs_is_refused(tmp_path):
    design = design_of("OAT")
    design["runs"] = []
    with pytest.raises(ValueError, match="no runs or factors"):
        synthetic_runner.run(write_design(tmp_path, design), tmp_path)


# --------------------------------------------------------------------------- #
# the KPI calculator recovers the model the series were built from
# --------------------------------------------------------------------------- #
def test_the_kpis_of_the_series_are_the_model_values(tmp_path):
    design = design_of("sobol", samples=8)
    _, document = run_chain(tmp_path, design)
    by_id = {r["run_id"]: r for r in document["results"]}
    for run in design["runs"]:
        u = u_of(design, run)
        metrics = by_id[run["run_id"]]["metrics"]
        assert metrics["min_return_temp_c"] == synthetic_runner._additive(u)                      # the lowest value, exactly
        assert metrics["max_mass_flow_kg_s"] == synthetic_runner._interaction(u)                  # the highest value, exactly
        assert metrics["heat_loss_kwh"] == pytest.approx(synthetic_runner._multiplicative(u), rel=1e-12)   # the integral


def test_the_results_document_is_what_the_analysis_reads(tmp_path):
    design = design_of("OAT")
    _, document = run_chain(tmp_path, design)
    assert document["study_definition"] == design
    assert document["source"]["kind"] == "synthetic"
    assert set(document["kpis"]) == set(ALL_METRICS) and document["kpis"]["heat_loss_kwh"]["statistic"] == "integral"
    assert all(r["status"] == "ok" and set(r["metrics"]) == set(ALL_METRICS) and r["execution_id"] for r in document["results"])


def test_metrics_follow_the_run_id_not_the_order_in_the_manifest(tmp_path):
    design = design_of("OAT")
    manifest_path = synthetic_runner.run(write_design(tmp_path, design), tmp_path / "wf")
    straight = kpi.calculate(json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path.parent, kpi.load_catalogue())

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runs"].reverse()
    shuffled = kpi.calculate(manifest, manifest_path.parent, kpi.load_catalogue())
    assert {r["run_id"]: r["metrics"] for r in shuffled["results"]} == {r["run_id"]: r["metrics"] for r in straight["results"]}


def test_only_the_requested_metrics_are_computed(tmp_path):
    design = design_of("OAT", metrics=("mean_mass_flow_kg_s", "final_return_temp_c"))
    _, document = run_chain(tmp_path, design)
    assert all(set(r["metrics"]) == {"mean_mass_flow_kg_s", "final_return_temp_c"} for r in document["results"])


def test_the_chain_ends_in_an_analysis_that_recovers_the_known_indices(tmp_path):
    design = design_of("sobol_indices", samples=512, metrics=("min_return_temp_c",))
    _, document = run_chain(tmp_path, design)
    report_path = analysis.run(tmp_path / "wf" / kpi.output_filename(document["experiment_id"]), tmp_path / "wf")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    a2 = np.array([10.0 / (i + 1) for i in range(3)]) ** 2     # S1_i = ST_i = a_i^2 / sum(a^2): the additive model
    expected = a2 / a2.sum()
    rows = {r["factor"]: r for r in report["results"]}
    for name, truth in zip((f["name"] for f in design["factors"]), expected):
        assert rows[name]["S1"] == pytest.approx(truth, abs=0.03) and rows[name]["ST"] == pytest.approx(truth, abs=0.03)


# --------------------------------------------------------------------------- #
# what goes wrong
# --------------------------------------------------------------------------- #
def manifest_of(tmp_path, design=None):
    path = synthetic_runner.run(write_design(tmp_path, design or design_of("OAT")), tmp_path / "wf")
    return json.loads(path.read_text(encoding="utf-8")), path.parent


def test_a_metric_that_is_not_in_the_catalogue_is_refused_with_the_available_ones(tmp_path):
    manifest, base = manifest_of(tmp_path, design_of("OAT", metrics=("min_return_temp_c", "efficiency")))
    with pytest.raises(ValueError, match=r"not in the KPI catalogue: \['efficiency'\].*min_return_temp_c"):
        kpi.calculate(manifest, base, kpi.load_catalogue())


def test_a_study_without_target_metrics_is_refused(tmp_path):
    manifest, base = manifest_of(tmp_path)
    manifest["study_definition"]["target_metrics"] = []
    with pytest.raises(ValueError, match="no target_metrics"):
        kpi.calculate(manifest, base, kpi.load_catalogue())


def test_a_run_the_runner_reports_as_failed_is_recorded_with_its_error(tmp_path):
    manifest, base = manifest_of(tmp_path)
    manifest["runs"][2].update(status="failed", error="the simulator did not converge", timeseries=None)
    document = kpi.calculate(manifest, base, kpi.load_catalogue())
    failed = document["results"][2]
    assert failed["status"] == "failed" and failed["error"] == "the simulator did not converge" and failed["metrics"] == {}
    assert all(r["status"] == "ok" for i, r in enumerate(document["results"]) if i != 2)
    assert document["notes"] == ["run_02: the simulator did not converge"]


def test_the_analysis_refuses_a_study_with_a_failed_run(tmp_path):
    manifest, base = manifest_of(tmp_path)
    manifest["runs"][0].update(status="failed", error="boom", timeseries=None)
    document = kpi.calculate(manifest, base, kpi.load_catalogue())
    with pytest.raises(ValueError, match="run_00"):
        analysis.analyze(document["study_definition"], document["results"], tmp_path / "out")


def rewrite_series(base, manifest, index, edit):
    path = base / manifest["runs"][index]["timeseries"]["uri"]
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(edit(lines)) + "\n", encoding="utf-8")


def test_a_gap_in_a_signal_fails_the_run_instead_of_being_skipped(tmp_path):
    manifest, base = manifest_of(tmp_path)

    def gap(lines):
        cells = lines[5].split(",")
        cells[1] = "nan"
        lines[5] = ",".join(cells)
        return lines

    rewrite_series(base, manifest, 1, gap)
    document = kpi.calculate(manifest, base, kpi.load_catalogue())
    assert document["results"][1]["status"] == "failed" and "1 missing or non-finite samples of 13" in document["results"][1]["error"]
    assert document["results"][0]["status"] == "ok"


def test_a_missing_signal_column_names_the_columns_there_are(tmp_path):
    manifest, base = manifest_of(tmp_path)
    rewrite_series(base, manifest, 0, lambda lines: [",".join(c for i, c in enumerate(l.split(",")) if i != 1) for l in lines])   # drops return_temp_c
    result = kpi.calculate(manifest, base, kpi.load_catalogue())["results"][0]
    assert result["status"] == "failed" and "signal 'return_temp_c' not in the time series" in result["error"] and "heat_loss_kw" in result["error"]


def test_time_that_does_not_increase_fails_the_run(tmp_path):
    manifest, base = manifest_of(tmp_path)
    rewrite_series(base, manifest, 0, lambda lines: lines[:3] + [lines[2]] + lines[3:])
    result = kpi.calculate(manifest, base, kpi.load_catalogue())["results"][0]
    assert result["status"] == "failed" and "does not increase" in result["error"]


def test_a_series_file_that_is_not_there_fails_that_run_only(tmp_path):
    manifest, base = manifest_of(tmp_path)
    (base / manifest["runs"][3]["timeseries"]["uri"]).unlink()
    document = kpi.calculate(manifest, base, kpi.load_catalogue())
    assert [r["status"] for r in document["results"]].count("failed") == 1 and document["results"][3]["status"] == "failed"


def test_a_manifest_run_the_study_does_not_have_is_refused(tmp_path):
    manifest, base = manifest_of(tmp_path)
    manifest["runs"][0]["run_id"] = "run_99"
    with pytest.raises(ValueError, match="run_99"):
        kpi.calculate(manifest, base, kpi.load_catalogue())


# --------------------------------------------------------------------------- #
# the statistics and the catalogue
# --------------------------------------------------------------------------- #
def test_the_statistics_on_a_series_that_can_be_checked_by_hand(tmp_path):
    t = np.array([0.0, 10.0, 30.0, 40.0])
    y = np.array([2.0, 4.0, 4.0, 0.0])
    assert kpi.STATISTICS["min"](y, t) == 0.0 and kpi.STATISTICS["max"](y, t) == 4.0
    assert kpi.STATISTICS["first"](y, t) == 2.0 and kpi.STATISTICS["last"](y, t) == 0.0
    assert kpi.STATISTICS["mean"](y, t) == 2.5 and kpi.STATISTICS["std"](y, t) == pytest.approx(np.std(y))
    assert kpi.STATISTICS["integral"](y, t) == 3 * 10 + 4 * 20 + 2 * 10     # trapezoids: (2+4)/2*10, (4+4)/2*20, (4+0)/2*10


def test_a_catalogue_entry_with_a_typo_or_an_unknown_statistic_is_refused(tmp_path):
    def catalogue(entry):
        path = tmp_path / "catalogue.yaml"
        path.write_text(json.dumps({"x": entry}), encoding="utf-8")   # JSON is YAML
        return path

    with pytest.raises(ValueError, match="unknown keys"):
        kpi.load_catalogue(catalogue({"signal": "s", "statistic": "max", "divde_by": 2}))
    with pytest.raises(ValueError, match="'statistic' must be one of"):
        kpi.load_catalogue(catalogue({"signal": "s", "statistic": "median"}))
    with pytest.raises(ValueError, match="'signal'"):
        kpi.load_catalogue(catalogue({"statistic": "max"}))
    with pytest.raises(ValueError, match="divide_by"):
        kpi.load_catalogue(catalogue({"signal": "s", "statistic": "max", "divide_by": 0}))


def test_the_shipped_catalogue_is_valid_and_every_entry_works_on_the_runners_signals(tmp_path):
    catalogue = kpi.load_catalogue()
    assert {e["signal"] for e in catalogue.values()} <= set(synthetic_runner.SIGNALS)
    design = design_of("OAT", metrics=tuple(catalogue))
    _, document = run_chain(tmp_path, design)
    assert all(r["status"] == "ok" and len(r["metrics"]) == len(catalogue) for r in document["results"])


def test_a_catalogue_of_your_own_can_point_at_other_columns(tmp_path):
    design = design_of("OAT", metrics=("peak_flow",))
    catalogue = tmp_path / "catalogue.yaml"
    catalogue.write_text(json.dumps({"peak_flow": {"signal": "mass_flow_kg_s", "statistic": "max", "unit": "kg/s"}}), encoding="utf-8")
    _, document = run_chain(tmp_path, design, catalogue_path=catalogue)
    assert all(r["metrics"]["peak_flow"] == synthetic_runner._interaction(u_of(design, run))
               for r, run in zip(document["results"], design["runs"]))
