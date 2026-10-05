"""Regression tests for LSTM audit export after a successful explanation."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.timeseries.lstm_pytorch import LSTMExplainer


class LSTMExcelTests(unittest.TestCase):
    def make_explainer(self, output_dir, **config):
        explainer = LSTMExplainer({"output_dir": output_dir, **config})
        explainer.raw_data_values = np.arange(24).reshape(2, 3, 4)
        explainer.all_shap_values = {0: np.ones((2, 3, 4))}
        explainer.all_predictions = {0: np.array([0.25, 0.75])}
        return explainer

    def test_export_without_look_back_or_feature_names(self):
        with tempfile.TemporaryDirectory() as directory:
            explainer = self.make_explainer(directory)
            explainer.save_results_to_excel()
            files = list(Path(directory).glob("*.xlsx"))
            self.assertEqual(len(files), 1)
            report = pd.read_excel(files[0])
            self.assertEqual(report.shape, (2, 25))
            self.assertEqual(report["Val_f0_t-2"].tolist(), [0, 12])
            self.assertEqual(report["SHAP_f3_t-0"].tolist(), [1, 1])
            self.assertEqual(report["Model_Prediction"].tolist(), [0.25, 0.75])

    def test_result_uses_positional_names_when_names_are_omitted(self):
        result = self.make_explainer("unused").to_result()
        self.assertEqual(result.feature_names, ["f0", "f1", "f2", "f3"])
        self.assertIn("SHAP_f0_t-2_target0", result.to_dataframe().columns)

    def test_result_preserves_explicit_feature_names(self):
        names = ["a", "b", "c", "d"]
        result = self.make_explainer("unused", feature_names=names).to_result()
        self.assertEqual(result.feature_names, names)

    def test_export_preserves_supplied_feature_names(self):
        with tempfile.TemporaryDirectory() as directory:
            explainer = self.make_explainer(
                directory, look_back=3, feature_names=["a", "b", "c", "d"]
            )
            explainer.save_results_to_excel()
            report = pd.read_excel(next(Path(directory).glob("*.xlsx")))
            self.assertIn("Val_a_t-2", report)
            self.assertIn("SHAP_d_t-0", report)

    def test_rejects_inconsistent_time_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            explainer = self.make_explainer(directory, look_back=6)
            with self.assertRaisesRegex(ValueError, "does not match"):
                explainer.save_results_to_excel()

    def test_rejects_inconsistent_feature_names(self):
        with tempfile.TemporaryDirectory() as directory:
            explainer = self.make_explainer(directory, feature_names=["a"])
            with self.assertRaisesRegex(ValueError, "4 names"):
                explainer.save_results_to_excel()


if __name__ == "__main__":
    unittest.main()
