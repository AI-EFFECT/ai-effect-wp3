"""Unit tests for supported explainability configuration intake."""

import unittest
from unittest.mock import patch

from services.config_assistant.service import (
    _AssistantError,
    _ask_assistant,
    _normalize_config,
    _parse_assistant_response,
    _prepare_input,
    _validate_run_config,
)


class NormalizeConfigTests(unittest.TestCase):
    def test_lstm_identifies_timeseries_without_another_question(self):
        result = _normalize_config({"model_type": "lstm"})
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["config"]["analysis"], "timeseries")

    def test_accepts_time_series_analysis_spellings(self):
        for analysis in ("time-series", "time series", "time_series"):
            with self.subTest(analysis=analysis):
                result = _normalize_config({"analysis": analysis, "model_type": "lstm"})
                self.assertEqual(result["status"], "ready")

    @patch("services.config_assistant.service.requests.post")
    def test_explicit_lstm_answer_is_not_lost_by_model_extraction(self, post):
        post.return_value.json.return_value = {
            "message": {"content": '{"status":"clarification","config":{}}'}
        }
        result = _ask_assistant([
            {"role": "user", "content": "Explain my model"},
            {"role": "assistant", "content": "Which model type are you using?"},
            {"role": "user", "content": "lstm timeseries"},
        ])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["config"]["model_type"], "lstm")
        self.assertEqual(result["config"]["analysis"], "timeseries")

    @patch("services.config_assistant.service.requests.post")
    def test_unknown_model_gets_help_without_guessing(self, post):
        result = _ask_assistant([{"role": "user", "content": "I don't know"}])
        self.assertEqual(result["status"], "clarification")
        self.assertEqual(result["config"], {})
        self.assertIn("training code", result["message"])
        post.assert_not_called()

    @patch("services.config_assistant.service.requests.post")
    def test_short_model_answer_accepts_model_suffix_and_spacing(self, post):
        for phrase in (
            "LSTM time-series models",
            "LSTM timeseries",
            "LSTM time series model.",
            "  LSTM   time-series   models  ",
            "Random Forest model",
        ):
            with self.subTest(phrase=phrase):
                result = _ask_assistant([{"role": "user", "content": phrase}])
                self.assertEqual(result["status"], "ready")
        post.assert_not_called()

    @patch("services.config_assistant.service.requests.post")
    def test_natural_language_still_uses_local_llm(self, post):
        post.return_value.json.return_value = {
            "message": {"content": '{"status":"ready","config":{"model_type":"lstm"}}'}
        }
        result = _ask_assistant([
            {"role": "user", "content": "Please explain my trained LSTM predictions."}
        ])
        self.assertEqual(result["config"]["model_type"], "lstm")
        post.assert_called_once()
        self.assertIn("/api/chat", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["json"]["options"]["temperature"], 0)

    def test_supports_tabular_random_forest(self):
        result = _normalize_config(
            {"analysis": "Tabular", "model_type": "Random Forest"}
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["config"]["model_type"], "random_forest")

    def test_asks_for_model_type_when_missing(self):
        result = _normalize_config({"analysis": "tabular"})

        self.assertEqual(result["status"], "clarification")
        self.assertIn("Which model type", result["message"])

    def test_declines_unsupported_model_type(self):
        result = _normalize_config(
            {"analysis": "tabular", "model_type": "transformer"}
        )

        self.assertEqual(result["status"], "unsupported")

    def test_declines_unsupported_analysis_type(self):
        result = _normalize_config(
            {"analysis": "image", "model_type": "random_forest"}
        )

        self.assertEqual(result["status"], "unsupported")

    def test_requires_tabular_model_and_dataset_paths_to_run(self):
        with self.assertRaisesRegex(_AssistantError, "model_path, dataset_path"):
            _validate_run_config(
                {"analysis": "tabular", "model_type": "random_forest"}
            )

    def test_requires_lstm_tensor_files_to_run(self):
        with self.assertRaisesRegex(_AssistantError, "background_data_path"):
            _validate_run_config(
                {"analysis": "timeseries", "model_type": "lstm", "model_path": "model.pt"}
            )

    def test_rejects_model_generated_unknown_config_fields(self):
        response = (
            '{"status":"ready","message":"Ready",'
            '"config":{"analysis":"tabular","model_type":"random_forest",'
            '"prompt":"unrelated"}}'
        )

        result = _parse_assistant_response(response)

        self.assertEqual(result["status"], "unsupported")

    def test_direct_config_requires_paths(self):
        with self.assertRaisesRegex(_AssistantError, "model_path, dataset_path"):
            _prepare_input(
                {
                    "config_json": (
                        '{"analysis":"tabular","model_type":"random_forest"}'
                    )
                }
            )

    def test_direct_config_accepts_paths(self):
        result = _prepare_input(
            {
                "config_json": (
                    '{"analysis":"tabular","model_type":"random_forest",'
                    '"model_path":"model.pkl","dataset_path":"data.csv"}'
                )
            }
        )

        self.assertEqual(result["status"], "ready")

    def test_manual_tabular_settings_are_preserved(self):
        config = {
            "analysis": "tabular",
            "package": "sklearn",
            "model_type": "random_forest",
            "model_path": "model.pkl",
            "dataset_path": "data.csv",
            "dataset_scope": "subset",
            "subset_end": 25,
            "save_excel": True,
            "generate_notebook": True,
            "feature_names": ["Age", "Income per Year", "Years of Employment"],
        }
        for payload in ({"config": config}, config):
            with self.subTest(payload=payload):
                result = _prepare_input(payload)
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["config"], config)

    def test_unknown_settings_are_named_for_revision(self):
        config = {
            "analysis": "tabular",
            "model_type": "random_forest",
            "model_path": "model.pkl",
            "dataset_path": "data.csv",
            "unsupported_option": True,
        }
        for payload in ({"config": config}, config):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(_AssistantError, "unsupported_option"):
                    _prepare_input(payload)

    @patch("services.config_assistant.service._ask_assistant")
    def test_empty_proto_config_uses_supplied_prompt(self, ask_assistant):
        expected = {
            "status": "ready",
            "message": "Supported setup.",
            "config": {
                "analysis": "tabular",
                "model_type": "random_forest",
                "model_path": "model.pkl",
                "dataset_path": "data.csv",
            },
        }
        ask_assistant.return_value = expected

        result = _prepare_input(
            {"config_json": "", "prompt": "Explain my random forest model."}
        )

        self.assertEqual(result, expected)
        ask_assistant.assert_called_once_with(
            [{"role": "user", "content": "Explain my random forest model."}]
        )


if __name__ == "__main__":
    unittest.main()
