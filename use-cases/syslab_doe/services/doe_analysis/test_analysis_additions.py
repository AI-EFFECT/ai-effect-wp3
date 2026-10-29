"""Tests for the MANOVA and for the original toolbox's meta-model in analysis.py.

    pip install -r requirements.txt pytest pyDOE
    pytest test_analysis_additions.py

The designs are made with ../design_of_experiments/doe.py (which needs pyDOE for LHS).
"""

import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent / "design_of_experiments")]

import analysis  # noqa: E402
import doe  # noqa: E402

THREE = {"p": {"a": [0.0, 1.0], "b": [0.0, 1.0], "c": [0.0, 1.0]}}
TWO = {"p": {"a": [0.0, 2.0], "b": [10.0, 20.0]}}


def design_of(doe_type, variations, metrics=("m1", "m2"), **extra):
    def middle(spec):
        return spec["mean"] if isinstance(spec, dict) else sum(spec) / 2

    baseline = {g: {k: middle(spec) for k, spec in params.items()} for g, params in variations.items()}
    return doe.design_experiment({"doe_type": doe_type, "variations_dict": variations, "entities_parameters": baseline,
                                  "target_metrics": list(metrics), **extra})


def results_of(design, **functions):
    """One result per run; each metric is a function of the factor values in design order."""
    return [{"run_id": run["run_id"], "execution_id": f"e{n}", "status": "ok",
             "metrics": {m: float(f(*run["factor_values"].values())) for m, f in functions.items()}}
            for n, run in enumerate(design["runs"])]


def two_metrics(design):
    return results_of(design, m1=lambda a, b, c: 3 * a + b + 0.1 * c ** 2, m2=lambda a, b, c: a * b + 0.5 * c)


# --------------------------------------------------------------------------- #
# MANOVA
# --------------------------------------------------------------------------- #
def manova_rows(report):
    return [r for r in report["results"] if r["metric"] == "all"]


def test_anova_with_two_metrics_also_reports_the_manova(tmp_path):
    design = design_of("extreme_points", THREE)

    report = analysis.analyze(design, two_metrics(design), tmp_path)

    rows = manova_rows(report)
    assert report["analysis"] == "anova"
    assert len(rows) == 3 * 4  # three factors, four tests each
    assert {r["test"] for r in rows} == {"Wilks' lambda", "Pillai's trace", "Hotelling-Lawley trace", "Roy's greatest root"}
    assert [r["factor"] for r in rows[:4]] == ["p.a"] * 4
    assert all(set(r) >= {"value", "num_df", "den_df", "F", "p", "significant"} for r in rows)
    assert any("MANOVA" in n for n in report["notes"])


def test_the_manova_numbers_are_those_of_statsmodels_called_as_the_original_calls_it(tmp_path):
    import pandas as pd
    from statsmodels.multivariate.manova import MANOVA

    design = design_of("extreme_points", THREE)
    results = two_metrics(design)
    x = np.array([[run["factor_values"][n] for n in ("p.a", "p.b", "p.c")] for run in design["runs"]])
    y = np.array([[r["metrics"]["m1"], r["metrics"]["m2"]] for r in results])
    expected = MANOVA(endog=pd.DataFrame(y, columns=["m1", "m2"]), exog=pd.DataFrame(x, columns=["p.a", "p.b", "p.c"])).mv_test()

    report = analysis.analyze(design, results, tmp_path)

    for i, factor in enumerate(["p.a", "p.b", "p.c"]):
        stat = expected[f"x{i}"]["stat"]
        for r in (r for r in manova_rows(report) if r["factor"] == factor):
            assert r["value"] == pytest.approx(stat.loc[r["test"], "Value"])
            assert r["p"] == pytest.approx(stat.loc[r["test"], "Pr > F"])


def test_a_single_metric_gets_no_manova(tmp_path):
    design = design_of("extreme_points", THREE, metrics=("m1",))

    report = analysis.analyze(design, results_of(design, m1=lambda a, b, c: a + b), tmp_path)

    assert manova_rows(report) == []


def test_a_metric_that_does_not_vary_is_left_out_of_the_manova(tmp_path):
    design = design_of("extreme_points", THREE, metrics=("m1", "m2", "flat"))
    results = results_of(design, m1=lambda a, b, c: 3 * a + b + 0.1 * c ** 2, m2=lambda a, b, c: a * b + 0.5 * c,
                         flat=lambda a, b, c: 5.0)

    report = analysis.analyze(design, results, tmp_path)

    assert len(manova_rows(report)) == 12
    assert any("flat" in n and "left out" in n for n in report["notes"])


def test_the_manova_is_skipped_when_fewer_than_two_metrics_vary(tmp_path):
    design = design_of("extreme_points", THREE, metrics=("m1", "flat"))

    report = analysis.analyze(design, results_of(design, m1=lambda a, b, c: a, flat=lambda a, b, c: 1.0), tmp_path)

    assert manova_rows(report) == [] and any("MANOVA skipped" in n for n in report["notes"])


def test_the_manova_summary_names_the_factors_with_a_joint_effect(tmp_path):
    variations = {"p": {k: [0.0, 1.0] for k in "abcd"}}
    design = design_of("extreme_points", variations)
    # a drives both metrics strongly, the others only a little, and not in the same way (so the metrics are not collinear)
    results = results_of(design, m1=lambda a, b, c, d: 10 * a + 0.2 * b, m2=lambda a, b, c, d: -4 * a + 0.3 * c - 0.2 * b * d)

    report = analysis.analyze(design, results, tmp_path)

    line = next(s for s in report["summary"] if s.startswith("All metrics together"))
    assert "significant joint effect" in line and "p.a" in line


def test_the_manova_failing_does_not_stop_the_analysis(tmp_path):
    design = design_of("extreme_points", {"p": {"a": [0.0, 1.0]}})  # two runs only: nothing to test with

    report = analysis.analyze(design, results_of(design, m1=lambda a: a, m2=lambda a: 2 * a), tmp_path)

    assert report["analysis"] == "anova"
    assert manova_rows(report) == []
    assert any("MANOVA could not be computed" in n for n in report["notes"])


# --------------------------------------------------------------------------- #
# the original meta-model
# --------------------------------------------------------------------------- #
def smooth(a, b):
    return a ** 2 + 0.5 * b


def test_the_default_meta_model_is_still_the_current_one(tmp_path):
    design = design_of("sobol", TWO, samples=16, add_extreme_points=True)

    report = analysis.analyze(design, results_of(design, m1=smooth, m2=lambda a, b: a + b), tmp_path)

    assert report["analysis"] == "meta_model"
    assert "m1_meta_model.png" in [pathlib.Path(f).name for f in report["figures"]]
    assert "r2_train" in report["results"][0]


@pytest.mark.parametrize("doe_type, extra", [("sobol", dict(samples=16)),
                                             ("LHS", dict(samples=12, seed=3))])
def test_the_original_meta_model_can_be_asked_for_on_sobol_and_lhs(tmp_path, doe_type, extra):
    variations = TWO if doe_type == "sobol" else {"p": {"a": {"type": "norm", "mean": 1, "stdvs": 0.2},
                                                       "b": {"type": "norm", "mean": 15, "stdvs": 2}}}
    design = design_of(doe_type, variations, **extra)

    report = analysis.analyze(design, results_of(design, m1=smooth, m2=lambda a, b: a + b), tmp_path, meta_model="original")

    assert report["analysis"] == "meta_model_original"
    names = [pathlib.Path(f).name for f in report["figures"]]
    assert "m1_meta_model_original.png" in names and "m2_meta_model_original.png" in names
    assert all((tmp_path / f).stat().st_size > 1000 for f in report["figures"])
    assert report["results"][0]["factors"] == ["p.a", "p.b"] and "RBF" in report["results"][0]["kernel"]
    assert any("original-toolbox Gaussian-process surface over p.a and p.b" in s for s in report["summary"])


def test_the_original_meta_model_fits_what_the_original_code_fits(tmp_path):
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C

    design = design_of("sobol", TWO, metrics=("m1",), samples=16)
    results = results_of(design, m1=smooth)
    x = np.array([[run["factor_values"]["p.a"], run["factor_values"]["p.b"]] for run in design["runs"]])
    y = np.array([[r["metrics"]["m1"]] for r in results])
    gp = GaussianProcessRegressor(kernel=C(1.0, (1e-3, 1e3)) * RBF(10, (1e-2, 1e2)), n_restarts_optimizer=9, random_state=0)
    gp.fit(x, y)

    rows = analysis._meta_model_original(design, design["factors"], x, ["m1"], y, tmp_path, [], random_state=0)[1]

    assert rows[0]["kernel"] == str(gp.kernel_)


def test_the_original_meta_model_uses_only_the_first_two_factors_and_says_so(tmp_path):
    design = design_of("sobol", THREE, metrics=("m1",), samples=16)

    report = analysis.analyze(design, results_of(design, m1=lambda a, b, c: a + b + c), tmp_path, meta_model="original")

    assert report["results"][0]["factors"] == ["p.a", "p.b"]
    assert any("The other 1 factors are not used" in n for n in report["notes"])


def test_the_original_meta_model_with_one_factor_only_plots_the_data(tmp_path):
    design = design_of("sobol", {"p": {"a": [0.0, 2.0]}}, metrics=("m1",), samples=8)

    report = analysis.analyze(design, results_of(design, m1=lambda a: a ** 2), tmp_path, meta_model="original")

    assert report["analysis"] == "meta_model_original"
    assert report["results"][0]["kernel"] is None
    assert (tmp_path / report["figures"][0]).stat().st_size > 1000


def test_asking_for_the_original_meta_model_changes_nothing_for_other_methods(tmp_path):
    design = design_of("OAT", TWO)

    report = analysis.analyze(design, results_of(design, m1=smooth, m2=lambda a, b: a + b), tmp_path, meta_model="original")

    assert report["analysis"] == "oat_variance"


def test_an_unknown_meta_model_is_refused(tmp_path):
    design = design_of("sobol", TWO, samples=8)

    with pytest.raises(ValueError, match="meta_model must be 'current' or 'original'"):
        analysis.analyze(design, results_of(design, m1=smooth, m2=smooth), tmp_path, meta_model="fancy")
