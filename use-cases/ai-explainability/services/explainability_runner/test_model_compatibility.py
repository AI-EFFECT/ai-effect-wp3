"""Real file-based analyses after assistant validation, using tiny local models."""

import json
import pickle
import tempfile
import unittest
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.multioutput import MultiOutputClassifier, MultiOutputRegressor

from ai_explainability import explain
from ai_explainability.capabilities import EXCEL_MODELS, MODEL_ROUTES
from analysis.tabular import _load_explainer_class as load_tabular
from analysis.timeseries import _load_explainer_class as load_timeseries
from analysis.timeseries.lstm_pytorch import LSTMForecaster
from service import UPLOAD_DIR, _prepare_config
from services.config_assistant.service import _prepare_input


class ModelCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        torch.manual_seed(7)
        cls.data = pd.DataFrame(
            np.random.default_rng(7).normal(size=(12, 2)),
            columns=["x0", "x1"],
        )
        cls.target = cls.data.x0.to_numpy() + 2 * cls.data.x1.to_numpy()

    def setUp(self):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="model-parity-", dir=UPLOAD_DIR)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data_path = self.root / "data.csv"
        self.data.to_csv(self.data_path, index=False)

    def run_config(self, config, output):
        prepared = _prepare_input({"config_json": json.dumps(config)})
        self.assertEqual(prepared["status"], "ready")
        self.assertEqual(prepared["config"], config)
        actual = _prepare_config(prepared["config"], output)
        return explain(**actual)

    def assert_reports(self, folder, excel, notebook):
        self.assertEqual(bool(list(folder.glob("*.xlsx"))), excel)
        self.assertEqual(bool(list(folder.glob("*.ipynb"))), notebook)

    def run_tabular(self, model, model_type, **options):
        path = self.root / "model.pkl"
        with path.open("wb") as handle:
            pickle.dump(model, handle)
        self.run_tabular_path(path, model_type, **options)

    def run_tabular_path(self, path, model_type, **options):
        run_root = Path(tempfile.mkdtemp(prefix=f"{model_type}-", dir=self.root))
        for excel, notebook in product((False, True), repeat=2):
            with self.subTest(model=model_type, options=options, excel=excel, notebook=notebook):
                output = run_root / f"output-{excel}-{notebook}"
                config = {
                    "analysis": "tabular",
                    "model_type": model_type,
                    "model_path": str(path),
                    "dataset_path": str(self.data_path),
                    "feature_names": ["x0", "x1"],
                    "dataset_scope": "subset",
                    "subset_end": 3,
                    "save_excel": excel,
                    "generate_notebook": notebook,
                    **options,
                }
                result = self.run_config(config, output)
                self.assertEqual(result.raw_data_values.shape, (3, 2))
                self.assertTrue(result.shap_values)
                for values in result.shap_values.values():
                    self.assertEqual(values.shape, (3, 2))
                    self.assertTrue(np.isfinite(values).all())
                self.assertEqual(len(result.to_dataframe()), 3)
                self.assert_reports(output, excel, notebook)

    def test_random_forest_regression_classification_and_multioutput(self):
        multi_target = np.column_stack([self.target, self.target * 2])
        labels = (multi_target > 0).astype(int)
        cases = (
            (RandomForestRegressor(n_estimators=3, random_state=7), self.target, [0]),
            (RandomForestClassifier(n_estimators=3, random_state=7), labels[:, 0], [0, 1]),
            (MultiOutputRegressor(RandomForestRegressor(n_estimators=3, random_state=7)), multi_target, [0, 1]),
            (MultiOutputClassifier(RandomForestClassifier(n_estimators=3, random_state=7)), labels, [0, 1]),
        )
        for model, target, targets in cases:
            with self.subTest(model=type(model).__name__):
                self.run_tabular(model.fit(self.data, target), "random_forest", package="sklearn", target_index=targets)

    def test_xgboost_regression_and_classification(self):
        from xgboost import XGBClassifier, XGBRegressor

        for model, target in (
            (XGBRegressor(n_estimators=3, max_depth=2, n_jobs=1), self.target),
            (XGBClassifier(n_estimators=3, max_depth=2, n_jobs=1), (self.target > 0).astype(int)),
        ):
            with self.subTest(model=type(model).__name__):
                self.run_tabular(model.fit(self.data, target), "xgboost", package="xgboost")

    def test_pytorch_all_neural_aliases_and_backends(self):
        for model_type, backend in product(("feedforward", "mlp", "neural_net"), ("kernel", "deep", "gradient")):
            with self.subTest(model=model_type, backend=backend):
                model = torch.nn.Sequential(
                    torch.nn.Linear(2, 3), torch.nn.ReLU(), torch.nn.Linear(3, 1),
                ).eval()
                self.run_tabular(
                    model, model_type, framework="torch", explainer_type=backend,
                    background_size=2, kernel_nsamples=8,
                )

    def test_tensorflow_all_neural_aliases_and_backends(self):
        from tensorflow import keras

        for model_type, backend in product(("feedforward", "mlp", "neural_net"), ("kernel", "deep", "gradient")):
            with self.subTest(model=model_type, backend=backend):
                model = keras.Sequential([
                    keras.Input(shape=(2,)),
                    keras.layers.Dense(3, activation="relu"),
                    keras.layers.Dense(1),
                ])
                path = self.root / "model.keras"
                model.save(path)
                self.run_tabular_path(
                    path, model_type, package="keras", explainer_type=backend,
                    background_size=2, kernel_nsamples=8,
                )

    def test_lstm_both_backends_and_report_options(self):
        model = LSTMForecaster(2, 3)
        path = self.root / "model.pth"
        torch.save(model.state_dict(), path)
        background = self.root / "background.pt"
        test = self.root / "test.pt"
        torch.save(torch.randn(4, 2, 2), background)
        torch.save(torch.randn(4, 2, 2), test)
        for backend, excel, notebook in product(("gradient", "deep"), (False, True), (False, True)):
            with self.subTest(backend=backend, excel=excel, notebook=notebook):
                output = self.root / f"{backend}-{excel}-{notebook}"
                result = self.run_config({
                    "analysis": "timeseries", "model_type": "lstm",
                    "model_path": str(path),
                    "background_data_path": str(background),
                    "test_data_path": str(test),
                    "input_dim": 2, "hidden_size": 3, "look_back": 2,
                    "look_ahead": 1, "explain_subset": 3,
                    "feature_names": ["x0", "x1"], "explainer_type": backend,
                    "save_excel": excel, "generate_notebook": notebook,
                }, output)
                self.assertEqual(result.raw_data_values.shape, (3, 2, 2))
                self.assertEqual(result.shap_values[0].shape, (3, 2, 2))
                self.assertTrue(np.isfinite(result.shap_values[0]).all())
                self.assert_reports(output, excel, notebook)

    def test_arima_coefficient_analysis_and_plots_without_excel(self):
        from statsmodels.tsa.arima.model import ARIMA

        rng = np.random.default_rng(7)
        exog = pd.DataFrame({"x0": rng.normal(size=40)})
        fitted = ARIMA(2 * exog.x0 + rng.normal(size=40), exog=exog, order=(1, 0, 0)).fit()
        path = self.root / "arima.pkl"
        with path.open("wb") as handle:
            pickle.dump(fitted, handle)
        for plots in (False, True):
            with self.subTest(plots=plots):
                output = self.root / f"arima-{plots}"
                config = {
                    "analysis": "timeseries", "model_type": "arima",
                    "model_path": str(path), "feature_names": ["x0"],
                    "generate_notebook": plots,
                }
                prepared = _prepare_config(config, output)
                self.assertFalse(prepared["save_excel"])
                result = self.run_config(config, output)
                self.assertFalse(result.shap_values)
                self.assertFalse(result.extras["stats_df"].empty)
                self.assertEqual(list(result.extras["exog_importance"].index), ["x0"])
                self.assertEqual(bool(list(output.glob("arima_*.png"))), plots)

    def test_contract_excel_support_matches_actual_methods(self):
        for analysis, routes in MODEL_ROUTES.items():
            for model_type in routes:
                loader = load_tabular if analysis == "tabular" else load_timeseries
                self.assertEqual(
                    hasattr(loader(model_type), "save_results_to_excel"),
                    model_type in EXCEL_MODELS,
                )


if __name__ == "__main__":
    unittest.main()
