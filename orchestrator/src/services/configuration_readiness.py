"""Parameter compatibility uses target wrapper declarations, not a node registry."""
from services.control_client import ControlClient, ControlClientError
from services.parameter_contract import validate_configuration_envelope
import hashlib


def check_configurations(graph, endpoints, configurations, api_key=None, inputs=None):
    selected_csv = any(ref.get("protocol") == "inline" and ref.get("format") == "csv" for ref in (inputs or []))
    if not configurations and not selected_csv:
        return {"status": "ready", "checks": {}, "mode": "legacy"}
    if set(configurations) - set(graph.all_nodes):
        raise ValueError("Configuration targets an unknown graph instance")
    identities = [validate_configuration_envelope(value)["instance_id"] for value in configurations.values()]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate logical instance identifier")
    task_ids = {hashlib.sha256(key.encode()).hexdigest()[:8] for key in graph.all_nodes}
    if len(task_ids) != len(graph.all_nodes):
        raise ValueError("Runtime task identity collision")
    client, checks = ControlClient(api_key=api_key), {}
    for key, configuration in configurations.items():
        node = graph.all_nodes[key]
        endpoint = endpoints.get(node.node.container_name)
        if endpoint is None:
            raise ValueError("Configured instance has no runtime endpoint")
        checks[key] = client.check_configuration(f"http://{endpoint.address}:{endpoint.port}",
            node.operation.operation_signature.operation_name, configuration)
    result = {"status": "ready", "checks": checks, "mode": "configured"}
    if selected_csv:
        result["input_checks"] = {}
        for node in graph.start_nodes:
            endpoint = endpoints.get(node.node.container_name)
            if endpoint is None: raise ValueError("Selected input has no start-node endpoint")
            result["input_checks"][node.key] = client.check_inputs(f"http://{endpoint.address}:{endpoint.port}",
                node.operation.operation_signature.operation_name, inputs, configurations.get(node.key))
    return result
