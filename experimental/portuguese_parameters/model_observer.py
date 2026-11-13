"""Read-only model observation using retained ML dependencies; no algorithm call."""
import hashlib
import hmac
import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator


def job_identity(workflow, task):
    return hashlib.sha256(json.dumps([workflow, task]).encode()).hexdigest()


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workflow_id: str
    task_id: str
    @field_validator("workflow_id", "task_id", mode="before")
    @classmethod
    def exact_string(cls, value):
        if type(value) is not str or not 1 <= len(value) <= 300:
            raise ValueError("Explicit workflow and task identities required")
        return value


def observe(models, owner, workflow, task, loader=None):
    # Never accept a filename, URL, user or model name from the caller.
    user = "sandbox_" + hashlib.sha256(owner.encode()).hexdigest()[:20]
    name = "model_" + job_identity(workflow, task)[:32]
    root = Path(models).resolve()
    path = (root / user / name / "model.pt").resolve()
    if not path.is_relative_to(root) or path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("Model artifact path/size is invalid")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    if loader is None:
        import torch
        value = torch.load(str(path), map_location="cpu")
    else:
        value = loader(path)
    config = value["config"]
    config = config if isinstance(config, dict) else vars(config)
    selected = {k: config[k] for k in ("epochs", "batch_size", "max_sequence_len", "sample_len")}
    if any(type(v) is not int or v < 1 for v in selected.values()):
        raise ValueError("Saved model configuration is invalid")
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise ValueError("Model changed during observation")
    return {"evidence_schema_version": "2.0", "workflow_id": workflow, "task_id": task,
        "model_identity": name, "model_sha256": before, "configuration": selected}


def create_app(policy, token, models="/models", loader=None):
    app = FastAPI(title="EXPERIMENTAL read-only Portuguese model observer")
    @app.get("/health")
    def health():
        return {"status": "ok", "read_only": True}
    @app.post("/control/model-evidence")
    def evidence(request: ModelRequest, authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Sandbox authorization required")
        try:
            return observe(models, policy["owner_id"], request.workflow_id, request.task_id, loader)
        except FileNotFoundError:
            raise HTTPException(404, "Produced model artifact is unavailable") from None
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(409, "Model artifact observation failed integrity validation") from None
    return app


if __name__ == "__main__":
    import uvicorn
    policy = json.loads(Path(os.environ["PT_SANDBOX_POLICY"]).read_text())
    token = Path(os.environ["PT_SANDBOX_TOKEN_FILE"]).read_text().strip()
    uvicorn.run(create_app(policy, token), host="0.0.0.0", port=8081, access_log=False)
