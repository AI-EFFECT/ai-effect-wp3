"""Unit tests for per-node parameters and start-node inputs."""

from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi.testclient import TestClient

from api.app import OrchestratorAPI
from models.data_reference import DataReference
from models.graph import ExecutionGraph, GraphNode
from models.node import Node, OperationSignature, OperationSignatureList
from services.control_client import ControlClient, ExecuteResponse
from services.dockerinfo_parser import ServiceEndpoint
from services.state_store import RedisStateStore
from services.task_queue import RedisTaskQueue
from services.worker import Worker
from services.workflow_engine import WorkflowEngine


def _graph_node(container: str, operation: str = "Process") -> GraphNode:
    node = Node(
        container_name=container,
        proto_uri="",
        image=f"{container}:latest",
        node_type="MLModel",
        operation_signature_list=[],
    )
    op = OperationSignatureList(operation_signature=OperationSignature(operation_name=operation))
    node.operation_signature_list.append(op)
    return GraphNode(node=node, operation=op)


def chain_graph() -> ExecutionGraph:
    """loader:Load -> trainer:Train, trainer:Report (two ops on one container)."""
    graph = ExecutionGraph()
    load = _graph_node("loader", "Load")
    train = _graph_node("trainer", "Train")
    report = _graph_node("trainer", "Report")
    load.next_nodes.append(train)
    train.dependencies.append(load)
    train.next_nodes.append(report)
    report.dependencies.append(train)
    for n in (load, train, report):
        graph.add_node(n)
    graph.start_nodes.append(load)
    return graph


def inline_ref(text: str) -> DataReference:
    return DataReference(protocol="inline", uri=text, format="json")


@pytest.fixture
def engine():
    redis = fakeredis.FakeRedis()
    return WorkflowEngine(RedisStateStore(redis), RedisTaskQueue(redis), redis)


class TestResolveNodeParameters:
    def test_container_key_applies_to_all_operations(self):
        resolved = WorkflowEngine.resolve_node_parameters(chain_graph(), {"trainer": {"epochs": 3}})
        assert resolved == {"trainer:Train": {"epochs": 3}, "trainer:Report": {"epochs": 3}}

    def test_node_key_overrides_container_key(self):
        resolved = WorkflowEngine.resolve_node_parameters(
            chain_graph(),
            {"trainer:Train": {"epochs": 5}, "trainer": {"epochs": 3, "seed": 1}},
        )
        assert resolved["trainer:Train"] == {"epochs": 5, "seed": 1}
        assert resolved["trainer:Report"] == {"epochs": 3, "seed": 1}

    def test_unknown_node_rejected(self):
        with pytest.raises(ValueError, match="unknown node 'trainr'"):
            WorkflowEngine.resolve_node_parameters(chain_graph(), {"trainr": {"epochs": 3}})

    def test_non_object_values_rejected(self):
        with pytest.raises(ValueError, match="must be an object"):
            WorkflowEngine.resolve_node_parameters(chain_graph(), {"trainer": 3})

    def test_empty_returns_empty(self):
        assert WorkflowEngine.resolve_node_parameters(chain_graph(), None) == {}


class TestResolveNodeInputs:
    def test_start_node_accepted(self):
        ref = inline_ref("e30=")
        assert WorkflowEngine.resolve_node_inputs(chain_graph(), {"loader": [ref]}) == {"loader:Load": [ref]}

    def test_downstream_node_rejected(self):
        with pytest.raises(ValueError, match="only target start nodes"):
            WorkflowEngine.resolve_node_inputs(chain_graph(), {"trainer:Train": [inline_ref("e30=")]})


class TestEngineStoresParameters:
    def test_parameters_stored_on_tasks(self, engine):
        engine.initialize_workflow("wf-1", chain_graph(), {"trainer:Train": {"epochs": 3}})
        tasks = {t.node_key: t for t in engine.get_all_tasks("wf-1")}
        assert tasks["trainer:Train"].parameters == {"epochs": 3}
        assert tasks["loader:Load"].parameters == {}

    def test_claimed_task_keeps_parameters(self, engine):
        graph = ExecutionGraph()
        only = _graph_node("svc", "Run")
        graph.add_node(only)
        graph.start_nodes.append(only)
        engine.initialize_workflow("wf-2", graph, {"svc": {"round_decimals": 2}})
        engine.start_workflow("wf-2")
        task = engine.claim_task("wf-2")
        assert task.parameters == {"round_decimals": 2}

    def test_node_inputs_replace_shared_inputs_for_that_node(self, engine):
        graph = ExecutionGraph()
        a, b = _graph_node("a", "Run"), _graph_node("b", "Run")
        for n in (a, b):
            graph.add_node(n)
            graph.start_nodes.append(n)
        engine.initialize_workflow("wf-3", graph)
        shared, own = inline_ref("c2hhcmVk"), inline_ref("b3du")
        engine.start_workflow("wf-3", [shared], {"a:Run": [own]})
        tasks = {t.node_key: t for t in engine.get_all_tasks("wf-3")}
        assert [r.uri for r in tasks["a:Run"].input_refs] == ["b3du"]
        assert [r.uri for r in tasks["b:Run"].input_refs] == ["c2hhcmVk"]


class TestWorkerForwardsParameters:
    def test_parameters_sent_to_service(self, engine):
        graph = ExecutionGraph()
        only = _graph_node("svc", "Run")
        graph.add_node(only)
        graph.start_nodes.append(only)
        engine.initialize_workflow("wf-4", graph, {"svc:Run": {"compress": True}})
        engine.start_workflow("wf-4")
        client = MagicMock(spec=ControlClient)
        client.execute.return_value = ExecuteResponse(status="complete")
        worker = Worker(engine, client, {"svc": ServiceEndpoint(address="svc", port=8080)})
        worker.process_task("wf-4")
        assert client.execute.call_args.kwargs["parameters"] == {"compress": True}


class TestSubmitApi:
    @pytest.fixture
    def setup(self):
        engine = MagicMock()
        parser = MagicMock()
        parser.parse_json.return_value = chain_graph()
        docker = MagicMock()
        docker.parse_json.return_value = {}
        api = OrchestratorAPI(engine, parser, docker, MagicMock())
        return engine, TestClient(api.create_app())

    def test_parameters_forwarded_to_engine(self, setup):
        engine, client = setup
        resp = client.post(
            "/workflows",
            json={"blueprint": {"x": 1}, "dockerinfo": {"y": 1}, "parameters": {"trainer": {"epochs": 3}}},
        )
        assert resp.status_code == 200
        assert engine.initialize_workflow.call_args.args[2] == {"trainer": {"epochs": 3}}

    def test_unknown_node_returns_400(self, setup):
        engine, client = setup
        resp = client.post(
            "/workflows",
            json={"blueprint": {"x": 1}, "dockerinfo": {"y": 1}, "parameters": {"nope": {"a": 1}}},
        )
        assert resp.status_code == 400
        assert "unknown node 'nope'" in resp.json()["detail"]
        engine.initialize_workflow.assert_not_called()

    def test_node_inputs_on_downstream_node_returns_400(self, setup):
        _, client = setup
        resp = client.post(
            "/workflows",
            json={
                "blueprint": {"x": 1},
                "dockerinfo": {"y": 1},
                "node_inputs": {"trainer:Train": [{"protocol": "inline", "uri": "e30=", "format": "json"}]},
            },
        )
        assert resp.status_code == 400
