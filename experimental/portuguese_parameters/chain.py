"""Versioned whole-output model handoff and additive bounded authorizations.

Only this experimental sidecar uses these tables. Legacy jobs/models/limits are
retained. No arbitrary URL is fetched: HTTP model references resolve in the local
journal, then a private read-only observer checks the exact derived model file.
"""
from datetime import datetime, timezone
import base64
import csv
import hashlib
import hmac
import io
import json
from pathlib import Path
import re
import httpx

CHAIN_VERSION = "0.2.0"
CONTRACTS = json.loads(Path(__file__).with_name("chain_contracts.json").read_text())


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def job_id(workflow, task):
    return hashlib.sha256(json.dumps([workflow, task]).encode()).hexdigest()


def core(refs):
    return [{k: ref[k] for k in ("protocol", "uri", "format")} for ref in refs]


def declarations():
    return [{"operation_name": c["runtime_operation_name"], "operation_identity": c["operation_identity"],
        "contract_version": CHAIN_VERSION, "execution_parameters": c["definition"]["execution_parameters"],
        "binding_transport": "whole-output-v1", "io_contract_sha256": digest(c["definition"])} for c in CONTRACTS]


class ChainMixin:
    def initialize_chain(self):
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS authorizations(
                    id TEXT PRIMARY KEY, policy TEXT NOT NULL, policy_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL, workflow_id TEXT UNIQUE, created_at TEXT NOT NULL, closed_at TEXT);
                CREATE TABLE IF NOT EXISTS authorization_events(
                    id INTEGER PRIMARY KEY, authorization_id TEXT NOT NULL, action TEXT NOT NULL,
                    actor TEXT NOT NULL, at TEXT NOT NULL, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS authorized_jobs(
                    job_id TEXT PRIMARY KEY, authorization_id TEXT NOT NULL, method TEXT NOT NULL,
                    UNIQUE(authorization_id,method));
                CREATE TABLE IF NOT EXISTS chain_models(
                    ref TEXT PRIMARY KEY, owner TEXT NOT NULL, workflow_id TEXT NOT NULL,
                    producer_task TEXT NOT NULL, producer_job TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL, name TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
                    model_proof TEXT NOT NULL, authorization_id TEXT NOT NULL);
            """)

    def event(self, db, authorization, action, actor, details):
        db.execute("INSERT INTO authorization_events VALUES(NULL,?,?,?,?,?)",
            (authorization, action, actor, datetime.now(timezone.utc).isoformat(), canonical_json(details)))

    def create_authorization(self, policy):
        from .runtime import canonical, capabilities
        expected = {"schema_version", "authorization_id", "owner_id", "actor", "reason", "request_sha256",
            "dataset_sha256", "tasks", "resources", "binding"}
        if (set(policy) != expected or policy["schema_version"] != "pt-chain-authorization-v1"
            or policy["owner_id"] != self.policy["owner_id"] or not policy["actor"] or not policy["reason"]
            or policy["dataset_sha256"] != self.policy["dataset_sha256"]
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", policy["authorization_id"])
            or not re.fullmatch(r"[a-f0-9]{64}", policy["request_sha256"])):
            raise ValueError("Explicit attributable isolated authorization required")
        tasks = policy["tasks"]
        if len(tasks) != 2 or {t["method"] for t in tasks.values()} != {"TrainModel", "GenerateData"}:
            raise ValueError("Exactly one training and one generation task required")
        for task, value in tasks.items():
            if not re.fullmatch(r"task_[a-f0-9]{8}", task) or set(value) != {"method", "configuration", "inputs"}:
                raise ValueError("Invalid frozen task policy")
            canonical.match_wrapper_configuration(value["method"], value["configuration"], capabilities())
            contract = next(c for c in CONTRACTS if c["runtime_operation_name"] == value["method"])
            if value["configuration"]["contract_id"] != contract["id"] or value["configuration"]["contract_version"] != CHAIN_VERSION:
                raise ValueError("Authorization requires the versioned chained contracts")
        train = next(k for k,v in tasks.items() if v["method"] == "TrainModel")
        gen = next(k for k,v in tasks.items() if v["method"] == "GenerateData")
        if policy["binding"] != {"producer_task": train, "consumer_task": gen, "source_slot": "model_ref", "target_slot": "model_ref"}:
            raise ValueError("Explicit producer-to-consumer model reference binding required")
        dataset = {"protocol": "inline", "format": "json", "uri": base64.b64encode(json.dumps({"dataset_ref": self.policy["dataset_ref"]}, ensure_ascii=False, allow_nan=False).encode()).decode()}
        if core(tasks[train]["inputs"]) != [dataset] or tasks[gen]["inputs"] != "producer:model_ref":
            raise ValueError("Dataset workflow input and produced model input must remain separate")
        ml = policy["resources"]["ml"]
        if ml["cpus"] != 2 or ml["mem_limit"] != "3g":
            raise ValueError("Existing isolated ML resource limits required")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM authorizations WHERE state IN ('prepared','active')").fetchone():
                raise ValueError("Another execution authorization is unresolved")
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('running','uncertain','failed')").fetchone():
                raise ValueError("Existing failed or unresolved dispatch prevents activation")
            db.execute("INSERT INTO authorizations VALUES(?,?,?,'prepared',NULL,?,NULL)",
                (policy["authorization_id"], canonical_json(policy), digest(policy), datetime.now(timezone.utc).isoformat()))
            self.event(db, policy["authorization_id"], "created", policy["actor"], {"policy_sha256": digest(policy), "max_workflows": 1, "max_training": 1, "max_generation": 1})
        return policy["authorization_id"]

    def bind_authorization(self, identity, workflow, request_sha256, actor):
        if not re.fullmatch(r"wf-[a-f0-9]{12}", workflow):
            raise ValueError("Returned workflow identity is invalid")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM authorizations WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] != "prepared" or json.loads(row["policy"])["request_sha256"] != request_sha256:
                raise ValueError("Authorization is unavailable or request snapshot changed")
            db.execute("UPDATE authorizations SET state='active',workflow_id=? WHERE id=?", (workflow, identity))
            self.event(db, identity, "bound", actor, {"workflow_id": workflow, "request_sha256": request_sha256})

    def close_authorization(self, identity, actor):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM authorizations WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] == "closed":
                raise ValueError("Unknown or already closed authorization")
            jobs = db.execute("SELECT j.id,j.state FROM jobs j JOIN authorized_jobs a ON a.job_id=j.id WHERE a.authorization_id=?", (identity,)).fetchall()
            if any(j["state"] == "running" for j in jobs):
                raise ValueError("Cannot close while a dispatch is running")
            db.execute("UPDATE authorizations SET state='closed',closed_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), identity))
            self.event(db, identity, "closed", actor, {"jobs": [dict(j) for j in jobs]})

    def chain_authority(self, request):
        from .runtime import canonical, capabilities
        canonical.match_wrapper_configuration(request.method, request.configuration, capabilities())
        contract = next((c for c in CONTRACTS if c["runtime_operation_name"] == request.method), None)
        if not contract or request.configuration["contract_id"] != contract["id"]:
            raise ValueError("Wrong chained contract")
        with self.db() as db:
            row = db.execute("SELECT * FROM authorizations WHERE workflow_id=? AND state='active'", (request.workflow_id,)).fetchone()
        if not row or digest(json.loads(row["policy"])) != row["policy_sha256"]:
            raise ValueError("No active authorization for this exact workflow")
        policy = json.loads(row["policy"])
        expected = policy["tasks"].get(request.task_id)
        if (not expected or expected["method"] != request.method or expected["configuration"] != request.configuration
            or policy["owner_id"] != self.policy["owner_id"]):
            raise ValueError("Task, owner or settings differ from the frozen authorization")
        return dict(row), policy, expected

    def resolve_chain_input(self, request, policy, expected):
        if len(request.inputs) != 1:
            raise ValueError("One exact authorized reference required")
        if request.method == "TrainModel":
            if core(request.inputs) != core(expected["inputs"]):
                raise ValueError("Training requires its separate approved dataset reference")
            # Reject covert authentication, settings or deployment data in references.
            if any(request.inputs[0].get(k) for k in set(request.inputs[0]) - {"protocol","uri","format"}):
                raise ValueError("Unexpected data-reference metadata")
            data = (self.storage / "dataset.csv").read_bytes()
            if hashlib.sha256(data).hexdigest() != policy["dataset_sha256"]:
                raise ValueError("Synthetic input integrity differs")
            return data
        producer_task = policy["binding"]["producer_task"]
        producer = job_id(request.workflow_id, producer_task)
        expected_ref = {"protocol": "http", "format": "json", "uri": self.self_url + "/control/data/" + producer}
        if core(request.inputs) != [expected_ref]:
            raise ValueError("Model input must be this workflow's authorized producer output; URLs are never fetched")
        if set(request.inputs[0]) - {"protocol","uri","format","schema_uri","size_bytes","checksum","metadata"} or any(request.inputs[0].get(k) for k in ("schema_uri","metadata")):
            raise ValueError("Unsupported model-reference metadata")
        with self.db() as db:
            source = db.execute("SELECT * FROM jobs WHERE id=?", (producer,)).fetchone()
            model = db.execute("SELECT * FROM chain_models WHERE producer_job=? AND owner=? AND workflow_id=? AND producer_task=? AND authorization_id=?",
                (producer,self.policy["owner_id"],request.workflow_id,producer_task,policy["authorization_id"])).fetchone()
        if not source or source["state"] != "complete" or not model:
            raise ValueError("Produced model is unknown, incomplete, stale or unauthorized")
        proof = json.loads(source["result"])
        output = (self.storage / (producer + ".json")).read_bytes()
        if (hashlib.sha256(output).hexdigest() != proof["sha256"] or json.loads(output) != {"model_ref": model["ref"]}
            or proof["model"]["model_sha256"] != model["artifact_sha256"]):
            raise ValueError("Produced model association or output integrity differs")
        if (request.inputs[0].get("checksum") not in (None, "sha256:"+proof["sha256"])
            or request.inputs[0].get("size_bytes") not in (None, len(output))):
            raise ValueError("Producer reference checksum or size differs")
        return dict(model)

    def accept_chain(self, request):
        authority, policy, expected = self.chain_authority(request)
        resource = self.resolve_chain_input(request, policy, expected)
        identity = job_id(request.workflow_id, request.task_id)
        body = request.model_dump(); fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Recheck within reservation transaction: closure cannot race new dispatch.
            if db.execute("SELECT state FROM authorizations WHERE id=?", (authority["id"],)).fetchone()["state"] != "active":
                raise ValueError("Execution authorization closed")
            prior = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint: raise ValueError("Conflicting immutable replay")
                return identity
            if db.execute("SELECT 1 FROM jobs j JOIN authorized_jobs a ON a.job_id=j.id WHERE a.authorization_id=? AND j.state IN ('failed','uncertain','running')", (authority["id"],)).fetchone():
                raise ValueError("Earlier authorized task unresolved or failed; no redispatch")
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,NULL)", (identity,fingerprint,request.method,json.dumps(body),"running"))
            db.execute("INSERT INTO authorized_jobs VALUES(?,?,?)", (identity,authority["id"],request.method))
            self.event(db, authority["id"], "reserved", "runtime", {"job_id":identity,"task_id":request.task_id,"method":request.method})
        self.executor.submit(self.perform_chain, identity, request, resource, policy)
        return identity

    def observe_model(self, client, workflow, task):
        if not self.model_proof_url:
            raise ValueError("Read-only model observer is unavailable; no substitute model")
        response = client.post(self.model_proof_url + "/control/model-evidence",
            json={"workflow_id":workflow,"task_id":task},headers={"Authorization":"Bearer "+self.token})
        if response.status_code != 200: raise ValueError("Model artifact could not be independently observed")
        value=response.json()
        if (value["workflow_id"]!=workflow or value["task_id"]!=task or value["evidence_schema_version"]!="2.0"
            or value["model_identity"]!="model_"+job_id(workflow,task)[:32]
            or not re.fullmatch(r"[a-f0-9]{64}",value["model_sha256"])):
            raise ValueError("Observed model identity or version differs")
        return value

    def perform_chain(self, identity, request, resource, policy):
        from .runtime import verified_count
        evidence={"evidence_schema_version":"2.0","workflow_id":request.workflow_id,"task_id":request.task_id,
            "instance_id":request.configuration["instance_id"],"operation_name":request.method,"owner_id":self.policy["owner_id"],
            "authorization_id":policy["authorization_id"],"authorization_policy_sha256":digest(policy),
            "configuration":request.configuration,"inputs":core(request.inputs),"requested_effective":request.configuration["parameters"],
            "upstream_completion":False,"historical":True}
        state="uncertain"
        try:
            with httpx.Client(timeout=600,transport=self.transport,trust_env=False,follow_redirects=False) as client:
                params=request.configuration["parameters"]
                if request.method=="TrainModel":
                    user="sandbox_"+hashlib.sha256(self.policy["owner_id"].encode()).hexdigest()[:20]
                    name="model_"+identity[:32]
                    args={"user_id":user,"model_name":name,"index_col":"datetime","sequence_len":100,"overwrite":False,**params}
                    response=client.post(self.upstream+"/train",params=args,files={"uploaded_file":("synthetic.csv",resource,"text/csv")})
                    if response.status_code!=200: raise ValueError("Synchronous training failed; generation is blocked")
                    models=client.get(self.upstream+"/models",params={"user_id":user})
                    if models.status_code!=200 or name not in [v.get("name") for v in models.json()]:
                        raise ValueError("Saved model is not listed")
                    model=self.observe_model(client,request.workflow_id,request.task_id)
                    rows=sum(1 for _ in csv.DictReader(io.StringIO(resource.decode())))
                    config=model["configuration"]
                    if config!={"epochs":params["epochs"],"batch_size":min(params["batch_size"],rows//100),"max_sequence_len":100,"sample_len":1}:
                        raise ValueError("Saved model settings differ from the documented implementation")
                    model.update(producer_workflow_id=request.workflow_id,producer_task_id=request.task_id,
                        model_ref="pt-model-"+identity,owner_id=self.policy["owner_id"],user_id=user)
                    with self.db() as db:
                        db.execute("INSERT INTO chain_models VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (model["model_ref"],self.policy["owner_id"],request.workflow_id,request.task_id,identity,user,name,
                             model["model_sha256"],canonical_json(model),policy["authorization_id"]))
                    data=json.dumps({"model_ref":model["model_ref"]}).encode();fmt="json"
                    evidence["algorithm_consumed"]={"epochs":config["epochs"],"batch_size":config["batch_size"]}
                    evidence["completion_basis"]="Synchronous HTTP 200, saved model listing and independently read saved model configuration/hash"
                else:
                    model=self.observe_model(client,request.workflow_id,resource["producer_task"])
                    if model["model_sha256"]!=resource["artifact_sha256"]:
                        raise ValueError("Produced model artifact changed; no generation")
                    model=json.loads(resource["model_proof"])
                    args={"user_id":resource["user_id"],"model_name":resource["name"],"number_of_examples":params["number_of_examples"],"return_csv":True}
                    response=client.get(self.upstream+"/generate",params=args)
                    if response.status_code!=200: raise ValueError("Generation failed; no retry or substitute model")
                    data=response.content;fmt="csv"
                    evidence.update(verified_count(data,params["number_of_examples"]))
                    evidence["algorithm_consumed"]={"number_of_examples":evidence["verified_examples"]}
                    evidence["input_binding_verified"]=True
                    evidence["producer_output_sha256"]=hashlib.sha256((self.storage/(resource["producer_job"]+".json")).read_bytes()).hexdigest()
                evidence.update(model=model,upstream_arguments=args,upstream_completion=True,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
                evidence["output"]={"protocol":"http","uri":self.self_url+"/control/data/"+identity,"format":fmt,
                    "checksum":"sha256:"+evidence["sha256"],"size_bytes":len(data)}
                (self.storage/(identity+"."+fmt)).write_bytes(data)
                state="complete"
        except httpx.HTTPError:
            evidence["error"]="Upstream outcome uncertain; authorization consumed; no retry"
        except (ValueError,KeyError,TypeError,OSError) as exc:
            state="failed";evidence["error"]=str(exc) if isinstance(exc,ValueError) else "Experimental processing failed"
        finally:
            evidence["terminal_state"]=state
            evidence["signature"]=hmac.new(self.token.encode(),canonical_json(evidence).encode(),hashlib.sha256).hexdigest()
            (self.storage/(identity+".evidence.json")).write_text(canonical_json(evidence))
            with self.db() as db:
                db.execute("UPDATE jobs SET state=?,result=? WHERE id=?",(state,canonical_json(evidence),identity))
                self.event(db,policy["authorization_id"],"observed","runtime",{"job_id":identity,"state":state})
