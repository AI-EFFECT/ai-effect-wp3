"""Tests for the toolbox's model-inspection and configuration APIs."""

import unittest

from ai_explainability import (
    FieldSpec,
    ModelProfile,
    __version__,
    inspect_model,
    resolve_config,
)


class ModelIntrospectionTests(unittest.TestCase):
    def test_native_multioutput_forest_uses_output_count_not_estimator_count(self):
        class MultiOutputMixin:
            pass

        class RandomForestRegressor(MultiOutputMixin):
            n_estimators = 12
            n_outputs_ = 2
            n_features_in_ = 2
            feature_names_in_ = ("x0", "x1")
            estimators_ = [object() for _ in range(n_estimators)]
            feature_importances_ = (0.5, 0.5)

        profile = inspect_model(RandomForestRegressor())
        self.assertIsInstance(profile, ModelProfile)
        self.assertEqual(profile.notes["n_outputs"], 2)
        self.assertEqual(profile.inferred["model_type"], "random_forest")

        target_index = next(spec for spec in profile.missing if spec.key == "target_index")
        self.assertIsInstance(target_index, FieldSpec)
        self.assertEqual(target_index.options, [0, 1])

        config = resolve_config(profile, {"target_index": [1]})
        self.assertEqual(config["target_index"], [1])
        self.assertEqual(config["model_type"], "random_forest")

    def test_multioutput_wrapper_uses_one_output_per_wrapped_estimator(self):
        class MultiOutputRegressor:
            estimators_ = (object(), object())

        profile = inspect_model(MultiOutputRegressor())
        self.assertEqual(profile.notes["n_outputs"], 2)

    def test_data_header_is_used_only_when_width_matches_model(self):
        class RandomForestRegressor:
            n_features_in_ = 2
            n_outputs_ = 1

        class Data:
            columns = ["x0", "x1"]

        profile = inspect_model(RandomForestRegressor(), Data())
        self.assertEqual(profile.inferred["feature_names"], ["x0", "x1"])
        self.assertEqual(profile.notes["feature_names_source"], "data header")

        class DataWithTarget:
            columns = ["x0", "x1", "target"]

        profile = inspect_model(RandomForestRegressor(), DataWithTarget())
        self.assertNotIn("feature_names", profile.inferred)
        feature_names = next(spec for spec in profile.missing if spec.key == "feature_names")
        self.assertEqual(feature_names.options, ["x0", "x1", "target"])
        self.assertTrue(feature_names.required)
        self.assertTrue(profile.warnings)

    def test_package_exports_match_upstream_version(self):
        self.assertEqual(__version__, "0.4.0")


if __name__ == "__main__":
    unittest.main()
