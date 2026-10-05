"""Safe file metadata inspection without executing uploaded model objects."""

import tempfile
import unittest
from pathlib import Path
from pickle import UnpicklingError
from unittest.mock import patch

import torch
from fastapi import HTTPException

import service


class DataInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        patcher = patch.object(service, "UPLOAD_DIR", self.uploads)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(service, "ASSETS_DIR", self.root / "assets")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_csv_headers_preserve_order_and_quoted_names(self):
        path = self.uploads / "data.csv"
        path.write_text('"Age","Income, per Year","Employment"\n20,2,1\n', encoding="utf-8")
        result = service._inspect_data(str(path))
        self.assertEqual(result["kind"], "table")
        self.assertEqual(result["feature_names"], ["Age", "Income, per Year", "Employment"])

    def test_tsv_headers(self):
        path = self.uploads / "data.tsv"
        path.write_text("Age\tIncome\n20\t2\n", encoding="utf-8")
        self.assertEqual(service._inspect_data(str(path))["feature_names"], ["Age", "Income"])

    def test_tensor_shape_and_positional_names(self):
        path = self.uploads / "data.pt"
        torch.save(torch.ones(5, 6, 12), path)
        result = service._inspect_data(str(path))
        self.assertEqual(result["shape"], [5, 6, 12])
        self.assertEqual(result["feature_names"], [f"f{i}" for i in range(12)])
        self.assertIn("positional", result["message"])

    def test_invalid_tensor_dimensions_and_model_weights_are_rejected(self):
        path = self.uploads / "invalid.pt"
        for data in (torch.ones(2, 3), torch.ones(0, 3, 2), {"weights": torch.ones(2, 3)}):
            with self.subTest(data=type(data).__name__):
                torch.save(data, path)
                with self.assertRaises(ValueError):
                    service._inspect_data(str(path))

    def test_model_objects_are_never_unpickled_by_inspection(self):
        path = self.uploads / "model.pt"
        torch.save(torch.nn.Linear(2, 1), path)
        with self.assertRaises(UnpicklingError):
            service._inspect_data(str(path))
        with self.assertRaises(HTTPException) as failure:
            service.inspect_data(service.InspectDataRequest(path=str(path)))
        self.assertEqual(failure.exception.status_code, 422)

    def test_pickle_and_other_formats_are_not_loaded(self):
        path = self.uploads / "model.pkl"
        path.write_bytes(b"not a model")
        result = service._inspect_data(str(path))
        self.assertEqual(result["kind"], "unavailable")

    def test_outside_uploads_and_missing_files_are_rejected(self):
        path = self.root / "outside.csv"
        path.write_text("a,b\n", encoding="utf-8")
        for candidate in (path, self.uploads / "missing.csv"):
            with self.subTest(path=candidate):
                with self.assertRaises(ValueError):
                    service._inspect_data(str(candidate))


if __name__ == "__main__":
    unittest.main()
