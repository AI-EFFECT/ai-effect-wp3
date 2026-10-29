"""Tests for the request format of the DoE service (doe.py and service.py): doe_conf, nested factor paths,
the baseline check, YAML input and the template.

    pip install -r requirements.txt pytest
    pytest test_request_format.py

The sampling methods themselves are not tested here: they are unchanged from the original toolbox, and
the requests used to compare them with the original script are in the old (two-level) layout.
"""

import base64
import copy
import json
import pathlib
import sys

import pytest
import yaml

HERE = pathlib.Path(__file__).parent
USE_CASE = HERE.parent.parent
sys.path[:0] = [str(HERE), str(USE_CASE.parent)]  # doe.py and service.py, and common/

import doe  # noqa: E402
import service  # noqa: E402

TEMPLATE_PATH = USE_CASE / "requests" / "doe_request_template.yaml"
TEMPLATE = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))


def template(**changes):
    """A copy of the template, with changes applied on top."""
    request = copy.deepcopy(TEMPLATE)
    request.update(changes)
    return request


# --------------------------------------------------------------------------- #
# the template
# --------------------------------------------------------------------------- #
def test_the_template_as_it_is_gives_a_design():
    design = doe.design_experiment(TEMPLATE)

    assert design["doe_type"] == "sobol" and design["n_runs"] == 16
    assert [f["name"] for f in design["factors"]] == [
        "input_timeseries.q_demand_kw.scale",
        "input_timeseries.t_supply_setpoint_c.final",
        "controller_parameters.sequence_step_duration",
        "physical_model_parameters.pipe_length_km",
        "physical_model_parameters.t_ambient_c",
    ]
    assert (design["scenario_name"], design["target"]) == ("template_sobol", "simulation")
    assert design["schema_version"] == "0.3"


def test_factor_values_land_at_their_nested_paths_and_the_rest_of_the_baseline_is_untouched():
    design = doe.design_experiment(template())
    run = design["runs"][1]

    expected = copy.deepcopy(TEMPLATE["entities_parameters"])
    for name, value in run["factor_values"].items():
        node = expected
        *groups, leaf = name.split(".")
        for key in groups:
            node = node[key]
        node[leaf] = value
    recipe = {k: v for k, v in run["recipe"].items() if k not in ("scenario_name", "target", "ID")}

    assert recipe == expected


def test_the_topology_and_the_time_series_definitions_are_passed_on_untouched():
    recipe = doe.design_experiment(template())["runs"][3]["recipe"]

    assert recipe["physical_model_parameters"]["topology"] == TEMPLATE["entities_parameters"]["physical_model_parameters"]["topology"]
    series = recipe["input_timeseries"]["q_demand_kw"]
    assert (series["type"], series["reference"], series["column"], series["interpolation"]) == (
        "timeseries", "profiles/demand_profile.csv", "q_kw", "hold")


def test_the_controller_type_and_its_setpoint_list_are_passed_on_and_only_the_numeric_parameter_varies():
    design = doe.design_experiment(template())

    durations = set()
    for run in design["runs"]:
        controller = run["recipe"]["controller_parameters"]
        assert controller["type"] == "sequence" and controller["sequence_setpoints"] == [40.0, 60.0, 90.0]
        durations.add(controller["sequence_step_duration"])
    assert len(durations) > 1 and min(durations) >= 900 and max(durations) <= 3600


def test_the_controllers_setpoint_list_cannot_be_varied_directly():
    request = template()
    request["variations_dict"]["controller_parameters"]["sequence_setpoints"] = [30.0, 50.0]

    with pytest.raises(ValueError, match=r"controller_parameters\.sequence_setpoints: the baseline value .* is not a number"):
        doe.design_experiment(request)


def test_a_null_in_the_baseline_means_not_set_and_a_factor_replaces_it():
    request = template()
    assert request["entities_parameters"]["physical_model_parameters"]["pipe_length_km"] is None

    design = doe.design_experiment(request)

    lengths = {run["recipe"]["physical_model_parameters"]["pipe_length_km"] for run in design["runs"]}
    assert None not in lengths and min(lengths) >= 0.25 and max(lengths) <= 0.35
    assert design["runs"][0]["recipe"]["physical_model_parameters"]["pipe_inner_diameter_mm"] is None  # not varied: stays null


def test_the_ids_and_the_run_settings_are_in_every_recipe():
    recipe = doe.design_experiment(template())["runs"][2]["recipe"]

    assert recipe["ID"] == "02"
    assert (recipe["scenario_name"], recipe["target"]) == ("template_sobol", "simulation")


# --------------------------------------------------------------------------- #
# doe_conf and the old top-level keys
# --------------------------------------------------------------------------- #
OLD_STYLE = {
    "experiment_id": "old", "doe_type": "sobol_indices", "samples": 8, "seed": 5,
    "variations_dict": {"forecast": {"q_peak_kw": [150.0, 250.0]}, "physical_model": {"t_ambient_c": [0.0, 20.0]}},
    "entities_parameters": {"forecast": {"q_peak_kw": 200.0}, "physical_model": {"t_ambient_c": 10.0, "pipe_sections": 30}},
    "basic_conf": {"scenario_name": "old_scenario"},
}


def new_style_of(old):
    new = {k: v for k, v in old.items() if k not in ("doe_type", "samples", "seed", "add_extreme_points", "basic_conf")}
    new["doe_conf"] = {k: old[k] for k in ("doe_type", "samples", "seed") if k in old}
    new["scenario_name"] = old["basic_conf"]["scenario_name"]
    return new


def test_the_new_layout_samples_exactly_what_the_old_layout_does():
    old = doe.design_experiment(OLD_STYLE)
    new = doe.design_experiment(new_style_of(OLD_STYLE))

    assert [r["factor_values"] for r in new["runs"]] == [r["factor_values"] for r in old["runs"]]
    assert [r["recipe"] for r in new["runs"]] == [r["recipe"] for r in old["runs"]]
    assert new["scenario_name"] == old["scenario_name"] == "old_scenario"


def test_the_old_top_level_settings_still_work():
    design = doe.design_experiment(OLD_STYLE)

    assert (design["doe_type"], design["samples_requested"], design["seed"]) == ("sobol_indices", 8, 5)
    assert design["n_runs"] == 8 * (2 + 2)


def test_doe_conf_wins_over_a_top_level_key():
    request = {**OLD_STYLE, "samples": 4, "doe_conf": {"samples": 8}}

    assert doe.design_experiment(request)["samples_requested"] == 8


@pytest.mark.parametrize("key", ["doe_conf", "basic_conf"])
def test_doe_conf_and_basic_conf_must_be_objects(key):
    with pytest.raises(ValueError, match=f"'{key}' must be an object"):
        doe.design_experiment({**OLD_STYLE, key: 3})


# --------------------------------------------------------------------------- #
# nested variations and the baseline check
# --------------------------------------------------------------------------- #
def nested_request(variations, baseline, **extra):
    return {"doe_type": "OAT", "variations_dict": variations, "entities_parameters": baseline, **extra}


def test_factors_can_be_any_depth_and_are_named_by_their_full_path():
    request = nested_request(
        {"a": {"b": {"c": {"d": [1, 3]}}, "e": [0, 1]}, "f": [10, 20]},
        {"a": {"b": {"c": {"d": 2}}, "e": 0.5}, "f": 15})

    design = doe.design_experiment(request)

    assert [f["name"] for f in design["factors"]] == ["a.b.c.d", "a.e", "f"]
    assert design["factors"][0]["path"] == ["a", "b", "c", "d"]
    assert design["runs"][1]["recipe"]["a"]["b"]["c"]["d"] == 1
    assert design["runs"][2]["recipe"]["a"]["b"]["c"]["d"] == 3
    assert design["runs"][3]["recipe"]["a"]["e"] == 0
    assert design["runs"][5]["recipe"]["f"] == 10


def test_normal_and_discrete_specs_are_leaves_not_groups():
    request = {"doe_type": "distribution_and_discrete", "samples": 3, "seed": 1,
               "variations_dict": {"g": {"x": {"type": "norm", "mean": 5, "stdvs": 1}, "n": {"type": "discrete", "set": [1, 2, 3]}}},
               "entities_parameters": {"g": {"x": 5.0, "n": 2}}}

    design = doe.design_experiment(request)

    assert [f["distribution"] for f in design["factors"]] == ["norm", "discrete"]
    assert design["n_runs"] == 9


def test_a_factor_that_the_baseline_does_not_list_is_refused_with_its_path():
    request = nested_request({"a": {"b": [0, 1]}}, {"a": {"other": 1}})

    with pytest.raises(ValueError, match=r"a\.b: not listed in entities_parameters"):
        doe.design_experiment(request)


def test_a_missing_group_is_refused_too():
    with pytest.raises(ValueError, match=r"x\.y: not listed"):
        doe.design_experiment(nested_request({"x": {"y": [0, 1]}}, {"a": {"b": 1}}))


def test_no_baseline_at_all_is_refused():
    with pytest.raises(ValueError, match="not listed in entities_parameters"):
        doe.design_experiment({"doe_type": "OAT", "variations_dict": {"a": {"b": [0, 1]}}})


@pytest.mark.parametrize("value", ["text", [1, 2, 3], {"type": "step"}, True])
def test_a_baseline_value_that_is_not_a_number_cannot_be_varied(value):
    with pytest.raises(ValueError, match="is not a number or null"):
        doe.design_experiment(nested_request({"a": {"b": [0, 1]}}, {"a": {"b": value}}))


def test_oat_with_a_null_baseline_leaves_it_unset_in_the_baseline_run_and_says_so():
    design = doe.design_experiment(nested_request({"a": {"b": [0, 1]}}, {"a": {"b": None}}))

    assert design["runs"][0]["recipe"]["a"]["b"] is None
    assert [r["recipe"]["a"]["b"] for r in design["runs"][1:]] == [0, 1]
    assert any("no baseline for a.b" in note for note in design["notes"])


@pytest.mark.parametrize("variations, message", [
    ({"a": {}}, "a: expected"),
    ({"a": 5}, "a: expected"),
    ({"a": {"b": "x"}}, r"a\.b: expected"),
    ({}, "non-empty object"),
    ({"a": {"b": [1]}}, r"a\.b: a range must be \[min, max\]"),
    ({"a": {"b": [2, 1]}}, "min must be smaller than max"),
    ({"a": {"b": {"type": "weird"}}}, "unknown factor type"),
])
def test_bad_variations_are_refused_with_the_path(variations, message):
    with pytest.raises(ValueError, match=message):
        doe.design_experiment(nested_request(variations, {"a": {"b": 1}}))


# --------------------------------------------------------------------------- #
# reading the request
# --------------------------------------------------------------------------- #
def test_parse_request_text_reads_json_and_yaml():
    assert doe.parse_request_text('{"a": {"b": 1}}') == {"a": {"b": 1}}
    assert doe.parse_request_text("a:\n  b: 1\n  c: null\n") == {"a": {"b": 1, "c": None}}
    assert doe.parse_request_text(TEMPLATE_PATH.read_text(encoding="utf-8")) == TEMPLATE


def test_parse_request_text_refuses_text_that_is_neither():
    with pytest.raises(ValueError, match="neither valid JSON nor valid YAML"):
        doe.parse_request_text("a: [1, 2\nb: }")


def test_the_command_line_takes_a_yaml_request(tmp_path, capsys):
    request = tmp_path / "request.yaml"
    request.write_text(yaml.safe_dump(template()))

    code = doe.main(["--request", str(request), "--out-dir", str(tmp_path / "out")])

    assert code == 0
    assert (tmp_path / "out" / "experiment_design_template-sobol.json").exists()
    assert json.loads(capsys.readouterr().out)["metadata"]["n_runs"] == 16


# --------------------------------------------------------------------------- #
# service.py
# --------------------------------------------------------------------------- #
def reference(text, fmt):
    return {"protocol": "inline", "uri": base64.b64encode(text.encode()).decode(), "format": fmt}


def test_the_service_reads_an_inline_yaml_request():
    request = service._load_doe_request([reference(yaml.safe_dump(template()), "yaml")])

    assert request["doe_conf"]["doe_type"] == "sobol"


def test_the_service_reads_yaml_labelled_binary_as_the_orchestrator_requires():
    request = service._load_doe_request([reference(yaml.safe_dump(template()), "binary")])

    assert request["doe_conf"]["doe_type"] == "sobol"


def test_the_service_reads_an_inline_json_request():
    assert service._load_doe_request([reference(json.dumps(OLD_STYLE), "json")])["doe_type"] == "sobol_indices"


def test_the_service_reads_a_file_request(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text(yaml.safe_dump(template()))

    request = service._load_doe_request([{"protocol": "file", "uri": str(path), "format": "yaml"}])

    assert request["experiment_id"] == "template-sobol"


def test_the_service_uses_the_default_request_without_inputs(monkeypatch):
    monkeypatch.setattr(service, "DEFAULT_REQUEST", USE_CASE / "requests" / "oat.yaml")

    assert service._load_doe_request([])["doe_conf"]["doe_type"] == "OAT"


def test_the_service_refuses_inputs_it_cannot_read():
    with pytest.raises(ValueError, match="No DoE request in the inputs"):
        service._load_doe_request([{"protocol": "grpc", "uri": "x", "format": "json"}])


def test_the_whole_service_writes_a_design_from_a_yaml_request(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "OUTPUT_DIR", tmp_path)
    request = service.ExecuteRequest(method="DesignExperiment", workflow_id="wf-test", task_id="t",
                                     inputs=[reference(yaml.safe_dump(template()), "yaml")])

    response = service.execute_DesignExperiment(request)

    assert response.status == "complete"
    design = json.loads(pathlib.Path(response.output.uri).read_text(encoding="utf-8"))
    assert design["n_runs"] == 16 and design["target"] == "simulation"
