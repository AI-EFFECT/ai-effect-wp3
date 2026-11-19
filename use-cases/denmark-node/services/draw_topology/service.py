"""Draw Topology service for the Danish Node workflow.

Pipeline role: final visualization step after topology generation.
Operation exposed to the orchestrator: RenderTopologies.
"""

import base64
import json
import logging
import os
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from draw_network import NetworkDrawing
from common.configuration import output_path, load_contract
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
    # Published I/O only; no invented adjustable parameters for this renderer.
    return load_contract(os.environ.get('SERVICE_PARAMETER_CONTRACT_FILE'),'RenderTopologies',{})


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
    with output_path.open('x',encoding='utf-8') as stream:
        json.dump(payload,stream,indent=2,allow_nan=False)
    return {"protocol": "file", "uri": str(output_path), "format": "json"}


def execute_RenderTopologies(request: ExecuteRequest) -> ExecuteResponse:
    """Render SVG drawings for each generated network candidate."""
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',value) for value in (request.workflow_id,request.task_id)):
        from fastapi import HTTPException
        raise HTTPException(422,'Safe workflow and task identities are required')
    output_path(DATA_DIR,request.workflow_id,request.task_id)
    if any((DATA_DIR/request.workflow_id).glob(request.task_id+'_network_*.svg')):
        from fastapi import HTTPException
        raise HTTPException(409,'Existing task artifacts must be preserved; reconcile before retry')
    task_manager.register_task(request.task_id, request)
    run_in_background(request.task_id, _render_topologies, request)
    return ExecuteResponse(status="running", task_id=request.task_id)


def _render_topologies(
    task_id: str,
    request: ExecuteRequest,
    manager: TaskManager,
) -> None:
    """Background worker: render all topology candidate drawings to SVG."""
    try:
        topology_output = resolve_input(request.inputs, default={}) or {}
        if not topology_output or "networks" not in topology_output:
            raise ValueError(
                "RenderTopologies expects topology_generator output "
                "(a JSON object containing 'networks')."
            )

        networks = topology_output.get("networks", [])
        if not networks:
            raise ValueError("topology output contains no network candidates to render")

        manager.update_progress(task_id, 20)
        data_dir = DATA_DIR / request.workflow_id
        data_dir.mkdir(parents=True, exist_ok=True)

        # Observe the actual processing boundary, independently of producer metadata.
        canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        input_digest = hashlib.sha256(canonical(topology_output)).hexdigest()
        processed = []
        drawings = []
        for index, network in enumerate(networks, 1):
            network_digest = hashlib.sha256(canonical(network)).hexdigest()
            drawing = NetworkDrawing(network, index, len(networks)).render()
            processed.append(network_digest)  # Only a completed render counts.
            drawing_path = data_dir / f"{task_id}_network_{index:03d}.svg"
            with drawing_path.open('x',encoding='utf-8') as stream:stream.write(drawing)
            drawings.append(
                {
                    "network_index": index,
                    "signature": network.get("signature"),
                    "protocol": "file",
                    "uri": str(drawing_path),
                    "format": "svg",
                }
            )

        manager.update_progress(task_id, 80)
        payload = {
            "run_config": topology_output.get("run_config", {}),
            "config_report": topology_output.get("config_report", {}),
            "topology_count": len(networks),
            "drawing_count": len(drawings),
            "drawings": drawings,
            "recommended_network": topology_output.get("recommended_network"),
            "report": {
                "summary": (
                    f"Rendered {len(drawings)} topology drawing(s) for review and confirmation."
                ),
                "recommended_network": topology_output.get("recommended_network", {}).get("signature"),
                "reason": topology_output.get("report", {}).get("reason"),
                "selection": topology_output.get("report", {}).get("selection"),
                "user_confirmation_required": True,
            },
            "generated_by": "draw_topology",
        }
        payload['artifact_manifest']={'schema_version':'1.0','kind':'task-artifacts-v1',
            'workflow_id':request.workflow_id,'task_id':request.task_id,'files':[
                {**reference,'sha256':hashlib.sha256(Path(reference['uri']).read_bytes()).hexdigest(),
                    'bytes':Path(reference['uri']).stat().st_size}
                for reference in drawings]}
        payload['input_consumption'] = {
            'schema_version': '1.0', 'kind': 'json-networks-render-v1',
            'workflow_id': request.workflow_id, 'task_id': request.task_id,
            'operation_name': request.method, 'input_sha256': input_digest,
            'networks_sha256': hashlib.sha256(canonical(networks)).hexdigest(),
            'processed_network_sha256': processed, 'processed_network_count': len(processed),
            'instrumentation': 'Parsed input and each network passed to NetworkDrawing.render in this task; completed calls only; software evidence, not scientific approval',
        }
        if "execution" in topology_output:
            # Keep producer settings attached to the visual result as well.
            payload["upstream_execution"] = topology_output["execution"]

        manager.complete_task(
            task_id,
            publish_output(request.workflow_id, task_id, payload),
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("RenderTopologies failed")
        manager.fail_task(task_id, str(exc))


if __name__ == "__main__":
    run(sys.modules[__name__])
