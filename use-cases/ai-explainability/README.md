# AI Explainability Pipeline

This use case wraps the vendored [AI Explainability toolbox](services/explainability_runner/toolbox/README.md)
in an AI-EFFECT workflow. A configuration assistant turns supported user requests
into validated analysis settings before the explainability runner executes. The toolbox uses SHAP to explain tabular and
time-series model predictions and produces audit artifacts such as a flattened
CSV, optional Excel workbook, and optional notebook report.

Author, maintainer, and MIT license information are available in
`services/explainability_runner/toolbox/ai_explainability.egg-info/PKG-INFO`.

## Pipeline

```
config_assistant → explainability_runner
 ConfigureAnalysis          Explain
```

The assistant supports tabular Random Forest, XGBoost, and feedforward neural
models, plus time-series ARIMA and LSTM. It asks a brief follow-up when the
analysis or model type is unclear, and does not configure requests outside those
capabilities. Users can skip the assistant and enter a configuration directly.
The assistant uses a local Ollama model; prompts are not sent to a hosted LLM.

`web_ui` and Ollama are supporting services, not workflow nodes. The UI is
deployed by `docker-compose.yml` to submit workflows and display their results.
Ollama provides the model used by the config assistant.

## Layout

```
ai-explainability/
├── assets/
│   ├── models/                         # place model files here
│   └── data/                           # place CSV, TSV, or Parquet data here
├── common/concurrent.py                # AI-EFFECT control interface
├── services/
│   ├── config_assistant/               # prompt-to-config intake and validation
│   └── explainability_runner/          # adapter plus vendored toolbox source
├── web_ui/                              # shared manifest-driven Bootstrap UI
├── connections.json
├── ui.json
├── docker-compose.yml
└── generate-export.sh
```

## Input contract

### Runnable sample

The upstream repository does not publish a serialized model, so the thin
use case includes a reproducible preparation script for testing. It downloads the
upstream energy CSV, trains a small Random Forest, and writes ignored assets.
Use the dedicated virtual environment, whose dependencies pin NumPy below 2:

```bash
python3 -m venv .venv-sample
.venv-sample/bin/pip install -r scripts/requirements-sample.txt
.venv-sample/bin/python scripts/prepare-sample.py
```

Then use the generated files in the web UI, or paste the contents of
`sample-config.json` into the configuration field. Completed runs return a
Markdown feature-influence report and links to generated artifacts.

Describe the requested analysis on the first screen. The assistant will confirm
a supported setup, ask one short follow-up question, or explain briefly that
the request is outside its supported model types. Choose **I know what I want,
proceed to next step** to bypass the assistant and configure the analysis
manually.


General conversational requests use the local Ollama `qwen2.5:1.5b` LLM with
deterministic sampling, followed by capability validation. Explicit short model
answers (including "LSTM time-series models") are normalized directly so a model
extraction miss cannot trigger a repeated question. This is a hybrid assistant,
not an LLM call for every answer.

Upload the model and dataset directly in the web UI, then submit the JSON
configuration. Uploaded files are stored on the shared workflow volume under
`/data/uploads/` and their paths are inserted into the configuration
as soon as each selection finishes uploading, before **Run analysis**. The UI
shows upload progress, reuses completed uploads on submission, and ignores stale
results after file replacement or reset. Alternatively, mount files in `assets/` and use paths relative
to that directory. The runner accepts files only from `/assets` or its
controlled `/data/uploads/` directory.

The configuration starts empty. The assistant supplies the model type; selecting files fills
paths but does not guess the algorithm. After a successful upload, missing
`analysis` and `model_type` fields appear as empty strings for the user to fill
in. Existing values are preserved. File notes show safe metadata inspection:

- CSV/TSV headers are suggested as `feature_names` in their original order.
  Review them and remove target/ID columns not used as model inputs.
- For a known LSTM setup, plain tensor uploads supply positional `f0`, `f1`, ...
  labels and display `(samples, look_back, features)`. Tensors do not carry the
  original semantic feature names; those must come from training metadata.
- Explicitly supplied feature names are preserved. Automatically generated
  names update when the selected dataset changes.
- Other formats require manual feature review; inspection failures are visible
  and do not discard an already uploaded file. Inspection does not deserialize
  executable model objects, and tensor reads use `weights_only=True`.

Feature names are not mandatory: tabular explainers use dataset columns when
omitted, LSTM uses positional labels, and ARIMA uses supplied names only to
select exogenous coefficients for its plots. All inputs still need to match the
model's training feature order and dimensions.


Each inspected file has a separate entry with its upload role, filename, and
dimensions. Tensor dimensions are labeled as samples, time steps, and features;
identical feature-name guidance is shown once instead of repeated per file.
CSV, Markdown, and JSON artifact previews use the task that produced each file,
just like downloads, including workflows with an assistant step before analysis.


Model-specific uploads follow `model_type` in the configuration, including
manual edits: test tensors appear only for LSTM; background data appears for
LSTM and feedforward neural models. These fields are hidden while the model is
unknown or unrelated. Existing selections and settings are retained when hidden.

For example, when using mounted assets:

```json
{
  "analysis": "tabular",
  "model_type": "random_forest",
  "model_path": "models/model.pkl",
  "dataset_path": "data/dataset.csv",
  "feature_names": ["wind_speed", "temperature"]
}
```

The adapter writes all results to `/data/<workflow_id>/<task_id>/` on the shared
Docker volume, returns `summary.json` as a file DataReference, and includes the
artifact names in its output. Set `save_excel` or `generate_notebook` in the
configuration to control optional reports.

The assistant reads the same lightweight
[capability contract](services/explainability_runner/toolbox/ai_explainability/capabilities.py)
as the explainer's model routers and neural/LSTM backend selectors. It preserves
supported settings on every JSON intake path, rather than maintaining a narrower
assistant-only whitelist. A supported setup does not guarantee compatible file
contents or a successful analysis; those checks remain the runner's responsibility.

Manual configurations accept `package`, `framework`, `dataset_scope`, and `subset_end`.
For tabular analysis, `dataset_scope: "subset"` explains the first `subset_end`
rows (100 by default). Unsupported configuration fields are named in the error
so users can revise them without guessing.

| Model | Explanation | Additional settings |
| --- | --- | --- |
| `random_forest`, `xgboost` | Tree SHAP | `target_index`, `output_labels` |
| `feedforward`, `mlp`, `neural_net` | PyTorch/TensorFlow kernel, deep, or gradient SHAP | `explainer_type`, `background_data_path`, `background_size`, `kernel_nsamples` |
| `lstm` | PyTorch gradient or deep SHAP | `explainer_type`, `input_dim`, `hidden_size`, `look_back`, `explain_subset`, background/test tensor paths |
| `arima` | Fitted coefficients and statistical significance, not SHAP | `feature_names` to select exogenous coefficients |

Excel export is available for tree, neural, and LSTM models. The ARIMA explainer
does not implement Excel export, so its workflow default is `save_excel: false`;
explicit requests for ARIMA Excel export receive a brief correction. For ARIMA,
`generate_notebook: true` creates coefficient and diagnostic plots, not a notebook.
LSTM has a single forecast output: accepting the legacy `look_ahead` key does not
provide multi-step forecasting. The workflow controls `output_dir` even when the
configuration includes it.

LSTM Excel reports derive the time-step count and feature count from the
computed tensors. Feature names are optional (unnamed features use `f0`, `f1`,
and so on); supplied names must match the tensor's feature count. An optional
`look_back` setting must match the tensor's time-step count.

## Running

1. Place supported model and data files in `assets/models/` and `assets/data/`.
2. Start the platform orchestrator from `orchestrator/`.
3. Run `./start.sh` from this directory.
4. Run `./generate-export.sh` to create `export.zip`.
5. Open `http://localhost:18204`, describe the analysis (or skip to manual
  setup), and follow instructions.

The orchestrator reaches `explainability-config-assistant` and `explainability-runner` over the
shared `ai-effect-services` Docker network on port 8080. Docker Compose pulls the configured
`qwen2.5:1.5b` Ollama model on its first run.

The runner image includes XGBoost, statsmodels, the ARIMA HTML parser, and CPU
TensorFlow alongside PyTorch so the advertised model families have their
runtime dependencies.

## Compatibility validation

Run all assistant tests without importing the heavy ML frameworks:

```bash
PYTHONPATH=.:services/explainability_runner/toolbox \
  ../../.venv/bin/python -m unittest discover -s services/config_assistant -p 'test*.py'
```

The parity suite exercises all seven model identifiers, each consumed
configuration key, optional value variants, and all JSON/conversation intake
paths. It checks actual model routing and fails if the explainer consumes a key
not covered by the assistant's contract.

Run `services/explainability_runner/test_model_compatibility.py` in the runner
environment with the use-case root on `PYTHONPATH` alongside its toolbox and
adapter. From this directory, validate without touching the live services: Tests are running on separate ports.

```bash
docker run --rm --network none \
  --mount "type=bind,source=$PWD,target=/validation,readonly" \
  --workdir /validation/services/explainability_runner \
  -e PYTHONPATH=/validation:/validation/services/explainability_runner/toolbox:/app \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 \
  -e TF_NUM_INTRAOP_THREADS=1 -e TF_NUM_INTEROP_THREADS=1 \
  ai-explainability-explainability-runner \
  python -m unittest discover -s /validation/services/explainability_runner -p 'test*.py'
```

It validates tiny serialized models and real explanations: Random Forest
and XGBoost regression/classification, multi-output tree wrappers, all three
neural aliases on PyTorch and TensorFlow with every supported SHAP backend, both
LSTM backends, and ARIMA coefficient analysis. It also checks supported report
options and the runner's ARIMA Excel default. Tests use temporary upload
directories and remove their generated files.

## Onboard to the Portal

1. From this directory, run `./generate-export.sh`. It regenerates the export
  and packages the portal import as `export.zip`.
2. Make sure `explainability-config-assistant` and `explainability-runner` are deployed and
  reachable by the portal orchestrator as `explainability-config-assistant:8080` and
  `explainability-runner:8080`. These addresses are declared in
  `connections.json`; a local `docker-compose.yml` deployment is not
  automatically deployed to the portal. The config assistant also needs an
  Ollama endpoint with the configured model.
3. Open [https://portal.renewenergy.io](https://portal.renewenergy.io), go to
  **Solutions → Import ZIP**, and upload `export.zip`.
4. Open the imported solution in the portal to view and run the pipeline.

### Deploy and Test: Instructions repeats what is already available in the orchestrator and platform tutorials

The tutorial deployment runs the service and orchestrator locally and exposes
the orchestrator to the portal with ngrok. Put model and data files under
`assets/` and use asset-relative paths in the analysis configuration; 
**files uploaded through the local web UI are not available to a portal deployment for now**.

1. Create the shared network once, then start the orchestrator:

  ```bash
  docker network create ai-effect-services
  cd orchestrator
  ORCHESTRATOR_API_KEY=your-orchestrator-key docker compose up -d
  curl http://localhost:18000/health
  ```

  Omit the `ORCHESTRATOR_API_KEY` assignment if running without authentication.
2. From `orchestrator/`, switch to the use case, then build and start its
  containers:

  ```bash
  cd ../use-cases/ai-explainability
  ./start.sh
  docker compose ps
  ```
3. Configure ngrok once with `ngrok config add-authtoken YOUR_NGROK_TOKEN`,
  then run `ngrok http 18000`. Keep the tunnel running and copy its HTTPS URL.
4. In the portal, open the imported solution and click **Edit**. Set the
  **Orchestrator URL** to the ngrok URL (no trailing slash) and the
  **Orchestrator API Key** to the key used in step 1. Save the solution once
  after import to rebuild its blueprint, then click **Deploy**. For a public
  solution you do not own, enter the URL and key in the **Deploy** dialog
  instead.
5. Check the deployment detail page until the `Explain` task completes. Keep
  both the service containers and ngrok tunnel running while the portal runs.

This compose setup does not pass `SERVICE_API_KEY` to the runner, so leave the
portal's **Services API Key** blank. Service authentication must be wired into
the runner deployment before using that field.

The export contains the `config_assistant` and `explainability_runner` workflow nodes and their service
metadata. The local `web_ui` is a supporting service and is not part of the
portal export; portal runs are submitted through the portal itself.
