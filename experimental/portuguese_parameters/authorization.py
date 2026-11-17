"""Supported additive journal operator. Never starts workers or algorithms."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from .chain import ChainMixin


class AuthorizationOperator(ChainMixin):
    def __init__(self, storage, policy):
        self.storage, self.policy = Path(storage), policy
        if not (self.storage / "journal.sqlite3").is_file():
            raise ValueError("Existing sandbox journal required; no replacement journal")
        self.initialize_chain()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.storage / "journal.sqlite3", timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db: yield db
        finally: db.close()

    def view(self):
        with self.db() as db:
            return {"authorizations":[dict(r) for r in db.execute("SELECT * FROM authorizations ORDER BY created_at")],
                "events":[dict(r) for r in db.execute("SELECT * FROM authorization_events ORDER BY id")],
                "legacy_jobs":db.execute("SELECT count(*) FROM jobs WHERE id NOT IN (SELECT job_id FROM authorized_jobs)").fetchone()[0],
                "scoped_jobs":[dict(r) for r in db.execute("SELECT a.authorization_id,j.method,j.state,j.id FROM authorized_jobs a JOIN jobs j ON a.job_id=j.id")]}


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["create","bind","close","status"])
    parser.add_argument("--file",type=Path)
    parser.add_argument("--id");parser.add_argument("--workflow");parser.add_argument("--request-sha256")
    parser.add_argument("--actor",required=True)
    args=parser.parse_args()
    operator=AuthorizationOperator(os.environ["PT_SANDBOX_STORAGE"],json.loads(Path(os.environ["PT_SANDBOX_POLICY"]).read_text()))
    if args.action=="create":
        policy=json.loads(args.file.read_text())
        if policy["actor"]!=args.actor: raise ValueError("Attributable actor differs")
        operator.create_authorization(policy)
    elif args.action=="bind":operator.bind_authorization(args.id,args.workflow,args.request_sha256,args.actor)
    elif args.action=="close":operator.close_authorization(args.id,args.actor)
    print(json.dumps(operator.view(),indent=2))
