"""The legacy direct-client API is distinct from validated workflow configuration."""
import json
import pytest
from services.control_client import ControlClient


@pytest.mark.parametrize("parameters", [None, {}, {"batch_size": 100, "verbose": True}])
def test_optional_legacy_parameter_body(httpx_mock, parameters):
    httpx_mock.add_response(method="POST", url="http://fixture/control/execute", json={"status": "complete"})
    ControlClient().execute("http://fixture", "ProcessData", "wf-fixture", "task-fixture", parameters=parameters)
    body = json.loads(httpx_mock.get_request().content)
    if parameters is None:
        assert set(body) == {"method", "workflow_id", "task_id", "inputs"}
    else:
        assert body["parameters"] == parameters
