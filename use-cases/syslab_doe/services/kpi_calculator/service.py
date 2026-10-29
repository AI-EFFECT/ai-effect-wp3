"""KPI calculator service - the target metrics of every run, computed from the run's time series.

The calculation lives in kpi.py; this file only adapts it to the orchestrator control interface.

CalculateKpis
    input:  file DataReference to experiment_runs.json (the output of RunExperiments): the manifest that
            links every run_id to its time series
    output: file DataReference to output/<workflow_id>/experiment_results_<experiment_id>.json
            (the study definition plus the target metrics of every run, which AnalyzeResults reads)
"""

import logging
import os
import sys
from pathlib import Path

import kpi
from common.sequential import DataReference, ExecuteRequest, ExecuteResponse, run

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "output"))
KPI_CATALOGUE = os.environ.get("KPI_CATALOGUE") or None   # default: kpi_catalogue.yaml next to kpi.py


def _manifest_path(inputs: list[dict]) -> Path:
    """The run manifest: the first JSON input with protocol 'file'."""
    for inp in inputs:
        if inp.get("format") == "json" and inp.get("protocol") == "file":
            return Path(inp.get("uri", ""))
    raise ValueError("No run manifest in the inputs: expected a JSON input with protocol 'file'")


def execute_CalculateKpis(request: ExecuteRequest) -> ExecuteResponse:
    manifest_path = _manifest_path(request.inputs)

    # One folder per workflow, named after the workflow_id.
    path = kpi.run(manifest_path, OUTPUT_DIR / Path(request.workflow_id).name, KPI_CATALOGUE)

    logger.info(f"CalculateKpis: metrics for {manifest_path.name} written to {path}")

    return ExecuteResponse(status="complete", output=DataReference(protocol="file", uri=str(path.resolve()), format="json"))


if __name__ == "__main__":
    run(sys.modules[__name__])
