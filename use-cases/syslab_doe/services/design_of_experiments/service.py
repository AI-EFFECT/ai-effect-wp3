"""Design of Experiments service - turns a DoE request into experiment_design.json.

The sampling logic lives in doe.py; this file only adapts it to the orchestrator control interface.

DesignExperiment
    input:  one DataReference holding the DoE request (see doe.py and requests/doe_request_template.yaml)
            - format "json", or "binary" for YAML text (the orchestrator has no yaml format; "yaml" is accepted
              too, for calls that don't go through it). The text is parsed as JSON, then as YAML.
            - protocol "inline": base64-encoded text, as passed in a workflow's start inputs
            - protocol "file":   path to a file on the shared volume
            - no inputs at all:  the default request file, DOE_DEFAULT_REQUEST
    output: file DataReference to output/<workflow_id>/experiment_design_<experiment_id>.json
"""

import base64
import logging
import os
import sys
from pathlib import Path

import doe
from common.sequential import DataReference, ExecuteRequest, ExecuteResponse, run

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "output"))
# Used when a workflow is started without inputs.
DEFAULT_REQUEST = Path(os.environ.get("DOE_DEFAULT_REQUEST") or "requests/oat.yaml")


def _load_doe_request(inputs: list[dict]):
    """Read the DoE request from the first JSON or YAML input (inline or file), or the default file."""
    if not inputs:
        logger.info(f"No inputs, using the default request {DEFAULT_REQUEST}")
        return doe.parse_request_text(DEFAULT_REQUEST.read_text(encoding="utf-8"))
    for inp in inputs:
        if inp.get("format") not in ("json", "yaml", "binary"):
            continue
        if inp.get("protocol") == "inline":
            return doe.parse_request_text(base64.b64decode(inp.get("uri", "")).decode("utf-8"))
        if inp.get("protocol") == "file":
            return doe.parse_request_text(Path(inp.get("uri", "")).read_text(encoding="utf-8"))
    raise ValueError("No DoE request in the inputs: expected a JSON or YAML input with protocol 'inline' or 'file'")


def execute_DesignExperiment(request: ExecuteRequest) -> ExecuteResponse:
    doe_request = _load_doe_request(request.inputs)

    # One folder per workflow, named after the workflow_id.
    result = doe.run(doe_request, OUTPUT_DIR / Path(request.workflow_id).name)

    logger.info(
        f"DesignExperiment: {result['metadata']['n_runs']} runs "
        f"({result['metadata']['doe_type']}) written to {result['uri']}"
    )

    return ExecuteResponse(
        status="complete",
        output=DataReference(protocol=result["protocol"], uri=result["uri"], format=result["format"]),
    )


if __name__ == "__main__":
    run(sys.modules[__name__])
