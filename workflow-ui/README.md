# Workflow UI

One shared web UI for every AI-EFFECT workflow. Each workflow keeps the same
layout and describes what is specific to it in a `ui.json` file.

The UI submits the workflow's `export/blueprint.json` to the orchestrator,
shows each step's progress and results, and records user decisions.

## Create a UI for a new workflow

1. **Add the UI service** to your workflow's `docker-compose.yml`:

   ```yaml
   web-ui:
     build:
       context: ../../workflow-ui
     ports:
       - "18304:8080"                       # pick a free port
     environment:
       - DATA_DIR=/data
       - ORCHESTRATOR_URL=http://host.docker.internal:18000
       - USE_CASE_DIR=/workspace/use-cases/my-workflow
     volumes:
       - my-workflow-data:/data             # same volume as your services
       - .:/workspace/use-cases/my-workflow:ro
     networks:
       - ai-effect-services
   ```

2. **Create `ui.json`** next to `connections.json`. A minimal one:

   ```json
   {
     "title": "My Workflow",
     "description": "What this workflow does.",
     "submit_label": "Run",
     "inputs": [
       {"name": "prompt", "label": "Request", "type": "textarea", "required": true}
     ],
     "steps": [
       {"method": "FirstMethod", "title": "First step"},
       {"method": "SecondMethod", "title": "Second step"}
     ]
   }
   ```

   Each step `method` must match a method in `connections.json`.

3. **Start it** and open the port:

   ```bash
   docker compose up -d --build web-ui
   ```

4. **Run the checks** from the repository root:

   ```bash
   node --test workflow-ui/tests/
   ```

   This validates every `use-cases/*/ui.json`.

If `ui.json` is missing or invalid, the page shows a warning and a generic form.

## Add a service to a workflow

Example: adding the experiment mapper after the topology steps.

1. **Create the service** from a template in
   [`use-cases/templates/`](../use-cases/templates/README.md):

   ```bash
   cp -r use-cases/templates/file_based/concurrent use-cases/<workflow>/services/experiment_mapper
   ```

   Implement your method in `service.py`, and add
   `proto/experiment_mapper.proto` declaring it (for example `rpc MapExperiment`).

2. **Run it** by adding it to the workflow's `docker-compose.yml`, on the
   `ai-effect-services` network and with the shared data volume.

3. **Wire it in** `connections.json`: add it to `service_mapping`, then connect
   the step that feeds it:

   ```json
   {"from_service": "draw_topology", "from_method": "RenderTopologies",
    "to_service": "experiment_mapper", "to_method": "MapExperiment"}
   ```

4. **Regenerate the export.** The UI submits `export/blueprint.json`, so a
   service missing from it never runs:

   ```bash
   cd use-cases/<workflow> && ./generate-export.sh
   ```

5. **Show it in the UI** by adding a step to `ui.json`:

   ```json
   {"method": "MapExperiment", "title": "Experiment mapping",
    "description": "Grounds the test spec in the confirmed topology."}
   ```

6. **Restart and check:**

   ```bash
   docker compose up -d --build
   node --test workflow-ui/tests/
   ```

For proto files, node types and the portal upload, see the
[onboarding guide](../use-cases/ONBOARDING-GUIDE.md).

## `ui.json` reference

| Key | Purpose |
|---|---|
| `title`, `description` | Page heading. |
| `brand` | `{"mark": "D", "name": "Danish Node"}` in the top bar. |
| `theme` | `accent`, `accent_strong`, `background`, `font_family`. |
| `submit_label` | Text of the run button. |
| `poll_interval_ms` | How often progress is refreshed (default 2000). |
| `inputs` | Form fields, submitted to the workflow by `name`. |
| `steps` | Readable names and order of the workflow's methods. |
| `output` | `title`, `summary_fields`, and `image_collection_field` / `image_signature_field` for image lists. |
| `actions` | Buttons shown when the whole run completes. |
| `assistant` | Optional setup assistant (needs `CONFIG_ASSISTANT_URL`). |
| `plugins` | Script files in the workflow's `ui/` folder. |

### Inputs

Types: `text`, `textarea`, `number`, `checkbox`, `select` (with `options`), `file`.
Optional keys: `label`, `help`, `placeholder`, `default`, `required`.

A `file` input uploads on selection and writes the stored path into the JSON
field `config_json`:

```json
{"name": "model_upload", "type": "file", "config_field": "model_path", "upload_category": "models"}
```

Show a field only for some configuration values:

```json
"show_when": {"config_key": "model_type", "values": ["lstm"]}
```

### Decision gates

Ask the user to approve a step's result once it completes:

```json
{
  "method": "RenderTopologies",
  "title": "Topology visualization",
  "gate": {
    "prompt": "Confirm the recommended topology.",
    "value_field": "report.recommended_network",
    "actions": [
      {"name": "accepted", "label": "Confirm", "style": "primary"},
      {"name": "rejected", "label": "Reject", "style": "danger"}
    ]
  }
}
```

`value_field` is read from that step's output (a dotted path, or a key found
anywhere). Decisions are saved in `DATA_DIR/<workflow_id>/ui-decisions.json`.

The orchestrator does not pause at gates yet, so later steps still run without
waiting for the decision.

### Results

Each step's output appears as a card showing, when present: a summary,
`report_markdown`, images, and `artifacts`. Artifacts are previewed by type:
images, HTML (sandboxed), Markdown, CSV, JSON, YAML and text. They can also be
downloaded singly or as a zip.

## Plugins

For behaviour that `ui.json` cannot express. Put the script in the workflow's
`ui/` folder, list it in `plugins`, and implement any of these hooks:

```js
(globalThis.WorkflowUIPlugins ||= []).push({
  applyUploads({config, uploads}) {},   // adjust config after uploads
  uploadGuidance({config, uploads}) {}, // return {messages: [], attention: false}
  validate({config, uploads}) {},       // throw an Error to block the run
  renderOutput({output, step}) {},      // return extra HTML for a result card
  reset() {},
});
```

Example: [`use-cases/ai-explainability/ui/explainability.js`](../use-cases/ai-explainability/ui/explainability.js).

## Environment

| Variable | Purpose |
|---|---|
| `USE_CASE_DIR` | Mounted workflow folder (required). |
| `ORCHESTRATOR_URL` | Orchestrator API. |
| `DATA_DIR` | Shared data volume. |
| `CONFIG_ASSISTANT_URL` | Optional setup assistant. |
| `DATA_INSPECTION_URL` | Optional file inspection for inputs with `"inspect": true`. |

## Tests

```bash
node --test workflow-ui/tests/ use-cases/ai-explainability/ui/tests/
python -m pytest workflow-ui/tests        # needs pytest and httpx
```
