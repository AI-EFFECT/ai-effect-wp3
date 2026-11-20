"""Shared, manifest-driven user interface for AI-EFFECT workflows.

One image serves every workflow. Each workflow mounts its use-case directory and
describes itself in ``ui.json`` (inputs, steps, decision gates, theme, plugins);
``connections.json`` and the ``export/`` files describe what is submitted to the
orchestrator.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://ai-effect-orchestrator:8000")
# Must match the orchestrator's ORCHESTRATOR_API_KEY when that is set.
ORCHESTRATOR_API_KEY = os.environ.get("ORCHESTRATOR_API_KEY") or None
# Optional workflow-specific services. A feature is disabled when its URL is unset.
CONFIG_ASSISTANT_URL = os.environ.get("CONFIG_ASSISTANT_URL") or None
DATA_INSPECTION_URL = os.environ.get("DATA_INSPECTION_URL") or None
USE_CASE_DIR = Path(os.environ.get("USE_CASE_DIR", "/workspace/use-case"))
EXPORT_PATH = Path(os.environ.get("EXPORT_PATH", str(USE_CASE_DIR / "export")))
UI_MANIFEST_PATH = Path(os.environ.get("UI_MANIFEST_PATH", str(USE_CASE_DIR / "ui.json")))
PLUGIN_DIR = Path(os.environ.get("UI_PLUGIN_DIR", str(USE_CASE_DIR / "ui")))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024)))
TEMPLATE_PATH = BASE_DIR / "templates" / "index.html"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
PLUGIN_NAME = re.compile(r"^[A-Za-z0-9_-]+\.js$")

logger = logging.getLogger(__name__)
app = FastAPI(title="AI-EFFECT Workflow UI", version="3.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class SubmitRequest(BaseModel):
    inputs: dict[str, Any]


class AssistantRequest(BaseModel):
    messages: list[dict[str, str]]


class DecisionRequest(BaseModel):
    workflow_id: str
    action: str
    step: str | None = None
    value: str | None = None


class InspectUploadRequest(BaseModel):
    path: str


class WorkflowStatusResponse(BaseModel):
    workflow_id: str
    status: str
    outputs: list[dict[str, Any]] | None = None
    tasks: list[dict[str, Any]] | None = None
    decisions: dict[str, dict[str, Any]] | None = None
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
    try:
        return _read_json(path, "connections.json is not available for this workflow.")
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid %s", path)
        return {}


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
        "steps": [],
        "output": {
            "title": "Workflow output",
            "summary_fields": ["summary", "reason", "status", "message"],
            "image_collection_field": "drawings",
            "image_signature_field": "signature",
        },
        "actions": [],
        "plugins": [],
        "pipeline": pipeline,
    }


def _load_manifest() -> dict[str, Any]:
    """Merge the workflow's ui.json over the defaults.

    A missing or unreadable ui.json does not break the page, but the problem is
    reported in ``manifest_warning`` so the UI can show it instead of silently
    presenting the generic form.
    """
    manifest = _default_manifest()
    warning = None
    try:
        with UI_MANIFEST_PATH.open("r", encoding="utf-8") as fp:
            configured = json.load(fp)
        if not isinstance(configured, dict):
            raise ValueError("the top level must be a JSON object")
    except FileNotFoundError:
        warning = f"No UI manifest found at {UI_MANIFEST_PATH}; showing the generic workflow form."
    except (json.JSONDecodeError, ValueError) as exc:
        warning = f"The UI manifest {UI_MANIFEST_PATH.name} is invalid ({exc}); showing the generic workflow form."
    else:
        manifest.update(configured)
        manifest["pipeline"] = {**_pipeline_metadata(), **configured.get("pipeline", {})}

    if warning:
        logger.warning(warning)
        manifest["manifest_warning"] = warning
    manifest["features"] = {
        "assistant": bool(CONFIG_ASSISTANT_URL) and manifest.get("assistant", {}).get("enabled") is True,
        "upload_inspection": bool(DATA_INSPECTION_URL),
    }
    return manifest


def _manifest_plugins(manifest: dict[str, Any]) -> list[str]:
    return [name for name in _as_list(manifest.get("plugins")) if isinstance(name, str) and PLUGIN_NAME.match(name)]


def _upload_categories(manifest: dict[str, Any]) -> set[str]:
    return {
        field["upload_category"]
        for field in _as_list(manifest.get("inputs"))
        if isinstance(field, dict)
        and field.get("type") == "file"
        and isinstance(field.get("upload_category"), str)
        and SAFE_NAME.match(field["upload_category"])
    }


def _gate_actions(manifest: dict[str, Any], step: str | None) -> set[str]:
    """Actions allowed for a decision: a step's gate, or the workflow-level actions."""
    if step is None:
        actions = manifest.get("actions")
    else:
        found = next(
            (item for item in _as_list(manifest.get("steps")) if isinstance(item, dict) and item.get("method") == step),
            None,
        )
        actions = (found or {}).get("gate", {}).get("actions") if found else None
    return {action.get("name") for action in _as_list(actions) if isinstance(action, dict)}


def _orchestrator_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ORCHESTRATOR_API_KEY}"} if ORCHESTRATOR_API_KEY else {}


def _poll_workflow(workflow_id: str, timeout_seconds: int = 2) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_response: dict[str, Any] = {"tasks": []}
    while time.time() < deadline:
        try:
            response = requests.get(
                f"{ORCHESTRATOR_URL}/workflows/{workflow_id}/tasks", headers=_orchestrator_headers(), timeout=10
            )
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


def _workflow_dir(workflow_id: str) -> Path:
    workflow_directory = (DATA_DIR / workflow_id).resolve()
    if DATA_DIR.resolve() not in workflow_directory.parents:
        raise HTTPException(status_code=400, detail="Invalid workflow ID")
    return workflow_directory


def _decisions_path(workflow_id: str) -> Path:
    return _workflow_dir(workflow_id) / "ui-decisions.json"


def _read_decisions(workflow_id: str) -> dict[str, dict[str, Any]]:
    try:
        path = _decisions_path(workflow_id)
        with path.open("r", encoding="utf-8") as fp:
            decisions = json.load(fp)
    except (FileNotFoundError, HTTPException, json.JSONDecodeError):
        return {}
    return decisions if isinstance(decisions, dict) else {}


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
        decisions=_read_decisions(workflow_id),
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


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "upload").name
    normalized = "".join(character for character in name if character.isalnum() or character in ".-_")
    if not normalized or normalized in {".", ".."}:
        raise HTTPException(status_code=400, detail="Upload filename is invalid")
    return normalized


def _brand(manifest: dict[str, Any]) -> tuple[str, str]:
    brand = manifest.get("brand") if isinstance(manifest.get("brand"), dict) else {}
    name = str(brand.get("name") or manifest.get("title") or "AI-EFFECT Workflow")
    mark = str(brand.get("mark") or name[:1] or "A")[:2]
    return mark, name


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    manifest = _load_manifest()
    mark, name = _brand(manifest)
    plugin_tags = "\n    ".join(
        f'<script src="/plugins/{html.escape(plugin)}" defer></script>' for plugin in _manifest_plugins(manifest)
    )
    replacements = {
        "{{ title }}": html.escape(str(manifest.get("title", "AI-EFFECT Workflow"))),
        "{{ description }}": html.escape(str(manifest.get("description", ""))),
        "{{ brand_mark }}": html.escape(mark),
        "{{ brand_name }}": html.escape(name),
        "{{ plugin_scripts }}": plugin_tags,
    }
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    return HTMLResponse(content=content)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ui/config")
def ui_config() -> dict[str, Any]:
    return _load_manifest()


@app.get("/plugins/{name}")
def plugin_script(name: str) -> FileResponse:
    if name not in _manifest_plugins(_load_manifest()):
        raise HTTPException(status_code=404, detail="Plugin is not declared in ui.json")
    path = (PLUGIN_DIR / name).resolve()
    if PLUGIN_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Plugin file is not available")
    return FileResponse(path, media_type="text/javascript")


@app.post("/assistant/configure")
def configure_analysis(payload: AssistantRequest) -> dict[str, Any]:
    if not CONFIG_ASSISTANT_URL:
        raise HTTPException(status_code=503, detail="No setup assistant is configured for this workflow.")
    try:
        response = requests.post(CONFIG_ASSISTANT_URL, json=payload.model_dump(), timeout=200)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail="The setup assistant is unavailable. Try again or continue to manual setup.",
        ) from exc

    try:
        result = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="The setup assistant returned an invalid response.") from exc

    if not response.ok:
        detail = result.get("detail") if isinstance(result, dict) else None
        raise HTTPException(
            status_code=response.status_code if response.status_code < 500 else 502,
            detail=detail or "The setup assistant could not process this request.",
        )
    if not isinstance(result, dict) or result.get("status") not in {"ready", "clarification", "unsupported"}:
        raise HTTPException(status_code=502, detail="The setup assistant returned an invalid response.")
    return result


@app.post("/uploads")
async def upload_file(file: UploadFile = File(...), category: str = Form(...)) -> dict[str, str | int]:
    allowed = _upload_categories(_load_manifest())
    if category not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Upload category must be one of: {', '.join(sorted(allowed)) or 'none configured'}",
        )

    filename = _safe_filename(file.filename)
    destination_dir = UPLOAD_DIR / uuid.uuid4().hex / category
    destination_dir.mkdir(parents=True, exist_ok=False)
    destination = destination_dir / filename
    bytes_written = 0

    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds the {MAX_UPLOAD_BYTES} byte limit",
                    )
                output.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    return {"path": str(destination), "filename": filename, "size": bytes_written}


@app.post("/uploads/inspect")
def inspect_upload(payload: InspectUploadRequest) -> dict[str, Any]:
    path = Path(payload.path).resolve()
    if UPLOAD_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=422, detail="Inspection requires an uploaded data file.")
    if not DATA_INSPECTION_URL:
        raise HTTPException(status_code=503, detail="The file is uploaded, but this workflow has no file inspection.")
    try:
        response = requests.post(DATA_INSPECTION_URL, json={"path": str(path)}, timeout=30)
        result = response.json()
    except (requests.RequestException, requests.exceptions.JSONDecodeError) as exc:
        logger.warning("Upload inspection is unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="The file is uploaded, but feature detection is unavailable. Review feature_names manually or retry.",
        ) from exc
    if not response.ok:
        detail = result.get("detail") if isinstance(result, dict) else None
        raise HTTPException(status_code=422, detail=detail or "Could not inspect this data file.")
    if not isinstance(result, dict) or result.get("kind") not in {"table", "tensor", "unavailable"}:
        raise HTTPException(status_code=502, detail="Data inspection returned an invalid response.")
    return result


@app.post("/submit")
def submit_workflow(payload: SubmitRequest) -> dict[str, Any]:
    blueprint, dockerinfo = _read_export()
    payload_b64 = base64.b64encode(json.dumps(payload.inputs).encode("utf-8")).decode("utf-8")
    request_body = {
        "blueprint": blueprint,
        "dockerinfo": dockerinfo,
        "inputs": [{"protocol": "inline", "uri": payload_b64, "format": "json"}],
    }

    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/workflows", json=request_body, headers=_orchestrator_headers(), timeout=20
        )
    except requests.RequestException as exc:
        logger.exception("Could not submit workflow to orchestrator at %s", ORCHESTRATOR_URL)
        raise HTTPException(
            status_code=503,
            detail="Could not reach the orchestrator. Check that it is running and the web UI's ORCHESTRATOR_URL is reachable.",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    workflow_id = response.json()["workflow_id"]
    tasks = _poll_workflow(workflow_id).get("tasks", [])
    return _workflow_status_from_tasks(workflow_id, tasks).model_dump()


@app.post("/decision")
def record_decision(payload: DecisionRequest) -> dict[str, Any]:
    """Record a human decision on a step's gate (or on the whole workflow).

    Decisions are stored next to the workflow's data so they survive restarts.
    The orchestrator does not pause for them yet; once it supports gates, this
    is the place to forward the decision so the workflow can resume.
    """
    if not payload.workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id is required")
    if payload.action not in _gate_actions(_load_manifest(), payload.step):
        raise HTTPException(status_code=400, detail="Unsupported workflow decision")

    decision = {
        "action": payload.action,
        "value": payload.value,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _decisions_path(payload.workflow_id)
    decisions = _read_decisions(payload.workflow_id)
    decisions[payload.step or "workflow"] = decision
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    return {
        "status": payload.action,
        "step": payload.step,
        "value": payload.value,
        "message": f"Decision recorded for workflow {payload.workflow_id}.",
    }


@app.get("/workflows/{workflow_id}")
def workflow_status(workflow_id: str) -> WorkflowStatusResponse:
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/workflows/{workflow_id}/tasks", headers=_orchestrator_headers(), timeout=20
        )
    except requests.RequestException as exc:
        logger.exception("Could not retrieve workflow status from orchestrator at %s", ORCHESTRATOR_URL)
        raise HTTPException(
            status_code=503,
            detail="Could not reach the orchestrator to check progress. Your workflow may still be running; retry the status check.",
        ) from exc
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
            workflow_directory = _workflow_dir(workflow_id)
            if workflow_directory in asset_path.parents and asset_path.is_file():
                return FileResponse(asset_path, media_type=drawing.get("media_type") or "image/svg+xml")

    raise HTTPException(status_code=404, detail="Workflow asset is not available")


@app.get("/workflows/{workflow_id}/files/{file_path:path}")
def workflow_file(workflow_id: str, file_path: str) -> FileResponse:
    workflow_directory = _workflow_dir(workflow_id)
    asset_path = (workflow_directory / file_path).resolve()
    if workflow_directory not in asset_path.parents or not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Workflow file is not available")
    return FileResponse(asset_path)


@app.get("/workflows/{workflow_id}/tasks/{task_id}/download")
def workflow_download(workflow_id: str, task_id: str) -> StreamingResponse:
    workflow_directory = _workflow_dir(workflow_id)
    task_directory = (workflow_directory / task_id).resolve()
    if workflow_directory not in task_directory.parents or not task_directory.is_dir():
        raise HTTPException(status_code=404, detail="Workflow task is not available")

    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
        for path in task_directory.rglob("*"):
            if path.is_file():
                zip_file.write(path, path.relative_to(task_directory).as_posix())
    archive.seek(0)
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{task_id}-results.zip"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")))
