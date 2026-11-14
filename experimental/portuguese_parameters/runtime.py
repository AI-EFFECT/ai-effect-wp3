"""Experimental sidecar for the pinned Portuguese synchronous HTTP API.

No original Portuguese module is imported or modified. The maintained AIE-18
portable validator is imported byte-for-byte, never reimplemented here.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import csv
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import sys

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from .chain import ChainMixin, CHAIN_VERSION, declarations

ROOT = Path(__file__).resolve().parents[2]
# Load the portable export directly, without importing the orchestrator package
# (whose unrelated __init__ imports Redis/worker dependencies).
_spec = importlib.util.spec_from_file_location("pt_canonical_parameter_contract", ROOT / "orchestrator/src/services/parameter_contract.py")
canonical = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(canonical)

VERSION = "0.1.0"
CONTRACTS = {
    "GenerateData": {"id": "pt-sandbox-generate-v1", "identity": "urn:aieffect:experimental:portuguese:generate-data",
        "properties": {"number_of_examples": {"type": "integer", "title": "Generated sequences",
            "description": "Number of time-series sequences; each contains 100 observations in this sandbox.",
            "default": 10, "minimum": 1, "maximum": 20}}},
    "TrainModel": {"id": "pt-sandbox-train-v1", "identity": "urn:aieffect:experimental:portuguese:train-model",
        "properties": {
            "epochs": {"type": "integer", "title": "Training epochs", "description": "Sandbox limit, not a scientific recommendation.", "default": 1, "minimum": 1, "maximum": 2},
            "batch_size": {"type": "integer", "title": "Batch size", "description": "Upstream clamps to the number of training sequences.", "default": 4, "minimum": 2, "maximum": 16}}},
}


def definition(method):
    return {"schema_version": "1.0", "transport": "http-parameters-v1", "properties": CONTRACTS[method]["properties"]}


def capabilities():
    return {"capability_schema_version": "1.0", "transports": ["http-parameters-v1"],
        "implementation_kind": "platform_controlled_experimental_sidecar", "partner_native": False,
        "operations": [{"operation_name": name, "operation_identity": value["identity"], "contract_version": VERSION,
            "execution_parameters": definition(name)} for name, value in CONTRACTS.items()] + declarations()}


class Execute(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    method: str
    workflow_id: str = Field(min_length=1, max_length=300)
    task_id: str = Field(min_length=1, max_length=300)
    inputs: list[dict] = Field(max_length=1)
    configuration: dict


def verified_count(data, expected, sequence_len=100):
    """Count actual sequence markers, never treat the request or CSV rows as proof."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    if not reader.fieldnames or "example" not in reader.fieldnames:
        raise ValueError("Generated CSV has no sequence markers")
    counts = {}
    for row in reader:
        marker = row["example"]
        if not marker or not marker.isdecimal() or int(marker) < 1:
            raise ValueError("Invalid generated example marker")
        marker = int(marker)
        counts[marker] = counts.get(marker, 0) + 1
    if set(counts) != set(range(1, expected + 1)) or any(n != sequence_len for n in counts.values()):
        raise ValueError("Actual generated sequences/lengths differ from the contract")
    return {"verified_examples": len(counts), "rows_per_example": sequence_len, "csv_rows": sum(counts.values())}


class Sandbox(ChainMixin):
    def __init__(self, storage, policy, *, token, upstream, self_url, transport=None, model_proof_url=None):
        provenance = json.loads((ROOT / "orchestrator/parameter_contract_provenance.json").read_text())
        if hashlib.sha256(Path(canonical.__file__).read_bytes()).hexdigest() != provenance["sha256"]:
            raise ValueError("Canonical validator provenance differs")
        if len(token) < 32 or policy["sequence_len"] != 100 or not policy["owner_id"]:
            raise ValueError("Explicit isolated owner/token/fixed sequence policy required")
        self.storage = Path(storage)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.policy, self.token, self.upstream, self.self_url = policy, token, upstream.rstrip("/"), self_url.rstrip("/")
        self.transport = transport
        self.model_proof_url = model_proof_url
        self.executor = ThreadPoolExecutor(max_workers=1)
        with self.db() as db:
            db.executescript("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, method TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL, result TEXT); CREATE TABLE IF NOT EXISTS models(ref TEXT PRIMARY KEY, owner TEXT NOT NULL, user_id TEXT NOT NULL, name TEXT NOT NULL);")
            # A process death must never turn an uncertain dispatched request into a retry.
            db.execute("UPDATE jobs SET state='uncertain' WHERE state='running'")
        self.initialize_chain()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.storage / "journal.sqlite3", timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def validate(self, request):
        if request.method not in CONTRACTS:
            raise ValueError("Unsupported experimental operation")
        conf = request.configuration
        canonical.match_wrapper_configuration(request.method, conf, capabilities())
        if conf["contract_id"] != CONTRACTS[request.method]["id"]:
            raise ValueError("Wrong experimental registry contract")
        if len(request.inputs) != 1:
            raise ValueError("Exactly one authorized data reference is required")
        ref = request.inputs[0]
        if (ref.get("protocol") != "inline" or ref.get("format") != "json" or
            set(ref) - {"protocol", "uri", "format", "schema_uri", "size_bytes", "checksum", "metadata"} or
            any(ref.get(k) for k in ("schema_uri", "size_bytes", "checksum", "metadata"))):
            raise ValueError("Only an opaque authorized inline reference is supported")
        value = json.loads(base64.b64decode(ref["uri"], validate=True).decode("utf-8"))
        kind = "model_ref" if request.method == "GenerateData" else "dataset_ref"
        if value != {kind: self.policy[kind]}:
            raise ValueError("Reference is not authorized for this isolated owner")
        if request.method == "GenerateData":
            with self.db() as db:
                model = db.execute("SELECT * FROM models WHERE ref=? AND owner=?", (value[kind], self.policy["owner_id"])).fetchone()
            if not model:
                raise ValueError("Authorized test model is not ready; no default model fallback")
            return dict(model)
        dataset = (self.storage / "dataset.csv").read_bytes()
        if hashlib.sha256(dataset).hexdigest() != self.policy["dataset_sha256"]:
            raise ValueError("Approved dataset integrity differs")
        return dataset

    def accept(self, request):
        if request.configuration.get('contract_version') == CHAIN_VERSION:
            return self.accept_chain(request)
        resource = self.validate(request)  # All validation before journal mutation or upstream access.
        body = request.model_dump()
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        identity = hashlib.sha256(json.dumps([request.workflow_id, request.task_id]).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("Conflicting replay; submitted instance is immutable")
                return identity
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('failed','uncertain') LIMIT 1").fetchone():
                raise ValueError("Sandbox stopped after failure/uncertain response; no further dispatch")
            limit = 1 if request.method == "TrainModel" else 2
            if db.execute("SELECT count(*) FROM jobs WHERE method=? AND id NOT IN (SELECT job_id FROM authorized_jobs)", (request.method,)).fetchone()[0] >= limit:
                raise ValueError("Experimental execution ceiling exhausted; no replacement attempt")
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,NULL)", (identity, fingerprint, request.method, json.dumps(body), "running"))
        self.executor.submit(self.perform, identity, request, resource)
        return identity

    def perform(self, identity, request, resource):
        parameters = request.configuration["parameters"]
        evidence = {"workflow_id": request.workflow_id, "task_id": request.task_id,
            "instance_id": request.configuration["instance_id"], "requested_effective": parameters,
            "parameter_contract_sha256": canonical.parameter_contract_digest(definition(request.method)),
            "implementation": "platform_controlled_experimental_sidecar", "upstream_completion": False}
        state = "uncertain"
        try:
            with self.db() as db:
                if db.execute("SELECT 1 FROM jobs WHERE id<>? AND state IN ('failed','uncertain') LIMIT 1", (identity,)).fetchone():
                    raise ValueError("Sandbox stopped before dispatch because an earlier task failed")
            with httpx.Client(timeout=600, transport=self.transport, trust_env=False, follow_redirects=False) as client:
                if request.method == "TrainModel":
                    user = "sandbox_" + hashlib.sha256(self.policy["owner_id"].encode()).hexdigest()[:20]
                    name = "model_" + identity[:32]
                    args = {"user_id": user, "model_name": name, "index_col": "datetime", "sequence_len": 100,
                        "overwrite": False, **parameters}
                    response = client.post(self.upstream + "/train", params=args, files={"uploaded_file": ("synthetic.csv", resource, "text/csv")})
                    if response.status_code != 200:
                        raise ValueError("Synchronous training did not complete successfully")
                    models = client.get(self.upstream + "/models", params={"user_id": user})
                    if models.status_code != 200 or name not in [v.get("name") for v in models.json()]:
                        raise ValueError("Completed model artifact is not listed; training remains unverified")
                    with self.db() as db:
                        db.execute("INSERT INTO models VALUES(?,?,?,?)", (self.policy["model_ref"], self.policy["owner_id"], user, name))
                    data = json.dumps({"model_ref": self.policy["model_ref"]}).encode()
                    fmt = "json"
                    evidence["completion_basis"] = "Synchronous HTTP 200 after save and model listing; progress counters alone are not completion"
                else:
                    args = {"user_id": resource["user_id"], "model_name": resource["name"],
                        "number_of_examples": parameters["number_of_examples"], "return_csv": True}
                    response = client.get(self.upstream + "/generate", params=args)
                    if response.status_code != 200:
                        raise ValueError("Generation failed; no fallback or retry")
                    data, fmt = response.content, "csv"
                    evidence.update(verified_count(data, parameters["number_of_examples"]))
                evidence.update(upstream_arguments=args, upstream_completion=True,
                    sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
                (self.storage / (identity + "." + fmt)).write_bytes(data)
                evidence["output"] = {"protocol": "http", "uri": self.self_url + "/control/data/" + identity, "format": fmt,
                    "checksum": "sha256:" + evidence["sha256"], "size_bytes": len(data)}
                state = "complete"
        except (ValueError, KeyError, TypeError, OSError) as exc:
            state = "failed"
            evidence["error"] = str(exc) if isinstance(exc, ValueError) else "Experimental processing failed; no redispatch"
        except httpx.HTTPError:
            evidence["error"] = "Upstream outcome uncertain; execution consumed; no automatic redispatch"
        finally:
            with self.db() as db:
                db.execute("UPDATE jobs SET state=?,result=? WHERE id=?", (state, json.dumps(evidence), identity))

    def job(self, identity):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
        if not row:
            raise HTTPException(404, "Unknown experimental task")
        return dict(row)


def create_app(sandbox):
    app = FastAPI(title="EXPERIMENTAL Portuguese parameter sandbox")
    @app.get("/health")
    def health():
        return {"status": "ok", "scope": "platform-controlled experimental adapter; no scientific readiness claim"}
    def authorize(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, "Bearer " + sandbox.token):
            raise HTTPException(401, "Sandbox service authorization required")
    @app.get("/control/capabilities", dependencies=[Depends(authorize)])
    def capability(): return capabilities()
    @app.post("/control/execute", dependencies=[Depends(authorize)])
    def execute(request: Execute):
        try: identity = sandbox.accept(request)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise HTTPException(422, str(exc) if isinstance(exc, ValueError) else "Invalid experimental input") from None
        row = sandbox.job(identity)
        return {"task_id": identity, "status": "running" if row["state"] == "running" else "complete" if row["state"] == "complete" else "failed",
            **({"output": json.loads(row["result"])["output"]} if row["state"] == "complete" else {})}
    @app.get("/control/status/{identity}", dependencies=[Depends(authorize)])
    def status(identity: str):
        row = sandbox.job(identity)
        return {"status": "running" if row["state"] == "running" else "complete" if row["state"] == "complete" else "failed",
            "error": None if row["state"] in {"running", "complete"} else "Stopped; no automatic redispatch"}
    @app.get("/control/output/{identity}", dependencies=[Depends(authorize)])
    def output(identity: str):
        row = sandbox.job(identity)
        if row["state"] != "complete": raise HTTPException(409, "Task has no verified output")
        return {"output": json.loads(row["result"])["output"]}
    @app.get("/control/data/{identity}", dependencies=[Depends(authorize)])
    def data(identity: str):
        row = sandbox.job(identity)
        if row["state"] != "complete": raise HTTPException(409, "Task has no verified output")
        evidence = json.loads(row["result"]);fmt = evidence["output"]["format"]
        content = (sandbox.storage / (row["id"] + "." + fmt)).read_bytes()
        if hashlib.sha256(content).hexdigest() != evidence["sha256"]: raise HTTPException(409, "Artifact integrity differs")
        return Response(content, media_type="text/csv" if fmt == "csv" else "application/json")
    return app
