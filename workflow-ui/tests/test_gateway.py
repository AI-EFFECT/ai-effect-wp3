"""Shared workflow UI gateway: manifest loading, uploads, decisions, orchestrator errors."""

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as gateway  # noqa: E402

MANIFEST = {
    "title": "Topology Workflow",
    "brand": {"mark": "D", "name": "Danish Node"},
    "plugins": ["topology.js", "../escape.js"],
    "assistant": {"enabled": True},
    "inputs": [
        {"name": "prompt", "type": "textarea"},
        {"name": "model", "type": "file", "config_field": "model_path", "upload_category": "models"},
    ],
    "steps": [
        {"method": "Render", "gate": {"actions": [{"name": "accepted"}, {"name": "rejected"}]}},
    ],
    "actions": [{"name": "archive"}],
}


class UseCaseTestCase(unittest.TestCase):
    """Points the gateway at a temporary use case and data directory."""

    manifest: dict | str | None = MANIFEST

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.use_case = Path(temp.name) / "use-case"
        self.data = Path(temp.name) / "data"
        (self.use_case / "ui").mkdir(parents=True)
        self.data.mkdir()
        if isinstance(self.manifest, dict):
            (self.use_case / "ui.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        elif isinstance(self.manifest, str):
            (self.use_case / "ui.json").write_text(self.manifest, encoding="utf-8")
        (self.use_case / "ui" / "topology.js").write_text("// plugin", encoding="utf-8")
        (Path(temp.name) / "escape.js").write_text("// outside", encoding="utf-8")
        for name, value in {
            "USE_CASE_DIR": self.use_case,
            "UI_MANIFEST_PATH": self.use_case / "ui.json",
            "PLUGIN_DIR": self.use_case / "ui",
            "DATA_DIR": self.data,
            "UPLOAD_DIR": self.data / "uploads",
            "CONFIG_ASSISTANT_URL": "http://assistant/configure",
            "DATA_INSPECTION_URL": "http://runner/inspect",
        }.items():
            patcher = patch.object(gateway, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(gateway.app)


class ManifestTests(UseCaseTestCase):
    def test_page_uses_manifest_branding_and_only_safe_plugins(self):
        page = self.client.get("/").text
        self.assertIn("<title>Topology Workflow</title>", page)
        self.assertIn('<span class="brand-mark">D</span>', page)
        self.assertIn("<span>Danish Node</span>", page)
        self.assertIn('<script src="/plugins/topology.js" defer></script>', page)
        self.assertNotIn("escape.js", page)
        self.assertLess(page.index("/plugins/topology.js"), page.index("/static/app.js"))

    def test_declared_plugins_are_served_and_others_are_not(self):
        self.assertEqual(self.client.get("/plugins/topology.js").text, "// plugin")
        self.assertEqual(self.client.get("/plugins/other.js").status_code, 404)
        self.assertEqual(self.client.get("/plugins/..%2Fescape.js").status_code, 404)

    def test_features_reflect_configured_services(self):
        self.assertEqual(
            self.client.get("/ui/config").json()["features"],
            {"assistant": True, "upload_inspection": True},
        )
        with patch.object(gateway, "CONFIG_ASSISTANT_URL", None), patch.object(gateway, "DATA_INSPECTION_URL", None):
            self.assertEqual(
                self.client.get("/ui/config").json()["features"],
                {"assistant": False, "upload_inspection": False},
            )
            response = self.client.post("/assistant/configure", json={"messages": []})
            self.assertEqual(response.status_code, 503)


class MissingManifestTests(UseCaseTestCase):
    manifest = None

    def test_missing_manifest_falls_back_with_a_visible_warning(self):
        config = self.client.get("/ui/config").json()
        self.assertIn("No UI manifest found", config["manifest_warning"])
        self.assertEqual(config["inputs"][0]["name"], "prompt")


class InvalidManifestTests(UseCaseTestCase):
    manifest = '{"title": "Broken", // comment\n'

    def test_invalid_manifest_falls_back_with_a_visible_warning(self):
        response = self.client.get("/ui/config")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ui.json is invalid", response.json()["manifest_warning"])
        self.assertEqual(self.client.get("/").status_code, 200)


class DecisionTests(UseCaseTestCase):
    def test_step_decisions_are_validated_and_persisted(self):
        response = self.client.post(
            "/decision", json={"workflow_id": "wf", "step": "Render", "action": "accepted", "value": "B"}
        )
        self.assertEqual(response.status_code, 200)
        stored = json.loads((self.data / "wf" / "ui-decisions.json").read_text())
        self.assertEqual(stored["Render"]["action"], "accepted")
        self.assertEqual(stored["Render"]["value"], "B")
        self.assertEqual(gateway._read_decisions("wf")["Render"]["action"], "accepted")

    def test_workflow_level_decisions_use_the_top_level_actions(self):
        ok = self.client.post("/decision", json={"workflow_id": "wf", "action": "archive"})
        self.assertEqual(ok.status_code, 200)
        self.assertIn("workflow", gateway._read_decisions("wf"))

    def test_unknown_actions_steps_and_paths_are_rejected(self):
        for body in (
            {"workflow_id": "wf", "step": "Render", "action": "archive"},
            {"workflow_id": "wf", "step": "Missing", "action": "accepted"},
            {"workflow_id": "wf", "action": "accepted"},
            {"workflow_id": "../escape", "step": "Render", "action": "accepted"},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/decision", json=body).status_code, 400)

    def test_status_includes_recorded_decisions(self):
        self.client.post("/decision", json={"workflow_id": "wf", "step": "Render", "action": "rejected"})
        status = gateway._workflow_status_from_tasks("wf", [{"task_id": "t", "status": "completed"}])
        self.assertEqual(status.decisions["Render"]["action"], "rejected")


class UploadTests(UseCaseTestCase):
    def test_upload_categories_come_from_the_manifest(self):
        ok = self.client.post("/uploads", files={"file": ("m.pkl", b"x")}, data={"category": "models"})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(Path(ok.json()["path"]).is_file())
        rejected = self.client.post("/uploads", files={"file": ("d.csv", b"x")}, data={"category": "data"})
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("models", rejected.json()["detail"])


class UploadInspectionTests(UseCaseTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.data / "uploads" / "data.csv"
        self.path.parent.mkdir(parents=True)
        self.path.write_text("a,b\n1,2\n", encoding="utf-8")

    @patch("app.requests.post")
    def test_metadata_is_forwarded(self, post):
        expected = {"kind": "table", "feature_names": ["a", "b"], "message": "Review names."}
        post.return_value.json.return_value = expected
        self.assertEqual(gateway.inspect_upload(gateway.InspectUploadRequest(path=str(self.path))), expected)
        self.assertEqual(post.call_args.kwargs["json"]["path"], str(self.path))

    @patch("app.requests.post")
    def test_service_failure_does_not_claim_inspection_succeeded(self, post):
        post.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(HTTPException) as failure:
            gateway.inspect_upload(gateway.InspectUploadRequest(path=str(self.path)))
        self.assertEqual(failure.exception.status_code, 503)
        self.assertIn("file is uploaded", failure.exception.detail)
        self.assertTrue(self.path.exists())

    @patch("app.requests.post")
    def test_invalid_and_outside_paths_never_reach_the_service(self, post):
        for path in (self.data / "uploads" / "missing.csv", Path("/etc/passwd")):
            with self.subTest(path=path):
                with self.assertRaises(HTTPException):
                    gateway.inspect_upload(gateway.InspectUploadRequest(path=str(path)))
        post.assert_not_called()

    @patch("app.requests.post")
    def test_parser_errors_are_shown(self, post):
        post.return_value.ok = False
        post.return_value.json.return_value = {"detail": "Invalid tensor dimensions."}
        with self.assertRaises(HTTPException) as failure:
            gateway.inspect_upload(gateway.InspectUploadRequest(path=str(self.path)))
        self.assertEqual(failure.exception.detail, "Invalid tensor dimensions.")

    @patch("app.requests.post")
    def test_inspection_is_unavailable_without_a_service(self, post):
        with patch.object(gateway, "DATA_INSPECTION_URL", None):
            with self.assertRaises(HTTPException) as failure:
                gateway.inspect_upload(gateway.InspectUploadRequest(path=str(self.path)))
        self.assertEqual(failure.exception.status_code, 503)
        post.assert_not_called()


class WorkflowSubmissionTests(UseCaseTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(gateway, "_read_export", return_value=({"nodes": []}, {}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_inline_outputs_are_decoded_per_task(self):
        payload = {"status": "ready", "message": "Ready.", "config": {"model_type": "lstm"}}
        reference = {
            "protocol": "inline", "format": "json",
            "uri": base64.b64encode(json.dumps(payload).encode()).decode(),
        }
        outputs = gateway._load_outputs([{"task_id": "assistant-task", "output_refs": [reference]}])
        self.assertEqual(outputs[0]["payload"], payload)
        self.assertEqual(outputs[0]["task_id"], "assistant-task")

    @patch("app.requests.post")
    def test_unreachable_orchestrator_returns_json(self, post):
        for error in (requests.ConnectionError("offline"), requests.Timeout("timed out")):
            with self.subTest(error=type(error).__name__):
                post.side_effect = error
                with self.assertRaises(HTTPException) as failure:
                    gateway.submit_workflow(gateway.SubmitRequest(inputs={"config_json": "{}"}))
                self.assertEqual(failure.exception.status_code, 503)
                self.assertIn("Could not reach the orchestrator", failure.exception.detail)

    @patch("app.requests.get")
    def test_unreachable_status_returns_json(self, get):
        get.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(HTTPException) as failure:
            gateway.workflow_status("test-workflow")
        self.assertEqual(failure.exception.status_code, 503)
        self.assertIn("may still be running", failure.exception.detail)

    @patch("app._poll_workflow", return_value={"tasks": []})
    @patch("app.requests.post")
    def test_successful_submission_preserves_workflow(self, post, poll):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"workflow_id": "test-workflow"}
        response = gateway.submit_workflow(gateway.SubmitRequest(inputs={"config_json": "{}"}))
        self.assertEqual(response["workflow_id"], "test-workflow")
        poll.assert_called_once_with("test-workflow")
        self.assertEqual(post.call_args.kwargs["json"]["inputs"][0]["protocol"], "inline")

    @patch("app._poll_workflow", return_value={"tasks": []})
    @patch("app.requests.post")
    def test_orchestrator_api_key_is_sent_only_when_configured(self, post, poll):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"workflow_id": "wf"}
        gateway.submit_workflow(gateway.SubmitRequest(inputs={}))
        self.assertEqual(post.call_args.kwargs["headers"], {})
        with patch.object(gateway, "ORCHESTRATOR_API_KEY", "secret"):
            gateway.submit_workflow(gateway.SubmitRequest(inputs={}))
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer secret"})

    @patch("app.requests.post")
    def test_orchestrator_rejection_is_preserved(self, post):
        post.return_value.status_code = 422
        post.return_value.text = "Invalid workflow"
        with self.assertRaises(HTTPException) as failure:
            gateway.submit_workflow(gateway.SubmitRequest(inputs={}))
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(failure.exception.detail, "Invalid workflow")


class FileAccessTests(UseCaseTestCase):
    def test_workflow_files_stay_inside_the_workflow_directory(self):
        (self.data / "wf" / "task").mkdir(parents=True)
        (self.data / "wf" / "task" / "report.md").write_text("# Report", encoding="utf-8")
        (self.data / "secret.txt").write_text("secret", encoding="utf-8")
        self.assertEqual(self.client.get("/workflows/wf/files/task/report.md").text, "# Report")
        self.assertEqual(self.client.get("/workflows/wf/files/..%2Fsecret.txt").status_code, 404)
        self.assertEqual(self.client.get("/workflows/wf/tasks/task/download").status_code, 200)


if __name__ == "__main__":
    unittest.main()
