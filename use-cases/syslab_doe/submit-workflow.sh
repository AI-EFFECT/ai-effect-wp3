#!/bin/bash
set -e
cd "$(dirname "$0")"

# Submits the workflow design-of-experiments.DesignExperiment -> synthetic-runner.RunExperiments
# -> kpi-calculator.CalculateKpis -> doe-analysis.AnalyzeResults and waits for it.
#
# The DoE request (a JSON or YAML file) becomes the workflow's start input as
# base64-encoded inline text, the same way portugal-node-integrated passes its config.
# Each node hands the next a file reference; on the host the files are under
# output/<workflow_id>/: the design, the runs (a manifest and one time series per run in runs/),
# the results (the target metrics of every run), the analysis report, the summary CSV and the
# figures in analysis/.
#
#   ./submit-workflow.sh                       # requests/oat.yaml
#   ./submit-workflow.sh requests/lhs.yaml
#   ./submit-workflow.sh requests/doe_request_template.yaml

API_URL="${API_URL:-http://localhost:18000}"
REQUEST_FILE="${1:-requests/oat.yaml}"
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"

echo "Submitting SYSLAB DoE workflow to $API_URL"
echo "  DoE request: $REQUEST_FILE"

# Build + submit + wait using python (no jq dependency).
"$PYTHON" - "$API_URL" "$REQUEST_FILE" "${SERVICE_API_KEY:-}" <<'PY'
import base64, json, pathlib, sys, time, urllib.error, urllib.request

api_url, request_file, service_key = sys.argv[1:4]

doe_request = pathlib.Path(request_file).read_bytes()
is_yaml = request_file.lower().endswith((".yaml", ".yml"))
if not is_yaml:
    json.loads(doe_request)  # fail here, not in the service, if the file isn't valid JSON (a YAML file is parsed by the service)

inputs = [{"protocol": "inline", "uri": base64.b64encode(doe_request).decode(), "format": "binary" if is_yaml else "json"}]  # the orchestrator has no yaml format

body = {
    "blueprint": json.loads(pathlib.Path("export/blueprint.json").read_text()),
    "dockerinfo": json.loads(pathlib.Path("export/dockerinfo.json").read_text()),
    "inputs": inputs,
}
if service_key:
    body["services_api_key"] = service_key
    print("  (forwarding SERVICE_API_KEY as services_api_key)")


def call(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(f"{api_url}{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


try:
    wid = call("/workflows", body)["workflow_id"]
except urllib.error.HTTPError as e:
    sys.exit(f"submit failed: {e.read().decode()}")
except urllib.error.URLError as e:
    sys.exit(f"cannot reach the orchestrator at {api_url}: {e.reason}")
print(f"\nWorkflow submitted!  ID: {wid}")

status = "running"
for _ in range(300):
    status = call(f"/workflows/{wid}")["status"]
    if status in ("completed", "failed"):
        break
    time.sleep(1)
print(f"Workflow status: {status}")

# The file each node reported, by method, e.g. files["DesignExperiment"] = output/<workflow_id>/experiment_design_<id>.json
files = {}
for task in call(f"/workflows/{wid}/tasks")["tasks"]:
    print(f"  {task['node_key']}: {task['status']}")
    if task.get("error"):
        print(f"    error: {task['error']}")
    for ref in task.get("output_refs", []):
        print(f"    output: {ref['protocol']}://{ref['uri']} ({ref['format']})")
        files[task["node_key"].split(":")[-1]] = pathlib.Path("output") / wid / ref["uri"].rsplit("/", 1)[-1]


def read(method):
    path = files.get(method)
    if status == "completed" and path and path.exists():
        return path, json.loads(path.read_text(encoding="utf-8"))
    return None, None


design_path, design = read("DesignExperiment")
if design:
    print(f"\n{design_path}: {design['doe_type']}, {len(design['factors'])} factors, {design['n_runs']} runs")
    print(f"  scenario {design.get('scenario_name')}, target {design.get('target')}")
    for note in design["notes"]:
        print(f"  note: {note}")

runs_path, runs = read("RunExperiments")
if runs:
    source = runs["source"]
    print(f"\n{runs_path}: {len(runs['runs'])} runs, one time series each in {runs_path.parent / 'runs'}")
    if source["kind"] == "synthetic":
        print(f"  SYNTHETIC ({source['note']}); the signals are built so that")
        for signal, model in source["signal_models"].items():
            print(f"    {signal}: {model}")

results_path, results = read("CalculateKpis")
if results:
    print(f"\n{results_path}: {len(results['results'])} runs, target metrics from the time series")
    for metric, kpi in results["kpis"].items():
        divisor = f" / {kpi['divide_by']}" if "divide_by" in kpi else ""
        print(f"  {metric}: {kpi['statistic']} of {kpi['signal']}{divisor} [{kpi.get('unit', '')}]")
    for note in results["notes"]:
        print(f"  note: {note}")

report_path, report = read("AnalyzeResults")
if report:
    print(f"\n{report_path}: {report['analysis']} analysis of {report['n_runs']} runs")
    for line in report["summary"]:
        print(f"  {line}")
    for note in report["notes"]:
        print(f"  note: {note}")
    print(f"  figures in {report_path.parent / 'analysis'}: " + ", ".join(pathlib.Path(f).name for f in report["figures"]))

print("\nCheck status:")
print(f"  curl -s {api_url}/workflows/{wid}/tasks | python -m json.tool")
sys.exit(0 if status == "completed" else 1)
PY
