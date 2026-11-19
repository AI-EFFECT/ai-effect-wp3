const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {setup, reply, uiDir} = require('./harness.cjs');

const topologyManifest = {
  inputs: [{name: 'prompt', type: 'textarea', help: 'Describe the equipment.'}],
  submit_label: 'Generate topology',
  steps: [
    {method: 'BuildRunConfig', title: 'Configuration preparation', description: 'Turns the request into settings.'},
    {method: 'GenerateTopology', title: 'Topology generation'},
    {
      method: 'RenderTopologies',
      title: 'Topology visualization',
      gate: {
        prompt: 'Confirm the recommended topology.',
        value_field: 'report.recommended_network',
        actions: [
          {name: 'accepted', label: 'Confirm topology', style: 'primary'},
          {name: 'rejected', label: 'Reject topology', style: 'danger'},
        ],
      },
    },
  ],
};

const assistantManifest = {
  features: {assistant: true},
  assistant: {enabled: true, hint: 'Model type means the algorithm.', hint_when_missing: 'model_type'},
  inputs: [{name: 'config_json', type: 'textarea', default: '{}'}],
};

const completedTopologyRun = {
  workflow_id: 'wf',
  status: 'completed',
  tasks: [
    {task_id: 'config-id', node_key: 'config_assistant1:BuildRunConfig', status: 'completed'},
    {task_id: 'generate-id', node_key: 'topology_generator1:GenerateTopology', status: 'completed'},
    {task_id: 'draw-id', node_key: 'draw_topology1:RenderTopologies', status: 'completed'},
  ],
  outputs: [
    {task_id: 'config-id', payload: {}},
    {task_id: 'generate-id', payload: {networks: [{signature: 'first'}]}},
    {task_id: 'draw-id', payload: {
      drawings: [{signature: 'first'}, {signature: 'best'}],
      report: {recommended_network: 'best'},
    }},
  ],
};

// --- Shell ---------------------------------------------------------------

test('uniform shell keeps status outside both forms and announces it', () => {
  const html = fs.readFileSync(path.join(uiDir, 'templates/index.html'), 'utf8');
  for (const name of ['app-navbar', 'workspace-grid', 'input-rail', 'results-stage']) assert.ok(html.includes(name));
  assert.match(html, /<\/form>\s*<div[^>]*id="status"[^>]*aria-live="polite"/);
  assert.match(html, /\{\{ plugin_scripts \}\}\s*<script src="\/static\/app\.js/);
  assert.doesNotMatch(html, /explain|topology/i);
});

test('a manifest warning stays visible instead of silently showing the generic form', async () => {
  const ui = await setup({manifest: {manifest_warning: 'No UI manifest found.'}});
  assert.equal(ui.element('manifestWarning').hidden, false);
  assert.equal(ui.element('manifestWarning').textContent, 'No UI manifest found.');
  const normal = await setup({manifest: {}});
  assert.equal(normal.element('manifestWarning').hidden, true);
});

test('field guidance uses collapsed accessible help', async () => {
  const ui = await setup({manifest: topologyManifest});
  assert.match(ui.element('fields').innerHTML, /<details class="field-help">/);
  const html = ui.fieldHtml({name: 'model', label: 'Model file', type: 'file', help: 'Check model type.'});
  assert.match(html, /<details class="field-help"><summary>Help/);
  assert.match(html, /visually-hidden"> for Model file/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
});

// --- Steps and timeline --------------------------------------------------

test('step labels from the manifest are used in the timeline and output cards', async () => {
  const ui = await setup({manifest: topologyManifest});
  ui.renderOutputs(completedTopologyRun);
  for (const id of ['timeline', 'output']) {
    const html = ui.element(id).innerHTML;
    for (const label of ['Configuration preparation', 'Topology generation', 'Topology visualization']) {
      assert.ok(html.includes(`>${label}<`), label);
    }
    for (const taskId of ['config-id', 'generate-id', 'draw-id']) {
      assert.match(html, new RegExp(`<summary>Technical details</summary>\\s*<div>Task ID: <code>${taskId}</code>`));
    }
    assert.doesNotMatch(html, /<(?:strong|h3)[^>]*>(?:config-id|generate-id|draw-id)</);
    assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  }
  assert.match(ui.element('output').innerHTML, /<summary>Raw output<\/summary><pre>/);
});

test('steps that have not started are shown as waiting in manifest order', async () => {
  const ui = await setup({manifest: topologyManifest});
  ui.renderOutputs({
    workflow_id: 'wf', status: 'running', outputs: [],
    tasks: [{task_id: 'config-id', node_key: 'config_assistant1:BuildRunConfig', status: 'running'}],
  });
  const html = ui.element('timeline').innerHTML;
  const order = ['Configuration preparation', 'Topology generation', 'Topology visualization'].map(label => html.indexOf(label));
  assert.deepEqual([...order].sort((a, b) => a - b), order);
  assert.equal((html.match(/>waiting</g) || []).length, 2);
  assert.match(html, /workflow-task-running/);
});

test('unknown operations and task identifiers remain escaped and identifiable', async () => {
  const ui = await setup({manifest: {}});
  ui.renderOutputs({
    tasks: [{task_id: '<id>', node_key: 'other:Custom<operation>', status: 'running'}],
    outputs: [{task_id: '<id>', payload: {}}],
  });
  for (const id of ['timeline', 'output']) {
    assert.match(ui.element(id).innerHTML, /Custom&lt;operation&gt;/);
    assert.match(ui.element(id).innerHTML, /Task ID: <code>&lt;id&gt;<\/code>/);
  }
});

test('running status uses the readable step title rather than its task ID', async () => {
  const ui = await setup({
    manifest: topologyManifest,
    request: async () => reply({
      workflow_id: 'wf', status: 'running', outputs: [],
      tasks: [{task_id: 'generate-id', node_key: 'topology_generator1:GenerateTopology', status: 'running'}],
    }),
  });
  ui.run('workflowId = "wf"');
  await ui.run('pollWorkflow()');
  assert.equal(ui.element('status').textContent, 'Running Topology generation...');
  assert.equal(ui.element('activityLabel').textContent, 'Running');
});

// --- Gates ---------------------------------------------------------------

test('a gated step asks for a decision once it completes, and records the gate value', async () => {
  const requests = [];
  const ui = await setup({
    manifest: topologyManifest,
    request: async (options, url) => {
      requests.push({url, body: JSON.parse(options.body)});
      return reply({status: 'accepted', value: 'best', message: 'Decision recorded for workflow wf.'});
    },
  });
  ui.renderOutputs({...completedTopologyRun, status: 'running', tasks: completedTopologyRun.tasks.map(task => (
    task.task_id === 'draw-id' ? {...task, status: 'running'} : task
  ))});
  assert.doesNotMatch(ui.element('output').innerHTML, /data-action/);

  ui.renderOutputs(completedTopologyRun);
  const html = ui.element('output').innerHTML;
  assert.equal((html.match(/class="workflow-gate"/g) || []).length, 1);
  assert.match(html, /Confirm the recommended topology\./);
  assert.match(html, /class="btn btn-primary" data-action="accepted" data-step="RenderTopologies">Confirm topology/);
  assert.match(html, /class="btn btn-outline-danger" data-action="rejected" data-step="RenderTopologies">Reject topology/);

  ui.run('workflowId = "wf"');
  await ui.run('recordDecision("accepted", "RenderTopologies")');
  assert.deepEqual(requests[0], {
    url: '/decision',
    body: {workflow_id: 'wf', action: 'accepted', step: 'RenderTopologies', value: 'best'},
  });
  assert.match(ui.element('output').innerHTML, /Decision recorded: <strong>Confirm topology<\/strong> <code>best<\/code>/);
  assert.doesNotMatch(ui.element('output').innerHTML, /data-action/);
});

test('completion status names gates that still need a decision', async () => {
  let decisions = {};
  const ui = await setup({
    manifest: topologyManifest,
    request: async () => reply({...completedTopologyRun, decisions}),
  });
  ui.run('workflowId = "wf"');
  await ui.run('pollWorkflow()');
  assert.equal(ui.element('status').textContent, 'Workflow completed. A decision is needed on: Topology visualization.');
  decisions = {RenderTopologies: {action: 'accepted', value: 'best'}};
  await ui.run('pollWorkflow()');
  assert.equal(ui.element('status').textContent, 'Workflow completed.');
});

test('workflow-level actions appear only when the run completes', async () => {
  const manifest = {...topologyManifest, actions: [{name: 'archive', label: 'Archive run', style: 'secondary'}]};
  const ui = await setup({
    manifest,
    request: async url => reply(url === '/submit'
      ? {workflow_id: 'wf', status: 'running', outputs: []}
      : {workflow_id: 'wf', status: 'completed', outputs: [{payload: {summary: 'Done'}}]}),
  });
  assert.equal(ui.element('actions').hidden, true);
  assert.match(ui.element('actions').innerHTML, /class="btn btn-outline-secondary" data-action="archive">Archive run/);
  await ui.submitWorkflow();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.element('status').textContent, 'Workflow completed.');
  assert.equal(ui.element('actions').hidden, false);
  assert.equal(ui.element('submitBtn').disabled, false);
});

// --- Output renderers ----------------------------------------------------

test('images collections render through the asset endpoint', async () => {
  const ui = await setup({manifest: topologyManifest});
  ui.renderOutputs({workflow_id: 'wf', status: 'completed', outputs: [{task_id: 'draw', payload: {drawings: [{signature: 'topology'}]}}]});
  assert.match(ui.element('output').innerHTML, /src="\/workflows\/wf\/assets\/topology"/);
});

test('artifacts are only listed when the payload declares them', async () => {
  const ui = await setup();
  assert.equal(ui.renderArtifacts({status: 'ready', config: {}}), '');
  assert.match(ui.renderArtifacts({artifacts: ['report.md']}), /report\.md/);
});

test('artifacts preview by type: images inline, HTML sandboxed, text fetched', async () => {
  const ui = await setup();
  const html = ui.renderArtifacts({artifacts: ['plot.png', 'Report.html', 'config.yaml', 'model.bin']});
  assert.match(html, /<img class="img-fluid" src="\/workflows\/workflow\/files\/task\/plot\.png"/);
  assert.match(html, /<iframe class="artifact-frame" sandbox="" src="\/workflows\/workflow\/files\/task\/Report\.html"/);
  assert.match(html, /data-artifact-url="\/workflows\/workflow\/files\/task\/config\.yaml"/);
  assert.equal((html.match(/artifact-preview/g) || []).length, 3);
});

test('previews fetch each producing task and render by format', async () => {
  const urls = [];
  const ui = await setup({
    request: async (_, url) => {
      urls.push(url);
      if (url.endsWith('.csv')) return {ok: true, text: async () => 'Age,Income\n30,100'};
      if (url.endsWith('.yaml')) return {ok: true, text: async () => 'supply: <310-D>'};
      return {ok: true, text: async () => '# Explanation'};
    },
  });
  const previews = [
    {dataset: {artifactPath: 'report.md', artifactUrl: '/workflows/workflow/files/explainer/report.md'}},
    {dataset: {artifactPath: 'explanation.csv', artifactUrl: '/workflows/workflow/files/other/explanation.csv'}},
    {dataset: {artifactPath: 'test_config.yaml', artifactUrl: '/workflows/workflow/files/spec/test_config.yaml'}},
  ];
  await ui.loadPreviews(previews);
  assert.deepEqual(urls, previews.map(preview => preview.dataset.artifactUrl));
  assert.match(previews[0].innerHTML, /Explanation/);
  assert.match(previews[1].innerHTML, /<table/);
  assert.match(previews[2].innerHTML, /<pre>supply: &lt;310-D&gt;<\/pre>/);
});

test('plugins can add output content', async () => {
  const plugin = path.join(require('node:os').tmpdir(), `workflow-ui-plugin-${process.pid}.js`);
  fs.writeFileSync(plugin, `(globalThis.WorkflowUIPlugins ||= []).push({
    renderOutput({output, step}) { return '<p class="plugin">' + WorkflowUI.escapeHtml(step?.title + ':' + output.payload.kpi) + '</p>'; },
  });`);
  try {
    const ui = await setup({manifest: topologyManifest, plugins: [plugin]});
    ui.renderOutputs({tasks: [{task_id: 't', node_key: 's:GenerateTopology'}], outputs: [{task_id: 't', payload: {kpi: '<3'}}]});
    assert.match(ui.element('output').innerHTML, /<p class="plugin">Topology generation:&lt;3<\/p>/);
  } finally {
    fs.unlinkSync(plugin);
  }
});

// --- Submission ----------------------------------------------------------

test('network failure restores submit controls and displays the error', async () => {
  const ui = await setup({manifest: topologyManifest, request: async () => { throw new Error('Offline'); }});
  await ui.submitWorkflow();
  assert.equal(ui.element('status').textContent, 'Offline');
  assert.equal(ui.element('submitBtn').disabled, false);
  assert.equal(ui.element('workflowForm').attributes['aria-busy'], 'false');
});

test('reset prevents an outstanding submission from restoring stale results', async () => {
  let finish;
  const ui = await setup({manifest: topologyManifest, request: () => new Promise(resolve => { finish = resolve; })});
  const pending = ui.submitWorkflow();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(ui.element('submitBtn').innerHTML, /spinner-border/);
  assert.equal(ui.element('submitBtn').disabled, true);
  ui.reset();
  finish(reply({workflow_id: 'stale', outputs: []}));
  await pending;
  assert.equal(ui.run('workflowId'), null);
  assert.equal(ui.element('actions').hidden, true);
  assert.equal(ui.element('submitBtn').disabled, false);
});

test('an invalid JSON configuration blocks submission with a clear message', async () => {
  const ui = await setup({manifest: assistantManifest, request: async () => reply({workflow_id: 'wf'})});
  ui.element('field-config_json').value = '{';
  await ui.submitWorkflow();
  assert.equal(ui.element('status').textContent, 'The configuration must be valid JSON.');
});

// --- Setup assistant -----------------------------------------------------

test('the assistant stays hidden unless the server enables it', async () => {
  const disabled = await setup({manifest: {assistant: {enabled: true}, features: {assistant: false}}});
  assert.equal(disabled.element('assistantStep').hidden, true);
  assert.equal(disabled.element('workflowForm').hidden, false);
  const enabled = await setup({manifest: assistantManifest});
  assert.equal(enabled.element('assistantStep').hidden, false);
  assert.equal(enabled.element('workflowForm').hidden, true);
});

test('pending assistant request shows feedback, spinner, and long-wait message', async () => {
  let finish;
  const ui = await setup({manifest: assistantManifest, request: () => new Promise(resolve => { finish = resolve; })});
  ui.element('assistantPrompt').value = 'Explain my LSTM model';
  const pending = ui.submitAssistant();
  assert.equal(ui.element('workflowForm').hidden, true);
  assert.match(ui.element('status').textContent, /workflow has not started/);
  assert.match(ui.element('assistantSubmitBtn').innerHTML, /spinner-border/);
  assert.equal(ui.element('activityLabel').textContent, 'Checking request');
  assert.equal(ui.element('skipAssistantBtn').disabled, true);
  [...ui.timers.values()].find(timer => timer.delay === 15000).callback();
  assert.match(ui.element('status').textContent, /Still waiting/);
  finish(reply({status: 'ready', message: 'Supported.', config: {}}));
  await pending;
  assert.equal(ui.element('assistantContinueBtn').hidden, false);
  assert.equal(ui.element('assistantSubmitBtn').textContent, 'Check my request');
  assert.equal(ui.element('skipAssistantBtn').disabled, false);
  assert.equal(ui.element('assistantStep').attributes['aria-busy'], 'false');
  assert.equal(ui.timers.size, 0);
});

test('clarification requests more information, restores focus, and shows the hint', async () => {
  const ui = await setup({manifest: assistantManifest, request: async () => reply({status: 'clarification', message: 'Which model type?'})});
  ui.element('assistantPrompt').value = 'Explain my model';
  await ui.submitAssistant();
  assert.match(ui.element('status').textContent, /more information/);
  assert.equal(ui.element('assistantPrompt').focused, true);
  assert.equal(ui.element('assistantContinueBtn').hidden, true);
  assert.equal(ui.element('assistantHint').hidden, false);
  assert.equal(ui.element('assistantHint').textContent, 'Model type means the algorithm.');
  assert.equal(ui.element('assistantPrompt').attributes['aria-describedby'], 'assistantHint');
});

test('the hint hides after a successful revision and when its key is already known', async () => {
  let response = {status: 'clarification', message: 'Setup response.', config: {}};
  const ui = await setup({manifest: assistantManifest, request: async () => reply(response)});
  assert.equal(ui.element('assistantHint').hidden, true);
  ui.element('assistantPrompt').value = 'Explain';
  await ui.submitAssistant();
  assert.equal(ui.element('assistantHint').hidden, false);
  response = {status: 'ready', message: 'Ready.', config: {model_type: 'lstm'}};
  ui.element('assistantPrompt').value = 'LSTM';
  await ui.submitAssistant();
  assert.equal(ui.element('assistantHint').hidden, true);
  assert.equal(ui.element('assistantPrompt').attributes['aria-describedby'], undefined);
  response = {status: 'clarification', message: 'Which analysis?', config: {model_type: 'lstm'}};
  ui.element('assistantPrompt').value = 'More';
  await ui.submitAssistant();
  assert.equal(ui.element('assistantHint').hidden, true);
});

test('assistant service errors remain visible and restore controls', async () => {
  const ui = await setup({manifest: assistantManifest, request: async () => ({ok: false, json: async () => ({detail: 'The setup assistant is unavailable.'})})});
  ui.element('assistantPrompt').value = 'Explain';
  await ui.submitAssistant();
  assert.match(ui.element('status').textContent, /unavailable/);
  assert.equal(ui.element('activityLabel').textContent, 'Needs attention');
  assert.equal(ui.element('assistantSubmitBtn').disabled, false);
  assert.equal(ui.timers.size, 0);
});

test('assistant timeout explains the delay and offers manual setup', async () => {
  const ui = await setup({
    manifest: assistantManifest,
    request: options => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const error = new Error('Aborted');
        error.name = 'AbortError';
        reject(error);
      });
    }),
  });
  ui.element('assistantPrompt').value = 'Explain';
  const pending = ui.submitAssistant();
  [...ui.timers.values()].find(timer => timer.delay === 210000).callback();
  await pending;
  assert.match(ui.element('status').textContent, /too long.*manual setup/);
  assert.equal(ui.element('skipAssistantBtn').disabled, false);
  assert.equal(ui.timers.size, 0);
});

// --- Uploads (generic behaviour, no plugin) ------------------------------

const configField = {name: 'config_json', type: 'textarea', default: '{}'};
const dataField = {name: 'data_upload', label: 'Data file', type: 'file', config_field: 'data_path', upload_category: 'data', inspect: true};
const uploadManifest = {features: {upload_inspection: true}, inputs: [dataField, configField]};

test('selecting a file uploads it once and writes its path into the configuration', async () => {
  let uploads = 0;
  const ui = await setup({
    manifest: uploadManifest,
    request: async (options, url) => {
      if (url === '/uploads') {
        uploads += 1;
        return reply({path: `/data/uploads/${options.body.get('file').name}`});
      }
      return reply({kind: 'table', feature_names: ['Age'], message: 'Review columns.'});
    },
  });
  await ui.select(dataField, {name: 'data.csv'});
  assert.deepEqual(JSON.parse(ui.element('field-config_json').value), {data_path: '/data/uploads/data.csv'});
  assert.match(ui.element('fileSetupFeedback').textContent, /^File information: 1 inspected/);
  await ui.finishUploads();
  assert.equal(uploads, 1);
});

test('inspection is skipped when the workflow has no inspection service', async () => {
  const urls = [];
  const ui = await setup({
    manifest: {inputs: [dataField, configField]},
    request: async (_, url) => { urls.push(url); return reply({path: '/data/uploads/data.csv'}); },
  });
  await ui.select(dataField, {name: 'data.csv'});
  assert.deepEqual(urls, ['/uploads']);
});

test('failed uploads leave the configuration unchanged and report the error', async () => {
  const ui = await setup({manifest: uploadManifest, request: async () => ({ok: false, json: async () => ({detail: 'Upload failed.'})})});
  await ui.select(dataField, {name: 'data.csv'});
  assert.equal(ui.element('field-config_json').value, '{}');
  assert.equal(ui.element('status').textContent, 'Upload failed.');
});

test('inspection failures keep the uploaded path and ask for manual review', async () => {
  const ui = await setup({
    manifest: uploadManifest,
    request: async (_, url) => url === '/uploads'
      ? reply({path: '/data/uploads/data.pt'})
      : {ok: false, json: async () => ({detail: 'Invalid tensor dimensions.'})},
  });
  await ui.select(dataField, {name: 'invalid.pt'});
  assert.equal(JSON.parse(ui.element('field-config_json').value).data_path, '/data/uploads/data.pt');
  assert.match(ui.element('status').textContent, /file inspection failed/);
  assert.match(ui.element('fileSetupFeedback').textContent, /Invalid tensor dimensions/);
  assert.equal(ui.element('fileSetupFeedback').classList.contains('file-setup-feedback-warning'), true);
  assert.equal(ui.element('submitBtn').disabled, false);
});

test('obsolete uploads cannot overwrite a replacement selection', async () => {
  let finishFirst;
  const ui = await setup({
    manifest: uploadManifest,
    request: async (options, url) => {
      if (url === '/uploads') {
        if (options.body.get('file').name === 'first.csv') return new Promise(resolve => { finishFirst = resolve; });
        return reply({path: '/data/uploads/second.csv'});
      }
      return reply({kind: 'table', feature_names: ['second'], message: 'Review columns.'});
    },
  });
  const first = ui.select(dataField, {name: 'first.csv'});
  await ui.select(dataField, {name: 'second.csv'});
  finishFirst(reply({path: '/data/uploads/first.csv'}));
  await first;
  assert.equal(JSON.parse(ui.element('field-config_json').value).data_path, '/data/uploads/second.csv');
});

test('reset prevents pending upload results from repopulating the configuration', async () => {
  let finish;
  const ui = await setup({manifest: uploadManifest, request: () => new Promise(resolve => { finish = resolve; })});
  const pending = ui.select(dataField, {name: 'data.csv'});
  ui.reset();
  finish(reply({path: '/data/uploads/stale.csv'}));
  await pending;
  assert.equal(ui.element('field-config_json').value, '{}');
  assert.equal(ui.element('submitBtn').disabled, false);
  assert.equal(ui.element('fileSetupFeedback').hidden, true);
});

test('reset clears uploads and configuration without returning to the assistant', async () => {
  const ui = await setup({
    manifest: {...uploadManifest, features: {assistant: true, upload_inspection: true}},
    request: async (_, url) => url === '/uploads'
      ? reply({path: '/data/uploads/data.csv'})
      : reply({kind: 'table', feature_names: ['Age'], message: 'Review columns.'}),
  });
  ui.element('field-config_json').value = '{"mode":"fast"}';
  await ui.select(dataField, {name: 'data.csv'});
  ui.reset();
  assert.deepEqual(ui.element('field-data_upload').files, []);
  assert.equal(ui.element('field-config_json').value, '{}');
  assert.equal(ui.element('assistantStep').hidden, true);
  assert.equal(ui.element('workflowForm').hidden, false);
  assert.equal(ui.element('backToAssistantBtn').hidden, false);
  assert.equal(ui.element('fileSetupFeedback').hidden, true);
  assert.equal(ui.element('status').textContent, 'Inputs cleared.');
  await ui.finishUploads();
  assert.equal(ui.element('field-config_json').value, '{}');
});

test('show_when fields follow the configuration and an invalid configuration leaves them alone', async () => {
  const conditional = {name: 'extra', type: 'file', show_when: {config_key: 'mode', values: ['deep']}};
  const ui = await setup({manifest: {inputs: [conditional, configField]}});
  const container = {hidden: false};
  ui.element('field-extra').closest = () => container;
  for (const [mode, hidden] of [['deep', false], ['fast', true], ['', true]]) {
    ui.element('field-config_json').value = JSON.stringify({mode});
    ui.updateFieldVisibility();
    assert.equal(container.hidden, hidden, mode);
  }
  ui.element('field-config_json').value = '{"mode":"deep"}';
  ui.updateFieldVisibility();
  ui.element('field-config_json').value = '{';
  ui.updateFieldVisibility();
  assert.equal(container.hidden, false);
});
