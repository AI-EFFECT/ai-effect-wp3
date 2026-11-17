"""One waiting processor for one existing authorization. No scheduling or grants.

Readiness never binds, reserves a job, pops a queue or calls an algorithm. The
separate opt-in worker refreshes a short lease only after its dependency probe.
"""
import hashlib
import json
import time
from .chain import digest

LEASE_SECONDS = 6


def initialize(operator):
    with operator.db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS supervised_processors(
            authorization_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL,
            state TEXT NOT NULL, lease_until REAL NOT NULL)""")


def register(operator, identity, worker):
    initialize(operator)
    with operator.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state FROM authorizations WHERE id=?", (identity,)).fetchone()
        if not row or row["state"] != "prepared":
            raise ValueError("A new prepared authorization is required; never reopen a scope")
        # A crashed processor is not permission to replace/retry it automatically.
        db.execute("INSERT INTO supervised_processors VALUES(?,?,'waiting',0)", (identity, worker))
        operator.event(db, identity, "processor_registered", worker, {})


def heartbeat(operator, identity, worker, probe, *, now=None):
    try:
        probe()  # Redis + private inspection endpoints; no queue pop / execute.
    except Exception:
        # A previously healthy lease must not survive a known failed probe.
        with operator.db() as db:
            db.execute("UPDATE supervised_processors SET lease_until=0 WHERE authorization_id=? AND worker_id=? AND state='waiting'",
                       (identity, worker))
        raise
    now = time.time() if now is None else now
    with operator.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state FROM authorizations WHERE id=?", (identity,)).fetchone()
        if not row or row["state"] not in {"prepared", "active"}:
            raise ValueError("Authorization closed or unavailable")
        changed = db.execute("UPDATE supervised_processors SET lease_until=? WHERE authorization_id=? AND worker_id=? AND state='waiting'",
                             (now + LEASE_SECONDS, identity, worker)).rowcount
        if changed != 1:
            raise ValueError("Processor is already consumed or unavailable")


def inspect(operator, value, *, now=None, db=None):
    if db is None:
        with operator.db() as connection:
            return inspect(operator, value, now=now, db=connection)
    if set(value) != {"authorization_id", "owner_id", "request_sha256", "tasks", "binding"}:
        raise ValueError("Exact supervised request required")
    row = db.execute("SELECT * FROM authorizations WHERE id=?", (value["authorization_id"],)).fetchone()
    if not row or row["state"] != "prepared":
        raise ValueError("Execution authorization is absent, closed or consumed")
    policy = json.loads(row["policy"])
    if digest(policy) != row["policy_sha256"] or any(policy[k] != value[k] for k in value):
        raise ValueError("Owner, request, parameters, inputs or binding differ from authorization")
    if hashlib.sha256((operator.storage / "dataset.csv").read_bytes()).hexdigest() != policy["dataset_sha256"]:
        raise ValueError("Authorized dataset integrity differs")
    if db.execute("SELECT 1 FROM jobs WHERE state IN ('running','uncertain','failed')").fetchone():
        raise ValueError("An earlier dispatch requires reconciliation")
    processor = db.execute("SELECT * FROM supervised_processors WHERE authorization_id=?", (row["id"],)).fetchone()
    now = time.time() if now is None else now
    if not processor or processor["state"] != "waiting" or not now < processor["lease_until"] <= now + LEASE_SECONDS:
        raise ValueError("Targeted processing is unavailable; no fresh waiting-processor lease")
    return {"status": "ready", "authorization_id": row["id"], "policy_sha256": row["policy_sha256"],
            "owner_id": policy["owner_id"], "request_sha256": policy["request_sha256"]}


def bind(operator, value, workflow, actor, *, expected_policy_sha256=None):
    import re
    if not re.fullmatch(r"wf-[a-f0-9]{12}", workflow):
        raise ValueError("Invalid returned workflow identity")
    with operator.db() as db:
        db.execute("BEGIN IMMEDIATE")
        result = inspect(operator, value, db=db)
        if expected_policy_sha256 is not None and result["policy_sha256"] != expected_policy_sha256:
            raise ValueError("Reviewed authorization policy changed")
        db.execute("UPDATE authorizations SET state='active',workflow_id=? WHERE id=?", (workflow, value["authorization_id"]))
        operator.event(db, value["authorization_id"], "bound", actor,
                       {"workflow_id": workflow, "request_sha256": value["request_sha256"], "supervised": True})
        return {**result, "workflow_id": workflow, "status": "bound"}


def claim(operator, identity, worker):
    with operator.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM authorizations WHERE id=?", (identity,)).fetchone()
        if not row or row["state"] != "active":
            raise ValueError("No active bound workflow")
        changed = db.execute("UPDATE supervised_processors SET state='processing',lease_until=0 WHERE authorization_id=? AND worker_id=? AND state='waiting' AND lease_until>?",
                             (identity, worker, time.time())).rowcount
        if changed != 1:
            raise ValueError("Processor claim unavailable; no automatic replacement")
        operator.event(db, identity, "processor_claimed", worker, {"workflow_id": row["workflow_id"]})
        return row["workflow_id"]


def finish(operator, identity, worker):
    with operator.db() as db:
        db.execute("UPDATE supervised_processors SET state='finished',lease_until=0 WHERE authorization_id=? AND worker_id=?", (identity, worker))
        operator.event(db, identity, "processor_stopped", worker, {})
