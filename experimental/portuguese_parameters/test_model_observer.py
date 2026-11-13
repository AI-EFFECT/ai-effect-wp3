"""OFFLINE read-only observer tests. Torch loader doubles; no ML execution."""
import hashlib
import json
from pathlib import Path
import sys
from fastapi.testclient import TestClient
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from experimental.portuguese_parameters.model_observer import create_app,observe,job_identity


def test_observer_reads_only_derived_model_and_its_saved_configuration(tmp_path):
    owner="designer";workflow="wf-one";task="task-one"
    path=tmp_path/("sandbox_"+hashlib.sha256(owner.encode()).hexdigest()[:20])/("model_"+job_identity(workflow,task)[:32])/"model.pt"
    path.parent.mkdir(parents=True);path.write_bytes(b"offline model fixture")
    calls=[]
    def loader(p):
        calls.append(p)
        return {"config":{"epochs":2,"batch_size":4,"max_sequence_len":100,"sample_len":1}}
    client=TestClient(create_app({"owner_id":owner},"x"*40,str(tmp_path),loader))
    body={"workflow_id":workflow,"task_id":task}
    assert client.post("/control/model-evidence",json=body).status_code==401
    headers={"Authorization":"Bearer "+"x"*40}
    response=client.post("/control/model-evidence",json=body,headers=headers)
    assert response.status_code==200 and response.json()["configuration"]["batch_size"]==4
    assert response.json()["model_sha256"]==hashlib.sha256(path.read_bytes()).hexdigest()
    assert calls==[path.resolve()]
    assert client.post("/control/model-evidence",json={**body,"model_path":str(path)},headers=headers).status_code==422
    assert client.post("/control/model-evidence",json={**body,"workflow_id":"other"},headers=headers).status_code==404


def test_observer_never_accepts_another_owner_model(tmp_path):
    with pytest.raises(FileNotFoundError):
        observe(tmp_path,"other","wf-one","task-one",lambda _:None)
