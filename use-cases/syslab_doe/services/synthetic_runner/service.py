"""Synthetic runner service - stand-in for the experiment runner and simulator until they can run a design.

THIS IS NOT A SIMULATION: the time series are made-up functions of the factor values (see
synthetic_runner.py), and the manifest says source.kind "synthetic". The logic lives in
synthetic_runner.py; this file only adapts it to the orchestrator control interface.

RunExperiments
    input:  file DataReference to experiment_design.json (the output of DesignExperiment)
    output: file DataReference to output/<workflow_id>/experiment_runs_<experiment_id>.json, the manifest
            that links every run_id to its time series (output/<workflow_id>/runs/<run_id>/timeseries.csv)
"""

import logging
import os
import sys
from pathlib import Path

import synthetic_runner
from common.sequential import DataReference, ExecuteRequest, ExecuteResponse, run

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "output"))


def _design_path(inputs: list[dict]) -> Path:
    """The experiment design: the first JSON input with protocol 'file'."""
    for inp in inputs:
        if inp.get("format") == "json" and inp.get("protocol") == "file":
            return Path(inp.get("uri", ""))
    raise ValueError("No experiment design in the inputs: expected a JSON input with protocol 'file'")


def execute_RunExperiments(request: ExecuteRequest) -> ExecuteResponse:
    design_path = _design_path(request.inputs)

    # One folder per workflow, named after the workflow_id.
    path = synthetic_runner.run(design_path, OUTPUT_DIR / Path(request.workflow_id).name)

    logger.info(f"RunExperiments: synthetic time series for {design_path.name} written under {path.parent}, manifest {path.name}")

    return ExecuteResponse(status="complete", output=DataReference(protocol="file", uri=str(path.resolve()), format="json"))


if __name__ == "__main__":
    run(sys.modules[__name__])
