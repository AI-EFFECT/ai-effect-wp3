"""Explicit one-shot standby for one authorization; disabled unless opted in.

Uses the maintained Worker and per-workflow queue unchanged. Never discovers or
processes other workflow IDs. Interrupted/uncertain attempts require inspection.
"""
import argparse
import json
import os
from pathlib import Path
import time
from uuid import uuid4
from .authorization import AuthorizationOperator
from . import supervision


def serve(operator, identity, probe, process, *, wait_seconds=300, sleep=time.sleep):
    worker = str(uuid4())
    supervision.register(operator, identity, worker)
    deadline = time.monotonic() + wait_seconds
    try:
        while time.monotonic() < deadline:
            supervision.heartbeat(operator, identity, worker, probe)
            with operator.db() as db:
                row = db.execute("SELECT state FROM authorizations WHERE id=?", (identity,)).fetchone()
            if row["state"] == "active":
                process(supervision.claim(operator, identity, worker))
                return
            sleep(1)
        raise ValueError("Waiting for confirmation expired; no workflow processed")
    finally:
        supervision.finish(operator, identity, worker)


def main():
    if os.environ.get("PT_SANDBOX_SUPERVISED") != "1":
        raise ValueError("Supervised processing is disabled")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("authorization_id")
    a = p.parse_args()
    import redis
    import httpx
    from worker_cli import load_endpoints
    from services.control_client import ControlClient
    from services.state_store import RedisStateStore
    from services.task_queue import RedisTaskQueue
    from services.workflow_engine import WorkflowEngine
    from services.worker import Worker
    r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True, socket_timeout=3, socket_connect_timeout=3)
    token = Path(os.environ["PT_SANDBOX_TOKEN_FILE"]).read_text().strip()
    operator = AuthorizationOperator(os.environ["PT_SANDBOX_STORAGE"], json.loads(Path(os.environ["PT_SANDBOX_POLICY"]).read_text()))
    engine = WorkflowEngine(RedisStateStore(r), RedisTaskQueue(r), r)
    def probe():
        if not r.ping():
            raise ValueError("Redis unavailable")
        # Do not approve standby alongside unresolved work, including empty queues
        # belonging to a nonterminal workflow. No values/credentials are logged.
        with operator.db() as db:
            bound = db.execute("SELECT workflow_id FROM authorizations WHERE id=?", (a.authorization_id,)).fetchone()["workflow_id"]
        for key in r.scan_iter("workflow:*"):
            if key.count(":") == 1 and key != "workflow:" + str(bound):
                state = json.loads(r.get(key))
                if state["status"] not in {"completed", "failed"}:
                    raise ValueError("Unresolved unrelated workflow; no processing")
        for key in r.scan_iter("queue:*"):
            if key != "queue:" + str(bound) and r.llen(key):
                raise ValueError("Unresolved unrelated queue; no processing")
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            # The retained ML API has no /health route. Inspect its published
            # schema, never /train or /generate, while waiting for confirmation.
            schema = client.get(os.environ["PT_SANDBOX_UPSTREAM"] + "/openapi.json")
            schema.raise_for_status()
            if not all(path in schema.json().get("paths", {}) for path in ("/train", "/generate", "/models")):
                raise ValueError("Retained ML API schema unavailable")
            client.get(os.environ["PT_SANDBOX_MODEL_PROOF_URL"] + "/health").raise_for_status()
            response = client.get(os.environ["PT_SANDBOX_SELF_URL"] + "/control/capabilities", headers={"Authorization": "Bearer " + token})
            response.raise_for_status()
            from .runtime import capabilities
            if response.json() != capabilities():
                raise ValueError("Adapter capability drift")
    def process(workflow):
        endpoints = load_endpoints(r, workflow)
        if r.get("services_key:" + workflow) != token:
            raise ValueError("Per-workflow service authority differs")
        Worker(engine, ControlClient(api_key=token), endpoints, poll_interval=1).run(workflow, timeout=1)
    serve(operator, a.authorization_id, probe, process)


if __name__ == "__main__":
    main()
