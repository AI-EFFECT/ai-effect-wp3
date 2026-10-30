"""DoE analysis service - which factors drive which metrics?

The analysis lives in analysis.py; this file only adapts it to the orchestrator control interface.

AnalyzeResults
    input:  file DataReference to experiment_results.json: the study definition plus one result per run
    output: file DataReference to output/<workflow_id>/analysis_report_<experiment_id>.json
            next to it: analysis_summary.csv and the figures in analysis/ (PNG)
"""

import logging
import os
import sys
from pathlib import Path

import analysis
from common.sequential import DataReference, ExecuteRequest, ExecuteResponse, run

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "output"))


def _results_path(inputs: list[dict]) -> Path:
    """The experiment results: the first JSON input with protocol 'file'."""
    for inp in inputs:
        if inp.get("format") == "json" and inp.get("protocol") == "file":
            return Path(inp.get("uri", ""))
    raise ValueError("No experiment results in the inputs: expected a JSON input with protocol 'file'")


def execute_AnalyzeResults(request: ExecuteRequest) -> ExecuteResponse:
    results_path = _results_path(request.inputs)

    # One folder per workflow, named after the workflow_id.
    report = analysis.run(results_path, OUTPUT_DIR / Path(request.workflow_id).name)

    logger.info(f"AnalyzeResults: {results_path.name} analysed, report at {report}")

    return ExecuteResponse(status="complete", output=DataReference(protocol="file", uri=str(report.resolve()), format="json"))


if __name__ == "__main__":
    run(sys.modules[__name__])
