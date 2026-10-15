"""Gateway validation and explicit file-inspection feedback."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi import HTTPException

from web_ui import app as gateway


class UploadInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "data.csv"
        self.path.write_text("a,b\n1,2\n", encoding="utf-8")
        patcher = patch.object(gateway, "UPLOAD_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("web_ui.app.requests.post")
    def test_metadata_is_forwarded(self, post):
        expected = {"kind": "table", "feature_names": ["a", "b"], "message": "Review names."}
        post.return_value.json.return_value = expected
        self.assertEqual(gateway.inspect_upload(gateway.InspectUploadRequest(path=str(self.path))), expected)
        self.assertEqual(post.call_args.kwargs["json"]["path"], str(self.path))

    @patch("web_ui.app.requests.post")
    def test_service_failure_does_not_claim_feature_detection_succeeded(self, post):
        post.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(HTTPException) as failure:
            gateway.inspect_upload(gateway.InspectUploadRequest(path=str(self.path)))
        self.assertEqual(failure.exception.status_code, 503)
        self.assertIn("file is uploaded", failure.exception.detail)
        self.assertTrue(self.path.exists())

    @patch("web_ui.app.requests.post")
    def test_invalid_and_outside_paths_never_reach_runner(self, post):
        for path in (self.root / "missing.csv", Path("/etc/passwd")):
            with self.subTest(path=path):
                with self.assertRaises(HTTPException):
                    gateway.inspect_upload(gateway.InspectUploadRequest(path=str(path)))
        post.assert_not_called()

    @patch("web_ui.app.requests.post")
    def test_parser_errors_are_shown(self, post):
        post.return_value.ok = False
        post.return_value.json.return_value = {"detail": "Invalid tensor dimensions."}
        with self.assertRaises(HTTPException) as failure:
            gateway.inspect_upload(gateway.InspectUploadRequest(path=str(self.path)))
        self.assertEqual(failure.exception.detail, "Invalid tensor dimensions.")


if __name__ == "__main__":
    unittest.main()
