"""Metadata-driven user-facing gateway for AI-EFFECT workflows."""

from __future__ import annotations

import base64
import html
import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://host.docker.internal:18000")
USE_CASE_DIR = Path(os.environ.get("USE_CASE_DIR", "/workspace/use-cases/denmark-node"))
EXPORT_PATH = Path(os.environ.get("EXPORT_PATH", str(USE_CASE_DIR / "export")))
UI_MANIFEST_PATH = Path(os.environ.get("UI_MANIFEST_PATH", str(USE_CASE_DIR / "ui.json")))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
TEMPLATE_PATH = BASE_DIR / "templates" / "index.html"

app = FastAPI(title="AI-EFFECT Workflow UI", version="2.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

WORKFLOW_DECISIONS: dict[str, dict[str, Any]] = {}


class SubmitRequest(BaseModel):
    inputs: dict[str, Any]


class DecisionRequest(BaseModel):
    workflow_id: str
    action: str
    value: str | None = None


class WorkflowStatusResponse(BaseModel):
    workflow_id: str
    status: str
    outputs: list[dict[str, Any]] | None = None
    tasks: list[dict[str, Any]] | None = None
    error: str | None = None


def _read_json(path: Path, missing_message: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=missing_message) from exc


def _read_export() -> tuple[dict[str, Any], dict[str, Any]]:
    missing_message = "Workflow export files are not ready yet. Run ./generate-export.sh first."
    return (
        _read_json(EXPORT_PATH / "blueprint.json", missing_message),
        _read_json(EXPORT_PATH / "dockerinfo.json", missing_message),
    )


def _read_connections() -> dict[str, Any]:
    path = USE_CASE_DIR / "connections.json"
    if not path.exists():
        return {}
    return _read_json(path, "connections.json is not available for this workflow.")


def _pipeline_metadata() -> dict[str, Any]:
    connections = _read_connections()
    pipeline = connections.get("pipeline", {}) if isinstance(connections, dict) else {}
    services = pipeline.get("service_mapping", {})
    return {
        "name": pipeline.get("name") or USE_CASE_DIR.name.replace("-", " ").title(),
        "services": list(services.keys()),
        "connections": pipeline.get("connections", []),
    }


def _default_manifest() -> dict[str, Any]:
    pipeline = _pipeline_metadata()
    return {
        "id": USE_CASE_DIR.name,
        "title": pipeline["name"],
        "description": "Submit inputs to this workflow and inspect the generated outputs.",
        "submit_label": "Start workflow",
        "inputs": [
            {
                "name": "prompt",
                "label": "Workflow request",
                "type": "textarea",
                "required": True,
                "placeholder": "Describe what this workflow should run.",
            }
        ],
        "output": {
            "mode": "auto",
            "title": "Workflow output",
            "summary_fields": ["summary", "reason", "status", "message"],
            "image_collection_field": "drawings",
            "image_signature_field": "signature",
        },
        "actions": [],
        "pipeline": pipeline,
    }


def _load_manifest() -> dict[str, Any]:
    manifest = _default_manifest()
    if UI_MANIFEST_PATH.exists():
        configured = _read_json(UI_MANIFEST_PATH, "Workflow UI manifest could not be loaded.")
        manifest.update(configured)
        manifest["pipeline"] = {**_pipeline_metadata(), **configured.get("pipeline", {})}
    return manifest


def _poll_workflow(workflow_id: str, timeout_seconds: int = 2) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_response: dict[str, Any] = {"tasks": []}
    while time.time() < deadline:
        try:
            response = requests.get(f"{ORCHESTRATOR_URL}/workflows/{workflow_id}/tasks", timeout=10)
            if response.status_code == 200:
                last_response = response.json()
                statuses = [task.get("status") for task in last_response.get("tasks", [])]
                if statuses and (
                    all(status == "completed" for status in statuses)
                    or any(status == "failed" for status in statuses)
                ):
                    return last_response
            elif response.status_code == 404:
                raise HTTPException(status_code=404, detail="Workflow not found")
        except requests.RequestException:
            pass
        time.sleep(1)
    return last_response


def _resolve_ref(ref: dict[str, Any]) -> dict[str, Any] | None:
    protocol = ref.get("protocol")
    uri = ref.get("uri")
    if not uri:
        return None

    try:
        if protocol == "inline":
            payload = json.loads(base64.b64decode(uri).decode("utf-8"))
        elif protocol == "file":
            with Path(uri).open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
        else:
            return {"reference": ref}
    except Exception as exc:
        return {"reference": ref, "error": str(exc)}

    return payload if isinstance(payload, dict) else {"value": payload}


def _load_outputs(task_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for task in task_list:
        for ref in task.get("output_refs", []):
            payload = _resolve_ref(ref)
            if payload is None:
                continue
            outputs.append(
                {
                    "task_id": task.get("task_id"),
                    "service": task.get("service") or task.get("service_name"),
                    "method": task.get("method") or task.get("method_name"),
                    "reference": ref,
                    "payload": payload,
                }
            )
    return outputs


def _workflow_status_from_tasks(workflow_id: str, tasks: list[dict[str, Any]]) -> WorkflowStatusResponse:
    failure = next((task.get("error") for task in tasks if task.get("status") == "failed"), None)
    status = "running"
    if tasks and all(task.get("status") == "completed" for task in tasks):
        status = "completed"
    elif tasks and any(task.get("status") == "failed" for task in tasks):
        status = "failed"

    return WorkflowStatusResponse(
        workflow_id=workflow_id,
        status=status,
        outputs=_load_outputs(tasks),
        tasks=tasks,
        error=failure,
    )


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _find_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_value(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, key)
            if found is not None:
                return found
    return None


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    manifest = _load_manifest()
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    content = template.replace("{{ title }}", html.escape(str(manifest.get("title", "AI-EFFECT Workflow"))))
    content = content.replace("{{ description }}", html.escape(str(manifest.get("description", ""))))
    return HTMLResponse(content=content)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ui/config")
def ui_config() -> dict[str, Any]:
    return _load_manifest()


@app.post("/submit")
def submit_workflow(payload: SubmitRequest) -> dict[str, Any]:
    blueprint, dockerinfo = _read_export()
    payload_b64 = base64.b64encode(json.dumps(payload.inputs).encode("utf-8")).decode("utf-8")
    request_body = {
        "blueprint": blueprint,
        "dockerinfo": dockerinfo,
        "inputs": [{"protocol": "inline", "uri": payload_b64, "format": "json"}],
    }

    response = requests.post(f"{ORCHESTRATOR_URL}/workflows", json=request_body, timeout=20)
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    workflow_id = response.json()["workflow_id"]
    tasks = _poll_workflow(workflow_id).get("tasks", [])
    return _workflow_status_from_tasks(workflow_id, tasks).model_dump()


@app.post("/decision")
def record_decision(payload: DecisionRequest) -> dict[str, str | None]:
    if not payload.workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id is required")

    manifest = _load_manifest()
    allowed_actions = {action.get("name") for action in _as_list(manifest.get("actions"))}
    if allowed_actions and payload.action not in allowed_actions:
        raise HTTPException(status_code=400, detail="Unsupported workflow decision")

    WORKFLOW_DECISIONS[payload.workflow_id] = {"action": payload.action, "value": payload.value}
    return {
        "status": payload.action,
        "message": f"Decision recorded for workflow {payload.workflow_id}.",
        "value": payload.value,
    }


@app.get("/workflows/{workflow_id}")
def workflow_status(workflow_id: str) -> WorkflowStatusResponse:
    response = requests.get(f"{ORCHESTRATOR_URL}/workflows/{workflow_id}/tasks", timeout=20)
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    return _workflow_status_from_tasks(workflow_id, response.json().get("tasks", []))


@app.get("/workflows/{workflow_id}/assets/{signature}")
def workflow_asset(workflow_id: str, signature: str) -> FileResponse:
    status = workflow_status(workflow_id)
    manifest = _load_manifest()
    collection_field = manifest.get("output", {}).get("image_collection_field", "drawings")
    signature_field = manifest.get("output", {}).get("image_signature_field", "signature")

    for output in status.outputs or []:
        drawings = _find_value(output.get("payload", {}), collection_field)
        if not isinstance(drawings, list):
            continue
        for drawing in drawings:
            if not isinstance(drawing, dict) or str(drawing.get(signature_field)) != signature:
                continue
            uri = drawing.get("uri")
            if not uri:
                continue
            asset_path = Path(uri).resolve()
            workflow_directory = (DATA_DIR / workflow_id).resolve()
            if workflow_directory in asset_path.parents and asset_path.is_file():
                return FileResponse(asset_path, media_type=drawing.get("media_type") or "image/svg+xml")

    raise HTTPException(status_code=404, detail="Workflow asset is not available")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")))