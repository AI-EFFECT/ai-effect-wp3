"""Explicit opt-in server, using only the separate sandbox storage and policy."""
import json
import os
from pathlib import Path
import uvicorn
from .runtime import Sandbox, create_app


def application():
    policy = json.loads(Path(os.environ["PT_SANDBOX_POLICY"]).read_text())
    token = Path(os.environ["PT_SANDBOX_TOKEN_FILE"]).read_text().strip()
    return create_app(Sandbox(os.environ["PT_SANDBOX_STORAGE"], policy, token=token,
        upstream=os.environ["PT_SANDBOX_UPSTREAM"], self_url=os.environ["PT_SANDBOX_SELF_URL"],
        model_proof_url=os.environ.get("PT_SANDBOX_MODEL_PROOF_URL")))


if __name__ == "__main__":
    uvicorn.run(application(), host="0.0.0.0", port=8080, workers=1)
