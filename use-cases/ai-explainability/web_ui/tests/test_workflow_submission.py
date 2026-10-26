"""Workflow gateway errors must remain actionable JSON responses."""

import unittest
from unittest.mock import patch

import requests
from fastapi import HTTPException

from web_ui import app as gateway


class WorkflowSubmissionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(gateway, "_read_export", return_value=({"nodes": []}, {}))
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("web_ui.app.requests.post")
    def test_unreachable_orchestrator_returns_json(self, post):
        for error in (requests.ConnectionError("offline"), requests.Timeout("timed out")):
            with self.subTest(error=type(error).__name__):
                post.side_effect = error
                with self.assertRaises(HTTPException) as failure:
                    gateway.submit_workflow(gateway.SubmitRequest(inputs={"config_json": "{}"}))
                self.assertEqual(failure.exception.status_code, 503)
                self.assertIn("Could not reach the orchestrator", failure.exception.detail)

    @patch("web_ui.app.requests.get")
    def test_unreachable_status_returns_json(self, get):
        get.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(HTTPException) as failure:
            gateway.workflow_status("test-workflow")
        self.assertEqual(failure.exception.status_code, 503)
        self.assertIn("may still be running", failure.exception.detail)

    @patch("web_ui.app._poll_workflow", return_value={"tasks": []})
    @patch("web_ui.app.requests.post")
    def test_successful_submission_preserves_workflow(self, post, poll):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"workflow_id": "test-workflow"}
        response = gateway.submit_workflow(gateway.SubmitRequest(inputs={"config_json": "{}"}))
        self.assertEqual(response["workflow_id"], "test-workflow")
        poll.assert_called_once_with("test-workflow")
        self.assertEqual(post.call_args.kwargs["json"]["inputs"][0]["protocol"], "inline")

    @patch("web_ui.app.requests.post")
    def test_orchestrator_rejection_is_preserved(self, post):
        post.return_value.status_code = 422
        post.return_value.text = "Invalid workflow"
        with self.assertRaises(HTTPException) as failure:
            gateway.submit_workflow(gateway.SubmitRequest(inputs={}))
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(failure.exception.detail, "Invalid workflow")


if __name__ == "__main__":
    unittest.main()
