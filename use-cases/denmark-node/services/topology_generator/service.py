"""Topology Generator for the Danish Node workflow.

Pipeline role: intermediate processor (receives config assistant output and
passes generated topologies to the renderer).
Operation exposed to the orchestrator: GenerateTopology.

Consumes a normalized run configuration and returns generated topology JSON
plus SVG drawings on the shared data volume.
"""

import base64
import json
import logging
import os
import sys
import time
from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any
from fastapi import HTTPException

from dh_network_generator import DEFAULT_TOPOLOGY, Infrastructure, enumerate_networks
from common.configuration import load_contract, output_path, write_output
from common.parameter_contract import match_wrapper_configuration
from handler import (
    DataReference,
    ExecuteRequest,
    ExecuteResponse,
    TaskManager,
    run,
    run_in_background,
    task_manager,
)

logger = logging.getLogger(__name__)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))


def configuration_capabilities():
    return load_contract(os.environ.get("SERVICE_PARAMETER_CONTRACT_FILE"), "GenerateTopology", {
        "allow_hx": "boolean", "allow_bypass": "boolean", "how_many": "integer"})


def resolve_input(inputs: list[dict], default: Any = None) -> Any:
    if not inputs:
        return default

    reference = inputs[0]
    protocol = reference.get("protocol")
    uri = reference.get("uri", "")
    if protocol == "inline":
        return json.loads(base64.b64decode(uri))
    if protocol == "file":
        path = Path(uri)
        if not path.is_file():
            raise ValueError(f"Input file not found on shared volume: {uri}")
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError(f"Unsupported input protocol '{protocol}'")


def publish_output(workflow_id: str, task_id: str, payload: dict) -> dict:
    output_dir = DATA_DIR / workflow_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{task_id}.json"
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"protocol": "file", "uri": str(output_path), "format": "json"}


def execute_GenerateTopology(request: ExecuteRequest) -> ExecuteResponse:
    """Generate a topology from the assistant's normalized run config."""
    if request.configuration is not None:
        # Also validate direct invocation before a task or background thread exists.
        match_wrapper_configuration(request.method, request.configuration, configuration_capabilities())
    # Removing configuration must not make a recorded configured output writable.
    output_path(DATA_DIR, request.workflow_id, request.task_id)
    configured_task_id = "cfg-" + hashlib.sha256(json.dumps([request.workflow_id, request.task_id]).encode()).hexdigest()
    if request.configuration is None and task_manager.get_status(configured_task_id) is not None:
        raise HTTPException(409, "A configured execution already owns this task identity")
    service_task_id = configured_task_id if request.configuration is not None else request.task_id
    task_manager.register_task(service_task_id, request)
    run_in_background(service_task_id, _generate_topology, request)
    return ExecuteResponse(status="running", task_id=service_task_id)


def execute_ValidateTopology(request: ExecuteRequest) -> ExecuteResponse:
    """Fast path: report a placeholder validation verdict."""
    design = resolve_input(request.inputs, default={}) or {}
    output = publish_output(
        request.workflow_id,
        request.task_id,
        {
            "experiment_id": design.get("experiment_id"),
            "valid": True,
            "violations": [],
            "generated_by": "topology_generator (dummy)",
        },
    )
    return ExecuteResponse(status="complete", output=DataReference(**output))


def _generate_topology(
    task_id: str,
    request: ExecuteRequest,
    manager: TaskManager,
) -> None:
    """Background worker: build a topology for each DoE run."""
    try:
        assistant_output = resolve_input(request.inputs)
        if not assistant_output or "run_config" not in assistant_output:
            raise ValueError(
                "GenerateTopology expects config_assistant output "
                "(a JSON object containing 'run_config')."
            )

        if not isinstance(assistant_output, dict) or not isinstance(assistant_output.get("run_config"), dict):
            raise ValueError("GenerateTopology requires a run_config object")
        config = deepcopy(assistant_output["run_config"])
        configuration = request.configuration
        matched = None
        if configuration is not None:
            if len(request.inputs) != 1:
                raise ValueError("Configured GenerateTopology requires exactly one run configuration input")
            matched = match_wrapper_configuration(request.method, configuration, configuration_capabilities())
            # Instance settings deliberately override upstream routing defaults.
            # Preserve supplies/loads and never mutate the upstream artifact.
            config.update(deepcopy(configuration["parameters"]))
        supplies = config.get("supplies", [])
        loads = config.get("loads", [])
        if not supplies or not loads:
            raise ValueError("run_config must contain at least one supply and load")

        manager.update_progress(task_id, 45)
        infra = Infrastructure.load(DEFAULT_TOPOLOGY)
        arguments = {
            "allow_hx": bool(config.get("allow_hx", True)),
            "allow_bypass": bool(config.get("allow_bypass", True)),
            "max_results": (config["how_many"] if configuration is not None and "how_many" in configuration["parameters"]
                            else int(config.get("how_many") or 20)),
        }
        solutions = enumerate_networks(
            infra,
            supplies,
            loads,
            **arguments,
        )
        if not solutions:
            raise ValueError("no feasible topology found for the requested supplies and loads")
        topology_data = [solution.to_dict() for solution in solutions]
        time.sleep(0.05)  # stands in for real network construction
        manager.update_progress(task_id, 90)

        best_network = None
        ranked_networks = []
        if topology_data:
            ranked_networks = sorted(
                topology_data,
                key=lambda network: (
                    len(network.get("pipes", {})),
                    network.get("pipe_m", float("inf")),
                    network.get("signature", ""),
                ),
            )
            best_network = ranked_networks[0]

        def network_metrics(network: dict | None) -> dict | None:
            if not network:
                return None
            return {
                "signature": network.get("signature"),
                "pipe_count": len(network.get("pipes", {})),
                "pipe_m": network.get("pipe_m"),
                "trench_m": network.get("trench_m"),
            }

        payload = {
            "run_config": config,
            "config_report": assistant_output.get("report", {}),
            "topology_count": len(solutions),
            "networks": topology_data,
            "report": {
                "summary": (
                    f"Generated {len(solutions)} feasible topology option(s) for the supplied "
                    f"run configuration."
                ),
                "recommended_network": best_network.get("signature") if best_network else None,
                "reason": "lowest pipe count and shortest practical path among the feasible options",
                "selection": {
                    "candidate_count": len(ranked_networks),
                    "recommended": network_metrics(best_network),
                    "next_best": network_metrics(ranked_networks[1] if len(ranked_networks) > 1 else None),
                    "criteria": ["fewest pipes", "shortest total pipe length", "stable signature tie-break"],
                },
                "user_confirmation_required": True,
            },
            "recommended_network": best_network,
            "generated_by": "dh_network_generator",
        }

        if configuration is not None:
            consumed = {name: arguments["max_results" if name == "how_many" else name]
                        for name in configuration["parameters"]}
            payload["execution"] = {
                "schema_version": "1.0", "workflow_id": request.workflow_id, "task_id": request.task_id,
                "service_task_id": task_id,
                "instance_id": configuration["instance_id"], "operation_name": request.method,
                "configuration": deepcopy(configuration), "algorithm_consumed": consumed,
                "algorithm_arguments": arguments,
                "parameter_contract_sha256": matched["parameter_contract_sha256"],
                "input_sha256": hashlib.sha256(json.dumps(assistant_output, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
                "parameter_sources": {name: "instance_configuration" if name in consumed else
                                      "upstream_run_config" if name in assistant_output["run_config"] else "legacy_handler_default"
                                      for name in ("allow_hx", "allow_bypass", "how_many")},
                "instrumentation": "Arguments passed to enumerate_networks in this task; provider/runtime instrumentation, not scientific qualification",
            }

        logger.info("Built %d topology/topologies for %d supply and %d load(s)",
                len(solutions), len(supplies), len(loads))
        manager.complete_task(
            task_id,
            (write_output(DATA_DIR, request.workflow_id, request.task_id, payload) if configuration is not None
             else publish_output(request.workflow_id, task_id, payload)),
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("GenerateTopology failed")
        manager.fail_task(task_id, str(exc))


if __name__ == "__main__":
    run(sys.modules[__name__])
