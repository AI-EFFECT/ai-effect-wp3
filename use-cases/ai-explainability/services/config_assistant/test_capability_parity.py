"""Guard every assistant intake path against drift from the actual explainer."""

import ast
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_explainability.api import _KNOWN_KWARGS
from ai_explainability.capabilities import (
    CONFIG_FIELDS,
    LSTM_EXPLAINERS,
    MODEL_ROUTES,
    NEURAL_EXPLAINERS,
)
from analysis.tabular import _MODEL_ROUTES, _load_explainer_class as load_tabular
from analysis.timeseries import _load_explainer_class as load_timeseries
from services.config_assistant.service import (
    MODEL_ALIASES,
    SYSTEM_PROMPT,
    TABULAR_MODELS,
    TIMESERIES_MODELS,
    _ask_assistant,
    _normalize_config,
    _parse_assistant_response,
    _prepare_input,
)

TOOLBOX = Path(__file__).resolve().parents[1] / "explainability_runner" / "toolbox"
OPTION_VALUES = {
    "package": "pytorch",
    "framework": "torch",
    "feature_names": ["Age", "Income", "Employment"],
    "target_index": [0, 1],
    "output_labels": {"0": "prediction"},
    "output_dir": "output",
    "save_excel": True,
    "generate_notebook": True,
    "dataset_scope": "subset",
    "subset_end": 3,
    "explainer_type": "gradient",
    "background_size": 3,
    "kernel_nsamples": 8,
    "input_dim": 3,
    "hidden_size": 4,
    "look_back": 2,
    "look_ahead": 1,
    "explain_subset": 3,
    "background_data_path": "data/background.pt",
    "test_data_path": "data/test.pt",
    "dataset_path": "data/data.csv",
}


def runnable_config(analysis, model_type):
    config = {
        "analysis": analysis,
        "model_type": model_type,
        "model_path": "models/model.pkl",
    }
    if analysis == "tabular":
        config["dataset_path"] = "data/data.csv"
    if model_type == "lstm":
        config.update(
            background_data_path="data/background.pt",
            test_data_path="data/test.pt",
        )
    return config


class CapabilityParityTests(unittest.TestCase):
    def test_model_sets_and_real_dispatch_share_the_contract(self):
        self.assertEqual(TABULAR_MODELS, set(MODEL_ROUTES["tabular"]))
        self.assertEqual(TIMESERIES_MODELS, set(MODEL_ROUTES["timeseries"]))
        self.assertIs(_MODEL_ROUTES, MODEL_ROUTES["tabular"])
        for analysis, routes in MODEL_ROUTES.items():
            for model_type, (module, name) in routes.items():
                with self.subTest(analysis=analysis, model=model_type):
                    sentinel = object()
                    loader = load_tabular if analysis == "tabular" else load_timeseries
                    with patch(f"analysis.{analysis}.import_module") as imported:
                        setattr(imported.return_value, name, sentinel)
                        self.assertIs(loader(model_type), sentinel)
                        imported.assert_called_once_with(module)
                    self.assertIn(model_type, SYSTEM_PROMPT)

    def test_every_consumed_config_key_is_allowed(self):
        consumed = set(_KNOWN_KWARGS)
        for folder in ("analysis", "output"):
            for path in (TOOLBOX / folder).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        receiver = node.func.value
                        is_config = (
                            isinstance(receiver, ast.Name) and receiver.id == "config"
                        ) or (
                            isinstance(receiver, ast.Attribute) and receiver.attr == "config"
                        )
                        if (is_config and node.func.attr in {"get", "setdefault"}) or node.func.attr == "get_path":
                            if node.args and isinstance(node.args[0], ast.Constant):
                                consumed.add(node.args[0].value)
                    if isinstance(node, ast.Subscript):
                        receiver = node.value
                        is_config = (
                            isinstance(receiver, ast.Name) and receiver.id == "config"
                        ) or (
                            isinstance(receiver, ast.Attribute) and receiver.attr == "config"
                        )
                        if is_config and isinstance(node.slice, ast.Constant):
                            consumed.add(node.slice.value)
        self.assertFalse(consumed - CONFIG_FIELDS, f"Unaccepted explainer keys: {consumed - CONFIG_FIELDS}")
        self.assertEqual(
            set(OPTION_VALUES),
            CONFIG_FIELDS - {"analysis", "model_type", "model_path"},
            "Add matrix values for any new supported fields.",
        )

    def assert_intake_preserves(self, config):
        payloads = (
            config,
            {"config": config},
            {"config_json": json.dumps(config)},
            {"config_json": "", **config},
        )
        for payload in payloads:
            result = _prepare_input(payload)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["config"], config)
        result = _parse_assistant_response(json.dumps({"status": "ready", "config": config}))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["config"], config)
        with patch("services.config_assistant.service.requests.post") as post:
            post.return_value.json.return_value = {
                "message": {"content": json.dumps({"status": "ready", "config": config})}
            }
            result = _ask_assistant([{"role": "user", "content": "Explain the uploaded model with these settings."}])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["config"], config)

    def test_every_model_and_option_through_all_intake_paths(self):
        for analysis, routes in MODEL_ROUTES.items():
            for model_type in routes:
                base = runnable_config(analysis, model_type)
                self.assert_intake_preserves(base)
                for key, value in OPTION_VALUES.items():
                    if model_type == "arima" and key == "save_excel":
                        value = False
                    with self.subTest(analysis=analysis, model=model_type, key=key):
                        self.assert_intake_preserves({**base, key: value})
                with self.subTest(analysis=analysis, model=model_type, key="combined"):
                    config = {**base, **OPTION_VALUES}
                    if model_type == "arima":
                        config["save_excel"] = False
                    self.assert_intake_preserves(config)

    def test_optional_value_variants_are_not_restricted(self):
        variants = {
            "target_index": (0, [0], [0, 1]),
            "output_labels": (["prediction"], {"0": "prediction"}),
            "feature_names": ([], ["Age"]),
            "dataset_scope": ("full", "subset"),
            "kernel_nsamples": ("auto", 10),
            "save_excel": (True, False),
            "generate_notebook": (True, False),
            "package": ("sklearn", "pytorch", "torch", "tensorflow", "tf", "keras"),
            "framework": ("pytorch", "torch", "tensorflow", "tf", "keras"),
        }
        for analysis, routes in MODEL_ROUTES.items():
            for model_type in routes:
                for key, values in variants.items():
                    for value in values:
                        if model_type == "arima" and key == "save_excel" and value:
                            continue
                        with self.subTest(model=model_type, key=key, value=value):
                            self.assert_intake_preserves({
                                **runnable_config(analysis, model_type), key: value,
                            })

    def test_all_neural_and_lstm_backends_match_validation(self):
        for model_type in ("feedforward", "mlp", "neural_net", "lstm"):
            analysis = "timeseries" if model_type == "lstm" else "tabular"
            backends = LSTM_EXPLAINERS if model_type == "lstm" else NEURAL_EXPLAINERS
            for backend in (*backends, "unsupported"):
                with self.subTest(model=model_type, backend=backend):
                    result = _normalize_config({
                        **runnable_config(analysis, model_type), "explainer_type": backend,
                    })
                    self.assertEqual(result["status"], "ready" if backend in backends else "unsupported")

    @patch("services.config_assistant.service.requests.post")
    def test_explicit_model_answers_never_depend_on_llm_rejection(self, post):
        for model_type in TABULAR_MODELS | TIMESERIES_MODELS:
            result = _ask_assistant([{"role": "user", "content": model_type.upper()}])
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["config"]["model_type"], model_type)
        for alias, model_type in MODEL_ALIASES.items():
            result = _ask_assistant([{"role": "user", "content": alias}])
            self.assertEqual(result["config"]["model_type"], model_type)
        post.assert_not_called()

    def test_wrong_families_and_unknown_models_are_not_advertised(self):
        for analysis in MODEL_ROUTES:
            for model_type in TABULAR_MODELS | TIMESERIES_MODELS | {"cnn", "transformer", "svm"}:
                with self.subTest(analysis=analysis, model=model_type):
                    result = _normalize_config({"analysis": analysis, "model_type": model_type})
                    self.assertEqual(result["status"], "ready" if model_type in MODEL_ROUTES[analysis] else "unsupported")

    def test_arima_does_not_promise_unimplemented_excel_export(self):
        result = _normalize_config({
            **runnable_config("timeseries", "arima"), "save_excel": True,
        })
        self.assertEqual(result["status"], "unsupported")
        self.assertIn("save_excel to false", result["message"])


if __name__ == "__main__":
    unittest.main()
