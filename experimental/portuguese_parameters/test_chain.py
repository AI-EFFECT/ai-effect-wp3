"""OFFLINE chain protocol/journal checks with upstream and model-observer doubles."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experimental.portuguese_parameters.runtime import Sandbox, create_app, capabilities
from experimental.portuguese_parameters.chain import CONTRACTS, job_id, digest
from experimental.portuguese_parameters.authorization import AuthorizationOperator

WF="wf-111111111111"
TRAIN="task_11111111"
GEN="task_22222222"


def configuration(method):
    contract=next(c for c in CONTRACTS if c["runtime_operation_name"]==method)
    return {"contract_id":contract["id"],"contract_version":"0.2.0","operation_identity":contract["operation_identity"],
        "schema_version":"1.0","instance_id":"train" if method=="TrainModel" else "generate",
        "parameters":{"epochs":2,"batch_size":4} if method=="TrainModel" else {"number_of_examples":10}}


@pytest.fixture
def chain(tmp_path):
    dataset=b"datetime,value\n"+b"2026-01-01,1\n"*400
    (tmp_path/"dataset.csv").write_bytes(dataset)
    policy={"owner_id":"designer","sequence_len":100,"model_ref":"historical-model","dataset_ref":"data",
        "dataset_sha256":hashlib.sha256(dataset).hexdigest()}
    calls=[];fault={"value":None};models={}
    def upstream(request):
        calls.append(request)
        if request.url.host=="observer":
            args=json.loads(request.content);identity=job_id(args["workflow_id"],args["task_id"])
            return httpx.Response(200,json={"evidence_schema_version":"2.0",**args,"model_identity":"model_"+identity[:32],
                "model_sha256":"f"*64 if fault["value"]=="model-changed" else "a"*64,
                "configuration":{"epochs":2,"batch_size":4,"max_sequence_len":100,"sample_len":1}})
        if request.url.path=="/train":
            if fault["value"]=="timeout":raise httpx.ReadTimeout("uncertain")
            if fault["value"]=="training":return httpx.Response(500)
            models[request.url.params["model_name"]]=True
            return httpx.Response(200,text="saved")
        if request.url.path=="/models":return httpx.Response(200,json=[{"name":name} for name in models])
        return httpx.Response(200,text="datetime,value,example\n"+"".join(f"2026-01-01,1,{e}\n" for e in range(1,11) for _ in range(100)))
    sandbox=Sandbox(tmp_path,policy,token="x"*40,upstream="http://ml",self_url="http://adapter",
        model_proof_url="http://observer",transport=httpx.MockTransport(upstream))
    # Previously exhausted standalone authorization stays at one/two, unchanged.
    with sandbox.db() as db:
        for i,method in enumerate(("TrainModel","GenerateData","GenerateData")):
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)",("legacy"+str(i),"old",method,"{}","complete","{}"))
    dataref={"protocol":"inline","format":"json","uri":base64.b64encode(json.dumps({"dataset_ref":"data"}).encode()).decode()}
    grant={"schema_version":"pt-chain-authorization-v1","authorization_id":"chain-phase","owner_id":"designer",
        "actor":"user-approved-test","reason":"Explicit new bounded phase; protocol doubles only","request_sha256":"b"*64,
        "dataset_sha256":policy["dataset_sha256"],"resources":{"ml":{"cpus":2,"mem_limit":"3g"}},
        "tasks":{TRAIN:{"method":"TrainModel","configuration":configuration("TrainModel"),"inputs":[dataref]},
                 GEN:{"method":"GenerateData","configuration":configuration("GenerateData"),"inputs":"producer:model_ref"}},
        "binding":{"producer_task":TRAIN,"consumer_task":GEN,"source_slot":"model_ref","target_slot":"model_ref"}}
    operator=AuthorizationOperator(tmp_path,policy)
    client=TestClient(create_app(sandbox),headers={"Authorization":"Bearer "+"x"*40})
    env={"sandbox":sandbox,"client":client,"operator":operator,"grant":grant,"calls":calls,"fault":fault,"dataref":dataref}
    yield env
    sandbox.executor.shutdown(wait=True)


def activate(env,wf=WF,identity=None):
    policy=deepcopy(env["grant"])
    if identity:policy["authorization_id"]=identity
    env["operator"].create_authorization(policy)
    env["operator"].bind_authorization(policy["authorization_id"],wf,policy["request_sha256"],"operator")
    return policy["authorization_id"]


def request(env,method="TrainModel",wf=WF):
    return {"method":method,"workflow_id":wf,"task_id":TRAIN if method=="TrainModel" else GEN,
        "configuration":configuration(method),
        "inputs":[env["dataref"]] if method=="TrainModel" else
            [{"protocol":"http","format":"json","uri":"http://adapter/control/data/"+job_id(wf,TRAIN)}]}


def finish(env,body):
    response=env["client"].post("/control/execute",json=body)
    assert response.status_code==200,response.text
    env["sandbox"].executor.submit(lambda:None).result(timeout=10)
    return env["sandbox"].job(response.json()["task_id"])


def test_real_routes_forward_exact_producer_and_preserve_legacy_authorization(chain):
    activate(chain)
    train=finish(chain,request(chain))
    gen=finish(chain,request(chain,"GenerateData"))
    assert train["state"]==gen["state"]=="complete"
    p=json.loads(train["result"]);g=json.loads(gen["result"])
    assert p["algorithm_consumed"]=={"epochs":2,"batch_size":4}
    assert g["algorithm_consumed"]=={"number_of_examples":10} and g["csv_rows"]==1000
    assert g["model"]["producer_task_id"]==TRAIN and g["model"]==p["model"]
    assert g["producer_output_sha256"]==p["sha256"] and g["input_binding_verified"]
    actual=[r for r in chain["calls"] if r.url.path=="/generate"]
    assert len(actual)==1 and actual[0].url.params["model_name"]==p["model"]["model_identity"]
    assert not any(r.url.host not in {"ml","observer"} for r in chain["calls"])
    view=chain["operator"].view()
    assert view["legacy_jobs"]==3 and len(view["scoped_jobs"])==2
    chain["operator"].close_authorization("chain-phase","operator")
    assert chain["client"].post("/control/execute",json=request(chain)).status_code==422
    with chain["sandbox"].db() as db:
        assert [tuple(r) for r in db.execute("SELECT id,fingerprint,method,request,state,result FROM jobs WHERE id LIKE 'legacy%' ORDER BY id")]==[
            ("legacy"+str(i),"old",m,"{}","complete","{}") for i,m in enumerate(("TrainModel","GenerateData","GenerateData"))]


@pytest.mark.parametrize("mutation",["unbound","owner","wrong-workflow","wrong-task","settings","contract-version","unknown-parameter"])
def test_unapproved_drift_never_mutates_journal_or_calls_upstream(chain,mutation):
    if mutation=="owner":
        policy=deepcopy(chain["grant"]);policy["owner_id"]="other"
        with pytest.raises(ValueError):chain["operator"].create_authorization(policy)
        return
    if mutation!="unbound":activate(chain)
    value=request(chain)
    if mutation=="wrong-workflow":value["workflow_id"]="wf-222222222222"
    if mutation=="wrong-task":value["task_id"]="task_33333333"
    if mutation=="settings":value["configuration"]["parameters"]["epochs"]=1
    if mutation=="contract-version":value["configuration"]["contract_version"]="0.3.0"
    if mutation=="unknown-parameter":value["configuration"]["parameters"]["overwrite"]=True
    assert chain["client"].post("/control/execute",json=value).status_code==422
    assert not chain["calls"] and not chain["operator"].view()["scoped_jobs"]


@pytest.mark.parametrize("uri",["https://attacker/model","file:///models/secret","http://adapter/control/data/unknown",
    "http://adapter/control/data/"+job_id("wf-222222222222",TRAIN)])
def test_unknown_cross_workflow_and_arbitrary_model_references_are_not_fetched(chain,uri):
    activate(chain);finish(chain,request(chain));before=len(chain["calls"])
    value=request(chain,"GenerateData");value["inputs"][0]["uri"]=uri
    assert chain["client"].post("/control/execute",json=value).status_code==422
    assert len(chain["calls"])==before


def test_model_artifact_change_prevents_generation_without_substitute(chain):
    activate(chain);finish(chain,request(chain));chain["fault"]["value"]="model-changed"
    gen=finish(chain,request(chain,"GenerateData"))
    assert gen["state"]=="failed"
    assert not any(r.url.path=="/generate" for r in chain["calls"])


def test_corrupt_model_reference_artifact_is_rejected_before_reservation(chain):
    activate(chain);finish(chain,request(chain))
    (chain["sandbox"].storage/(job_id(WF,TRAIN)+".json")).write_text('{"model_ref":"foreign"}')
    assert chain["client"].post("/control/execute",json=request(chain,"GenerateData")).status_code==422
    assert len(chain["operator"].view()["scoped_jobs"])==1


@pytest.mark.parametrize("fault,state",[("training","failed"),("timeout","uncertain")])
def test_failed_or_uncertain_training_blocks_generation_and_never_retries(chain,fault,state):
    activate(chain);chain["fault"]["value"]=fault
    result=finish(chain,request(chain));assert result["state"]==state
    assert chain["client"].post("/control/execute",json=request(chain,"GenerateData")).status_code==422
    before=len(chain["calls"])
    assert chain["client"].post("/control/execute",json=request(chain)).status_code==200
    assert len(chain["calls"])==before and len(chain["operator"].view()["scoped_jobs"])==1


def test_concurrent_duplicate_dispatch_and_operator_status_do_not_restart_jobs(chain):
    activate(chain)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:chain["client"].post("/control/execute",json=request(chain)),range(2)))
    chain["sandbox"].executor.submit(lambda:None).result(timeout=10)
    assert all(r.status_code==200 for r in results)
    assert len([r for r in chain["calls"] if r.url.path=="/train"])==1
    assert chain["operator"].view()["scoped_jobs"][0]["state"]=="complete"


def test_new_authorization_cannot_reopen_old_and_model_names_are_run_specific(chain):
    activate(chain);one=finish(chain,request(chain));finish(chain,request(chain,"GenerateData"))
    chain["operator"].close_authorization("chain-phase","operator")
    with pytest.raises(Exception):chain["operator"].create_authorization(chain["grant"])
    activate(chain,wf="wf-222222222222",identity="separate-offline-proof")
    two=finish(chain,request(chain,wf="wf-222222222222"))
    assert json.loads(one["result"])["model"]["model_identity"]!=json.loads(two["result"])["model"]["model_identity"]
    assert chain["operator"].view()["legacy_jobs"]==3


def test_capabilities_match_exact_versioned_io_export():
    from experimental.portuguese_parameters.chain import declarations
    assert all(v["binding_transport"]=="whole-output-v1" for v in declarations())
    assert len(capabilities()["operations"])==4
    for contract in CONTRACTS:
        op=next(v for v in declarations() if v["operation_name"]==contract["runtime_operation_name"])
        assert op["io_contract_sha256"]==digest(contract["definition"])


def test_library_batch_adjustment_remains_distinct_from_validated_request(chain):
    chain["grant"]["tasks"][TRAIN]["configuration"]["parameters"]["batch_size"]=6
    activate(chain)
    body=request(chain);body["configuration"]["parameters"]["batch_size"]=6
    result=finish(chain,body);assert result["state"]=="complete"
    proof=json.loads(result["result"])
    assert proof["requested_effective"]["batch_size"]==6
    assert proof["algorithm_consumed"]["batch_size"]==4
