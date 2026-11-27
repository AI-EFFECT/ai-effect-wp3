# syslab_doe

The SYSLAB workflow: **design-of-experiments** decides which runs to do,
**synthetic-runner** stands in for the experiment runner and simulator and logs a time series
per run, **kpi-calculator** turns those time series into the target metrics, and
**doe-analysis** works out which factors drive which metrics and reports it back.

```
  requests/oat.yaml ──(inline JSON or YAML start input)──► design-of-experiments
                                                    DesignExperiment
                                                          │
                                  (file: experiment_design_<experiment_id>.json)
                                                          ▼
                                                    synthetic-runner
                                                     RunExperiments      ◄── STAND-IN: made-up time series, not a simulation
                                                          │
                       (file: experiment_runs_<experiment_id>.json, the manifest; runs/<run_id>/timeseries.csv)
                                                          ▼
                                                    kpi-calculator
                                                      CalculateKpis      ◄── the target_metrics, from the time series
                                                          │
                                  (file: experiment_results_<experiment_id>.json)
                                                          ▼
                                                      doe-analysis
                                                     AnalyzeResults
                                                          │
                       (file: analysis_report_<experiment_id>.json + analysis_summary.csv + analysis/*.png)
```

All files land in `output/<workflow_id>/`. The simulator and the lab test will replace
the stand-in when they exist; the KPI calculator and the analysis don't change, because they
only depend on the [run data](#the-run-data-and-the-kpis) and the
[results file](#the-results-file-the-contract-between-the-kpis-and-analysis).

The sampling works exactly like the original ERIGrid toolbox (see [Credits](#credits)): the sampling
functions are unchanged, with the same libraries and versions. In a comparison with the original script (12
requests over all the methods, same seed) the recipes and run IDs were identical; that comparison needs the
original toolbox and is not part of this repo. The request format is described [below](#the-request-format).

**The synthetic time series are not a simulation.** Every signal is a made-up function of the
run's factor values, chosen so the right answer is known and the analysis can be checked
against it. The manifest says `"kind": "synthetic"` and lists the formulas.

## Run it

```bash
# from the repo root: shared network + orchestrator + this use case
./start.sh syslab_doe

# submit a workflow and wait for the result (default request: requests/oat.yaml)
cd use-cases/syslab_doe
./submit-workflow.sh
./submit-workflow.sh requests/lhs.yaml

# stop everything
cd ../.. && ./stop.sh syslab_doe
```

`submit-workflow.sh` needs Python 3. It prints the task status and the output reference of each
node, then the design, the runs, the KPI definitions and the analysis summary in words; the
files are in `output/<workflow_id>/`:

| File | What |
|---|---|
| `experiment_design_<experiment_id>.json` | the runs (the study definition) |
| `experiment_runs_<experiment_id>.json` | the manifest: which time series belongs to which run |
| `runs/<run_id>/timeseries.csv` | the logged signals of one run, one row per time step |
| `experiment_results_<experiment_id>.json` | the study definition plus the target metrics of every run |
| `analysis_report_<experiment_id>.json` | the findings: `summary` (in words), `results` (the numbers), `figures`, `notes` |
| `analysis_summary.csv` | the numbers of the report as a table |
| `analysis/*.png` | the figures |

Wait a few seconds after `./start.sh` before submitting: the services need a moment to come up.
The first build of `doe-analysis` takes a few minutes (scipy, scikit-learn, matplotlib).

`distribution_and_discrete` designs stop at the analysis node, with an error saying so: the
original toolbox has no analysis for that method either.

## The example requests

Each file in `requests/` is a DoE request: the input of the workflow, in the layout of
`requests/doe_request_template.yaml` (see "The request format" below). They share the template's baseline and
vary only variables that baseline lists. The ranges are example values; the service accepts any names.

| File | `doe_type` | Factors | Runs |
|---|---|---|---|
| `oat.yaml` | `OAT` | `q_demand_kw.scale` 0.75–1.25, `t_supply_setpoint_c.final` 70–90, `pipe_u_w_per_mk` 0.2–0.4, `t_ambient_c` 0–20; the default | 9 |
| `extreme_points.yaml` | `extreme_points` | the same four | 16 |
| `sobol.yaml` | `sobol` | `q_demand_kw.scale` and `pipe_length_km` 0.25–0.35 (null in the baseline, so a factor sets it); 12 samples rounded up to 16, plus the 4 corners | 20 |
| `lhs.yaml` | `LHS` | three normal distributions: `q_demand_kw.scale` (1.0 ± 0.1), `t_ambient_c` (10 ± 4), `pipe_u_w_per_mk` (0.30 ± 0.04); seed 42 | 10 |
| `sobol_indices.yaml` | `sobol_indices` | the four ranges, 16 samples, seed 42 | 96 |
| `fast.yaml` | `fast` | the four ranges, 65 samples, seed 42 | 260 |
| `distribution_and_discrete.yaml` | `distribution_and_discrete` | `q_demand_kw.scale` (range), `pump_lift_bar` (normal, 1.5 ± 0.1), `pipe_sections` (one of 20, 30, 40); 5 samples | 15 |

(Factor names above are shortened: `q_demand_kw.scale` is `input_timeseries.q_demand_kw.scale`, and so on.)

The design file is named after the request's `experiment_id`
(`experiment_design_demo-sobol.json` for `sobol.yaml`).

Every request has the same `target_metrics`, taken from the template: `min_return_temp_c`, `heat_loss_kwh` and
`max_mass_flow_kg_s`, three entries of the [KPI catalogue](#the-run-data-and-the-kpis). They are placeholders until
the real KPIs are decided.

The examples are generated from the template's baseline, so they carry whatever it lists. If the template's baseline
changes, they have to be regenerated.

To use your own, copy one, change the factors and pass its path to
`submit-workflow.sh`. No rebuild is needed: the folder is mounted into the container.

## Plugging in the simulator or the lab test

Only the runner changes; the KPI calculator and the analysis already work on its output.

- It receives `experiment_design_<id>.json`. Every run has a `recipe`: the complete parameter set of one execution, in
  the groups of the template (`runner_parameters`, `input_timeseries`, `controller_parameters`,
  `physical_model_parameters`) plus `scenario_name`, `target` and the run's `ID`. The DoE passes the groups on without
  interpreting them (see [the request format](#the-request-format)); what each value means is the runner's business.
- It executes every recipe and writes the [run data](#the-run-data-and-the-kpis): one time series per run and the
  manifest that links them to the `run_id`s. The manifest is its output.
- The KPI catalogue then has to name the columns it logs.

None of the existing simulator or runner code reads recipes yet: the stand-in in this use case only uses
`runner_parameters` for its time axis.

## The request format

`requests/doe_request_template.yaml` is the current request layout. The DoE accepts JSON or YAML.

```yaml
experiment_id: template-sobol
scenario_name: template_sobol
target: simulation              # where the experiment runs: simulation or syslab

doe_conf:                       # the DoE settings
  doe_type: sobol
  samples: 16
  seed: 42
  add_extreme_points: false

entities_parameters:            # the baseline: every variable that can be changed, in groups of any depth
  input_timeseries: ...         # time-controlled variables, keyed by the variable they drive
  controller_parameters: ...
  physical_model_parameters:
    pipe_length_km: null        # null: not set (taken from the topology)
    topology: {reference: topologies/example.json}

variations_dict:                # mirrors the paths above, to any depth
  input_timeseries:
    q_demand_kw: {scale: [0.75, 1.25]}
  physical_model_parameters:
    pipe_length_km: [0.25, 0.35]

target_metrics: [min_return_temp_c, heat_loss_kwh, max_mass_flow_kg_s]
```

**What the DoE does with it**
- `experiment_id`, `scenario_name`, `target`: carried into the design header, and `scenario_name` and `target`
  into every recipe.
- `doe_conf`: the settings the sampling methods read. The old top-level keys (`doe_type`, `samples`, ...) still
  work, so the original toolbox configs are valid requests.
- `entities_parameters`: copied into every recipe and **not interpreted**. Time series definitions, the topology
  reference, controller and plant parameters are all passed on as they are.
- `variations_dict`: every leaf is a factor: a `[min, max]` range, or a `{type: norm, ...}` or `{type: discrete, ...}`
  spec. A factor is named by its full path (`input_timeseries.q_demand_kw.scale`). **Every path must exist in
  `entities_parameters`, with a number or `null`**, otherwise the request is refused with an error naming the path.
  A value that is a list or text cannot be varied.
- A `null` in the baseline means "not set". A factor on it replaces it, and a baseline run (OAT) leaves it null.
- The sampling itself is unchanged from the original toolbox.

**Time series.** An entry under `input_timeseries` is keyed by the variable it drives and has a `type` (`step` or
`timeseries`), the data for that type, and `scale` and `offset` (value(t) = scale x signal(t) + offset). The DoE only
varies the numeric fields (`final`, `time`, `scale`, ...). Building the signal, reading a `reference` file and deciding
which model input a series drives are for the runner or simulator.

**Topology.** It sits at `physical_model_parameters.topology` and is passed on untouched. The DoE neither reads nor
generates it; `topologies/example.json`, the file the template names, is an example of what the topology
generator produces. The pipe scalars that a topology also describes (`pipe_length_km`, `pipe_inner_diameter_mm`) default to
`null`, meaning "from the topology". A value, or a factor, overrides it. Applying that rule is the runner's job.

**Controller.** `controller_parameters` says which controller the runner builds and with what. The template's example
is the experiment runner's `SequenceController` (its `config.yaml`: `controller_type: sequence`, `sequence_setpoints`,
`sequence_step_duration`):
```yaml
controller_parameters:
  type: sequence                           # which controller: fixed text, not sampled
  sequence_setpoints: [40.0, 60.0, 90.0]   # a list, so it is not varied directly
  sequence_step_duration: 1800             # a number: can be a factor
```
The recipe never holds controller code. The runner is expected to keep the controller classes and a register from `type`
to class, build the one the recipe names and give it the parameters. The DoE only passes `controller_parameters`
on, and can vary its numbers like any other.

**Not done yet**
- Limits per target (simulation or SYSLAB), and checks of the sampled values against them.
- The simulator does not use the topology, the time series definitions or the new group names yet.
- The runner does not read recipes yet. The controller section above shows what a recipe would hand it.

## Analysis details

- **MANOVA.** For `extreme_points` with more than one metric, the report also has the MANOVA of the original
  toolbox (statsmodels), as rows with `metric: all`: four test statistics per factor (Wilks' lambda, Pillai's trace,
  Hotelling-Lawley trace, Roy's greatest root) with F and p, and one summary line. Metrics that do not vary are left
  out, and if the test cannot be computed (too few runs) a note says so and the rest of the analysis is unaffected.
- **Two meta-models for sobol and LHS.** The default is the richer fit (all factors, scaled inputs, R², cross-validation).
  `analysis.analyze(..., meta_model="original")` runs the original toolbox's fit instead: the first two factors only,
  on raw values, with its kernel, 9 restarts and a 50 x 50 surface. Nothing in the workflow selects it yet; it is a
  function argument. The original has no seed, so its fit changes slightly between calls.

## The run data and the KPIs

The runner (the stand-in now, the simulator or the lab test later) produces **time series**, not
metrics. The KPI calculator turns them into the metrics the study asked for. The two meet in one
folder, `output/<workflow_id>/`:

```
experiment_design_<id>.json       the DoE node's output (the runs and their recipes)
experiment_runs_<id>.json         the runner's output: the manifest
runs/run_00/timeseries.csv        one log per run, named after the run_id
runs/run_01/timeseries.csv
...
experiment_results_<id>.json      the KPI node's output: the target metrics of every run
```

The manifest links the data to the run ids, with paths relative to itself, so the folder can be
moved or archived as it is:

```json
{
  "source": {"kind": "synthetic", "note": "...", "signal_models": {"return_temp_c": "..."}},
  "study_definition": { "...": "experiment_design.json, unchanged" },
  "runs": [
    {"run_id": "run_00", "execution_id": "3f9c2a71b8de", "status": "ok", "error": null,
     "timeseries": {"uri": "runs/run_00/timeseries.csv", "format": "csv", "time_column": "time_s",
                    "signals": ["return_temp_c", "heat_loss_kw", "mass_flow_kg_s"], "n_rows": 1201}}
  ]
}
```

- `timeseries.csv` has a time column in seconds from the start of the run, then one column per logged
  signal, as the real runner's logger writes one row per tick. The stand-in's time axis is the recipe's
  `runner_parameters` (`dt_s`, `sim_duration_h`).
- A run that did not finish has `"status": "failed"`, an `error` and no `timeseries`.
- `execution_id` identifies one execution of a run (a rerun gets a new one).
- The KPI calculator resolves `uri` against the manifest's folder (an absolute path also works), so a
  real runner can write the same manifest around its own logs. The CSV is the only format so far.

**The KPI calculator** reads `target_metrics` from the study definition, looks each one up in
[`services/kpi_calculator/kpi_catalogue.yaml`](services/kpi_calculator/kpi_catalogue.yaml), and applies
its statistic to its signal, once per run:

```yaml
heat_loss_kwh:
  signal: heat_loss_kw       # a column of timeseries.csv
  statistic: integral        # min, max, mean, std, first, last or integral (trapezoid over time_s)
  divide_by: 3600            # kW x s -> kWh
  unit: kWh
```

- The catalogue is the set of metrics a study can select from. A name that is not in it is an error that
  lists the ones that are, not a guess. A study with no `target_metrics` is refused too.
- A run that cannot be evaluated (the runner reported it failed, the file is missing, a signal column is
  missing, the time column does not increase, or a signal has missing samples) gets `"status": "failed"` and
  the reason in `error`; the other runs are still evaluated, and the analysis then refuses the study.
  Missing samples are not skipped, because a gap in a log is something to know about; whether to
  interpolate or ignore them is a decision for the real data.
- **The catalogue is a placeholder.** Its signal names are those of the synthetic runner. The real
  simulator and the lab test log their own columns, and the KPIs that matter for the use case are not
  decided; when they are, the entries change, not the code. `KPI_CATALOGUE` (an environment variable of the
  service) points it at another file.

## The results file: the contract between the KPIs and analysis

The analysis reads one file, `experiment_results_<experiment_id>.json`: the study definition
(the design file, unchanged) and one result per run. The KPI calculator writes it, whatever produced
the time series, and the analysis doesn't change.

```json
{
  "source": {"kind": "synthetic", "...": "from the manifest"},
  "kpis": {"heat_loss_kwh": {"signal": "heat_loss_kw", "statistic": "integral", "divide_by": 3600, "unit": "kWh"}},
  "study_definition": { "...": "experiment_design.json, unchanged" },
  "results": [
    {"run_id": "run_00", "execution_id": "3f9c2a71b8de", "status": "ok",
     "metrics": {"min_return_temp_c": 44.8, "heat_loss_kwh": 12.1, "max_mass_flow_kg_s": 1.3}}
  ],
  "notes": []
}
```

- Results are matched to the design's runs by `run_id`, so their order doesn't matter.
- The metrics analysed are the design's `target_metrics`, or every numeric metric of the results if
  it names none. Every run needs `status: "ok"` and a number for each of them.
- `source` and `kpis` say where the numbers came from and how they were computed; the analysis ignores them.

**What the stand-in builds.** Each factor is scaled to `u` in [0, 1] over its range (factor `i`
is the i-th in the design), and the three signals take these models in turn. Each signal is a fixed
profile arranged so that one statistic of it is exactly the model value, so the sensitivity indices are
known, and the tests check that the analysis recovers them:

| Signal | Statistic = the model | Model | Truth |
|---|---|---|---|
| `return_temp_c` | lowest value (`min_return_temp_c`) | sum of (10 / i) `u_i` | additive: no interactions; each factor's share is its weight squared |
| `heat_loss_kw` | integral in kWh (`heat_loss_kwh`) | product of (1 + (4 / i) `u_i`) | increasing in every factor, with interactions |
| `mass_flow_kg_s` | highest value (`max_mass_flow_kg_s`) | 20 (`u_1` − ½)(`u_2` − ½) + 2 `u_3` | `u_1` and `u_2` matter only together (S1 = 0, ST = 0.89) |

The other catalogue entries (a mean, a peak, the final value) work on the same signals but have no
known truth: they depend on the profiles, which are fixed shapes. The signals do not depend on what the
factors are called or mean: `input_timeseries.q_demand_kw.scale` is just the first factor.

## How JSON goes in and out

The same conventions as the other use cases:

| | |
|---|---|
| **In**: the DoE request | the workflow's start input, `{"protocol": "inline", "uri": <base64 text>, "format": "json"}` (like `portugal-node-integrated`). YAML goes as `"format": "binary"`, because the orchestrator has no yaml format; the service parses the text as JSON, then as YAML |
| **In**, alternative | `{"protocol": "file", "uri": "/app/output/.../request.json", "format": "json"}`, for when an upstream node writes the request to the shared volume |
| **In**, no inputs | the service reads its default request file, `DOE_DEFAULT_REQUEST` (default `requests/oat.yaml`) |
| **Out**: the design | `{"protocol": "file", "uri": "/app/output/<workflow_id>/experiment_design_<experiment_id>.json", "format": "json"}` on the shared `./output` volume, one folder per workflow (like `germany-node`) |
| **Between nodes** | each node's output reference is the next node's only input (a `file` reference to the design, then to the run manifest, then to the results); the analysis report is the last output |

The orchestrator only passes these references along. A downstream node mounts
the same `./output:/app/output` volume and reads the file at that path.

`inline` is fine for the request (a few kB). The design is written as a file
because it grows with the number of runs (`fast` with N = 65 and 3 factors
already gives 195 runs).

## Run it from the portal

Build the portal package (also refreshes `export/`; delete `export/` first if you
removed a service, because the generator doesn't clear it):

```bash
python3 scripts/onboarding-export-generator.py use-cases/syslab_doe use-cases/syslab_doe/export --overwrite --zip
```

Upload `use-cases/syslab_doe/export.zip` at https://portal.renewenergy.io under
**Solutions → Import ZIP**. The zip only holds the pipeline graph and the
service address. The portal runs nothing itself: it sends the workflow to an
orchestrator, which calls the service. So before deploying:

1. The service and the orchestrator must be running: `./start.sh syslab_doe`.
2. The portal must be able to reach the orchestrator. For local testing, expose
   it with `ngrok http 18000` and set that URL as the solution's **Orchestrator
   URL** (see `orchestrator/README.md`, "Using with the Portal").

Where the DoE request comes from in a portal run:

- **Nothing entered**: the workflow starts without inputs and the service uses
  its default request, `requests/oat.yaml`. To use another one, set
  `DOE_DEFAULT_REQUEST` (e.g. in a `.env` file next to `docker-compose.yml`:
  `DOE_DEFAULT_REQUEST=requests/lhs.yaml`) and run `docker compose up -d`.
  Edits to the files in `requests/` apply immediately, no restart.
- **An initial input entered at deploy time**: if the portal offers an
  initial-input field (the local launcher does), paste the request JSON there.
  It arrives as the same inline JSON start input that `submit-workflow.sh` sends.

Either way the files land in `output/<workflow_id>/` on the machine running the services. The
orchestrator's API only returns a reference to each node's file, so the portal can show that the
workflow completed but probably not the contents or the figures.

## What's in here

| Path | What |
|---|---|
| `services/design_of_experiments/doe.py` | the sampling logic, from the ERIGrid toolbox (see Credits), and the request format |
| `services/design_of_experiments/service.py` | the orchestrator wrapper: `execute_DesignExperiment` reads the request, calls `doe.run()` |
| `services/design_of_experiments/proto/` | interface description for the portal |
| `services/synthetic_runner/` | the stand-in for the runner and simulator: `synthetic_runner.py` (the time series and the manifest), `service.py` (`execute_RunExperiments`), Dockerfile, proto |
| `services/kpi_calculator/` | `kpi.py` (the calculation), `kpi_catalogue.yaml` (the metrics a study can select from), `service.py` (`execute_CalculateKpis`), Dockerfile, proto, `test_kpi.py` (`pytest`; also covers the runner and the whole chain to the analysis) |
| `services/doe_analysis/` | the analysis: `analysis.py`, `service.py` (`execute_AnalyzeResults`), Dockerfile (adds scikit-learn, matplotlib and statsmodels), proto |
| `services/design_of_experiments/Dockerfile`, `requirements.txt` | `python:3.11-slim` plus the original toolbox's pinned libraries (`pyDOE==0.3.8`, `SALib==1.4.7`, `numpy<2`) and FastAPI |
| `requests/` | the example DoE requests; mounted into the service for the default request |
| `requests/doe_request_template.yaml` | the current request layout, see "The request format" |
| `topologies/example.json` | an example topology: a real output of the topology generator, for the CHP at 310-D to the dumpload at 716-D. The template names it; the DoE passes the reference on and never opens it |
| `services/doe_analysis/test_analysis_additions.py` | tests of the MANOVA and the original meta-model (`pytest`) |
| `LICENSE-ERIGrid-toolbox` | the license of the ERIGrid toolbox the sampling and analysis are based on |
| `services/design_of_experiments/test_request_format.py` | tests of the request format (`pytest`; needs fastapi and PyYAML next to the DoE requirements) |
| `output/` | shared volume, where every node's files land (one folder per workflow). Git-ignored |
| `connections.json` | pipeline topology for the export generator: design-of-experiments → synthetic-runner → kpi-calculator → doe-analysis |
| `export/`, `export.zip` | generated `blueprint.json` / `dockerinfo.json` and the portal package; `submit-workflow.sh` reads `export/`. Regenerate them after changing the services or `connections.json` |
| `docker-compose.yml` | the services on ports 18301 (design), 18302 (synthetic runner), 18303 (analysis) and 18304 (KPI calculator), on the `ai-effect-services` network |

## Changing things

- **A new request**: add a JSON file to `requests/` and pass it to `submit-workflow.sh`.
- **The sampling logic**: edit `services/design_of_experiments/doe.py` and rebuild with `./start.sh`. The sampling
  functions are what make the designs identical to the original toolbox's, so change the request handling around them,
  not them.
- **The analysis**: edit `services/doe_analysis/analysis.py` and rebuild with `./start.sh`.
- **Tests** (`pytest`): `services/design_of_experiments/test_request_format.py`,
  `services/doe_analysis/test_analysis_additions.py` and `services/kpi_calculator/test_kpi.py` (which also runs the
  whole chain from design to analysis). They import the services, so install the requirements of
  `design_of_experiments`, `doe_analysis` and `kpi_calculator` plus `pytest`; each file's docstring has the details.
- **A new metric**: add an entry to `services/kpi_calculator/kpi_catalogue.yaml`, rebuild, and name it in
  `target_metrics`. A new kind of statistic goes in `STATISTICS` in `kpi.py`.
- **Replacing the stand-in with the simulator or the lab test**: the new service takes the place of
  `synthetic-runner` in `connections.json`, and writes the [run data](#the-run-data-and-the-kpis): the manifest
  and one time series per run. Then point the catalogue's `signal` entries at its columns. The KPI calculator
  and the analysis don't change. Delete `export/` and regenerate the export (see above).
- **Adding nodes**: add the service under `services/`, a `connections` entry for
  it, delete `export/` and regenerate the export (see above).

## Credits

The sampling in `services/design_of_experiments/doe.py` and the analysis in
`services/doe_analysis/analysis.py` are based on the ERIGrid 2.0 "Toolbox with DoE and SA
methods", <https://github.com/ERIGrid2/toolbox_doe_sa>, MIT License, Copyright (c) 2023
ERIGrid 2.0; the license text is in [`LICENSE-ERIGrid-toolbox`](LICENSE-ERIGrid-toolbox). The
toolbox in turn uses code from ERIGrid/NA4-SummerSchool-DTU-2018 v2.0
(<https://zenodo.org/record/2837928>), Copyright (c) 2019 Tue Vissing Jensen, on behalf of the
Technical University of Denmark. The toolbox was developed within the ERIGrid 2.0 project
(H2020, Grant Agreement No. 870620).

## Known limits

- The template's `DataReference` (`use-cases/common/sequential.py`) only has
  `protocol`, `uri` and `format`. The size, checksum and metadata (`doe_type`,
  `n_runs`) that `doe.run()` returns are therefore dropped at the service
  boundary, even though the orchestrator itself supports `metadata`.
- Seeded random designs are the same on every run with the same library versions
  (the image pins the original's). Between Windows and the Linux container, `fast`
  can differ in the last digit (about 1e-16 relative) because the platforms round
  sines differently; the other methods are identical.
- Some inputs are refused where the original would crash or give wrong values:
  `LHS` takes normal factors only, and `distribution_and_discrete` needs exactly
  one discrete factor with 3 integer values.
- A design needs every run to have finished: one failed run stops the analysis with an error
  listing the failed runs.
- The analysis differs from the original `toolbox_analysis.py` in a few ways: the bootstrap seed is fixed
  (the original's confidence intervals change on every call), results are matched to runs by `run_id`, a metric
  that does not vary gets null indices, and the default meta-model for sobol and LHS is a richer fit than the
  original's. The Sobol and FAST indices, the OAT variances and the ANOVA F and p are identical to the original's.
- The analysis reads scalar metrics per run. The time series stay with the KPI calculator, and there is
  no plot of a single run.
