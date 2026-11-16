"""One processor, one existing scope. Probe retries never retry execution."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4
from .authorization import AuthorizationOperator
from .chain import digest
from .dependency_probe import (dependency_probe, TransientProbeError,
    STARTUP_SECONDS, POLL_SECONDS)
from . import supervision


def serve(operator, identity, probe, process, *, wait_seconds=300,
          startup_seconds=STARTUP_SECONDS, sleep=time.sleep, monotonic=time.monotonic):
    if not 0 < startup_seconds <= wait_seconds <= 300:
        raise ValueError("Startup <= standby deadline <= 300 seconds required")
    worker = str(uuid4())
    supervision.register(operator, identity, worker)
    deadline = monotonic() + wait_seconds
    startup_deadline = min(deadline, monotonic() + startup_seconds)
    with operator.db() as db:
        initial = db.execute("SELECT * FROM authorizations WHERE id=?", (identity,)).fetchone()
    policy_sha = initial["policy_sha256"]
    recovering = False
    try:
        while monotonic() < deadline:
            with operator.db() as db:
                row = db.execute("SELECT * FROM authorizations WHERE id=?", (identity,)).fetchone()
            if not row or row["policy_sha256"] != policy_sha or digest(json.loads(row["policy"])) != policy_sha:
                raise ValueError("Authorization policy drift; reconcile without replacing the processor")
            # Binding has crossed the admission boundary. Do not retry a probe,
            # claim or dispatch here. claim() still requires a fresh healthy lease.
            if row["state"] == "active":
                process(supervision.claim(operator, identity, worker))
                return
            if row["state"] != "prepared":
                raise ValueError("Authorization closed or unavailable")
            if recovering and monotonic() >= startup_deadline:
                raise ValueError("Read-only startup deadline expired; admission remains unavailable")
            def checked_probe():
                probe()
                if monotonic() >= deadline or (recovering and monotonic() >= startup_deadline):
                    raise TransientProbeError("Read-only startup deadline expired before healthy inspection")
            try:
                supervision.heartbeat(operator, identity, worker, checked_probe)
            except TransientProbeError as exc:
                recovering = True
                with operator.db() as db:
                    state = db.execute("SELECT state FROM authorizations WHERE id=?", (identity,)).fetchone()["state"]
                    operator.event(db, identity, "processor_probe_failed", worker,
                                   {"reason": str(exc), "transient": True})
                if state != "prepared" or monotonic() >= startup_deadline:
                    raise ValueError("Read-only startup deadline expired or workflow bound; no dispatch retry") from exc
                sleep(min(POLL_SECONDS, max(0, startup_deadline - monotonic())))
                continue
            recovering = False
            sleep(min(POLL_SECONDS, max(0, deadline - monotonic())))
        raise ValueError("Waiting for confirmation expired; no workflow processed")
    finally:
        supervision.finish(operator, identity, worker)


def main():
    if "--probe-only" not in sys.argv and os.environ.get("PT_SANDBOX_SUPERVISED") != "1":
        raise ValueError("Supervised processing is disabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("authorization_id", nargs="?")
    parser.add_argument("--probe-only", action="store_true",
                        help="One read-only dependency check; no journal/authority/worker initialization")
    args = parser.parse_args()
    import redis
    r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True,
                             socket_timeout=1, socket_connect_timeout=1)
    token = Path(os.environ["PT_SANDBOX_TOKEN_FILE"]).read_text().strip()
    queues_checked = False
    def probe():
        nonlocal queues_checked
        # Check all queues before the first lease. While waiting, exact Portal
        # admission may enqueue its root just before journal binding; health
        # renewal must not mistake that root for an unrelated workflow.
        dependency_probe(r, token, os.environ["PT_SANDBOX_UPSTREAM"],
                         os.environ["PT_SANDBOX_MODEL_PROOF_URL"], os.environ["PT_SANDBOX_SELF_URL"],
                         check_queues=not queues_checked)
        queues_checked = True
    if args.probe_only:
        if args.authorization_id:
            raise ValueError("Probe-only does not accept or create execution authority")
        probe()
        print(json.dumps({"status":"ready", "kind":"read_only_dependency_probe",
                          "workflow_creations":0, "algorithm_reservations":0}))
        return
    if not args.authorization_id:
        raise ValueError("One explicit existing authorization required")
    operator = AuthorizationOperator(os.environ["PT_SANDBOX_STORAGE"],
        json.loads(Path(os.environ["PT_SANDBOX_POLICY"]).read_text()))
    from worker_cli import load_endpoints
    from services.control_client import ControlClient
    from services.state_store import RedisStateStore
    from services.task_queue import RedisTaskQueue
    from services.workflow_engine import WorkflowEngine
    from services.worker import Worker
    def process(workflow):
        # Recheck every queue at the bound-workflow boundary, excluding only
        # that exact ID. This check is never retried after admission/claim.
        dependency_probe(r, token, os.environ["PT_SANDBOX_UPSTREAM"],
                         os.environ["PT_SANDBOX_MODEL_PROOF_URL"], os.environ["PT_SANDBOX_SELF_URL"],bound=workflow)
        if r.get("services_key:" + workflow) != token:
            raise ValueError("Per-workflow service authority differs")
        engine = WorkflowEngine(RedisStateStore(r), RedisTaskQueue(r), r)
        Worker(engine, ControlClient(api_key=token), load_endpoints(r, workflow),
               poll_interval=1).run(workflow, timeout=1)
    serve(operator, args.authorization_id, probe, process)


if __name__ == "__main__":
    main()
