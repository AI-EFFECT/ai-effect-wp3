// Explainability plugin running inside the shared workflow UI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {setup: setupUi, reply} = require('../../../../workflow-ui/tests/harness.cjs');

const useCaseDir = path.resolve(__dirname, '../..');
const realManifest = JSON.parse(fs.readFileSync(path.join(useCaseDir, 'ui.json'), 'utf8'));
const plugin = path.join(useCaseDir, 'ui', realManifest.plugins[0]);
const inputs = Object.fromEntries(realManifest.inputs.map(field => [field.name, field]));
const modelField = inputs.model_upload;
const datasetField = inputs.dataset_upload;
const tensorField = inputs.test_data_upload;

function setup(request, manifestInputs = realManifest.inputs) {
  return setupUi({
    manifest: {...realManifest, inputs: manifestInputs, features: {assistant: true, upload_inspection: true}},
    plugins: [plugin],
    request,
  });
}

test('the manifest declares the plugin and labels both explainability steps', () => {
  assert.deepEqual(realManifest.plugins, ['explainability.js']);
  assert.deepEqual(realManifest.steps.map(step => step.method), ['ConfigureAnalysis', 'Explain']);
  assert.ok(fs.existsSync(plugin));
});

test('model-specific uploads follow every supported model and reset to hidden', async () => {
  const ui = await setup();
  const background = {hidden: false};
  const tensors = {hidden: false};
  ui.element('field-background_data_upload').closest = () => background;
  ui.element('field-test_data_upload').closest = () => tensors;
  for (const model of ['', 'random_forest', 'xgboost', 'arima', 'lstm', 'feedforward', 'mlp', 'neural_net']) {
    ui.element('field-config_json').value = JSON.stringify({model_type: model});
    ui.updateFieldVisibility();
    assert.equal(tensors.hidden, model !== 'lstm', model);
    assert.equal(background.hidden, !['lstm', 'feedforward', 'mlp', 'neural_net'].includes(model), model);
  }
  ui.element('field-config_json').value = '{';
  ui.updateFieldVisibility();
  assert.equal(background.hidden, false);
  ui.reset();
  assert.equal(background.hidden, true);
  assert.equal(tensors.hidden, true);
});

test('uploads add placeholders, suggest dataset columns, and ask for the model type', async () => {
  const ui = await setup(async (options, url) => url === '/uploads'
    ? reply({path: `/data/uploads/${options.body.get('file').name}`})
    : reply({kind: 'table', feature_names: ['Age', 'Income'], message: 'Review target columns.'}));
  await ui.select(modelField, {name: 'model.pkl'});
  assert.deepEqual(JSON.parse(ui.element('field-config_json').value), {
    analysis: '', model_type: '', model_path: '/data/uploads/model.pkl',
  });
  await ui.select(datasetField, {name: 'data.csv'});
  const config = JSON.parse(ui.element('field-config_json').value);
  assert.equal(config.dataset_path, '/data/uploads/data.csv');
  assert.deepEqual(config.feature_names, ['Age', 'Income']);
  const feedback = ui.element('fileSetupFeedback');
  assert.match(feedback.textContent, /^Attention:/);
  assert.match(feedback.textContent, /Specify model_type/);
  assert.equal(feedback.classList.contains('file-setup-feedback-warning'), true);
  assert.equal(feedback.querySelector('details'), null);
});

test('placeholders never overwrite provided settings', async () => {
  const ui = await setup(async () => reply({path: '/data/uploads/model.pth'}));
  ui.element('field-config_json').value = '{"analysis":"timeseries","model_type":"lstm","hidden_size":32}';
  await ui.select(modelField, {name: 'model.pth'});
  assert.deepEqual(JSON.parse(ui.element('field-config_json').value), {
    analysis: 'timeseries', model_type: 'lstm', hidden_size: 32, model_path: '/data/uploads/model.pth',
  });
});

test('suggestions replace earlier suggestions but preserve names the user typed', async () => {
  let count = 0;
  const ui = await setup(async (_, url) => url === '/uploads'
    ? reply({path: `/data/uploads/${++count}.csv`})
    : reply({kind: 'table', feature_names: [`column${count}`], message: 'Review columns.'}));
  await ui.select(datasetField, {name: 'first.csv'});
  await ui.select(datasetField, {name: 'second.csv'});
  assert.deepEqual(JSON.parse(ui.element('field-config_json').value).feature_names, ['column2']);
  ui.element('field-config_json').value = '{"feature_names":["my feature"]}';
  await ui.select(datasetField, {name: 'third.csv'});
  assert.deepEqual(JSON.parse(ui.element('field-config_json').value).feature_names, ['my feature']);
});

test('removing the file that supplied suggestions removes them too', async () => {
  const ui = await setup(async (_, url) => url === '/uploads'
    ? reply({path: '/data/uploads/data.csv'})
    : reply({kind: 'table', feature_names: ['Age'], message: 'Review columns.'}));
  ui.element('field-config_json').value = '{"model_type":"random_forest"}';
  await ui.select(datasetField, {name: 'data.csv'});
  await ui.select(datasetField, null);
  const config = JSON.parse(ui.element('field-config_json').value);
  assert.equal(config.feature_names, undefined);
  assert.equal(config.dataset_path, undefined);
});

test('LSTM tensors supply positional labels without guessing the model algorithm', async () => {
  const ui = await setup(async (_, url) => url === '/uploads'
    ? reply({path: '/data/uploads/data.pt'})
    : reply({kind: 'tensor', shape: [4, 6, 2], feature_names: ['f0', 'f1'], message: 'Positional labels.'}));
  ui.element('field-config_json').value = '{"analysis":"timeseries","model_type":"lstm"}';
  await ui.select(tensorField, {name: 'data.pt'});
  assert.deepEqual(JSON.parse(ui.element('field-config_json').value).feature_names, ['f0', 'f1']);
  assert.match(ui.element('fileSetupFeedback').textContent, /^File information:/);
  assert.equal(ui.element('fileSetupFeedback').classList.contains('file-setup-feedback-warning'), false);
});

test('file notes separate upload roles and dimensions and deduplicate guidance safely', async () => {
  const message = 'Positional labels; provide training feature names.';
  const filename = '<img src=x onerror=alert(1)>.pt';
  const ui = await setup(async (options, url) => url === '/uploads'
    ? reply({path: `/data/uploads/${options.body.get('file').name}`})
    : reply({kind: 'tensor', shape: [50, 6, 12], feature_names: ['f0'], message}));
  ui.element('field-config_json').value = '{"model_type":"lstm"}';
  await ui.select(datasetField, {name: filename});
  await ui.select(tensorField, {name: 'test.pt'});
  const feedback = ui.element('fileSetupFeedback');
  const details = feedback.children[0];
  assert.equal(details.tagName, 'details');
  assert.equal(details.open, false);
  const list = details.children.find(child => child.className === 'file-feedback-list');
  assert.equal(list.children.length, 2);
  assert.equal(list.children[0].children[0].textContent, 'Dataset file');
  assert.equal(list.children[1].children[0].textContent, 'LSTM test data');
  assert.equal(list.children[0].children[1].textContent, filename);
  assert.equal(list.children[0].children[1].innerHTML, undefined);
  assert.equal(list.children[0].children[2].textContent, '50 samples / 6 time steps / 12 features');
  assert.equal(feedback.textContent.split(message).length - 1, 1);
  details.open = true;
  await ui.select(datasetField, null);
  assert.equal(feedback.children[0].open, true);
  await ui.select(tensorField, null);
  assert.equal(feedback.hidden, true);
  assert.equal(feedback.children.length, 0);
});

test('submission requires the files each analysis needs', async () => {
  const submitted = [];
  const ui = await setup(async (options, url) => {
    if (url === '/submit') submitted.push(JSON.parse(options.body));
    return reply({workflow_id: 'wf', status: 'running', outputs: []});
  });
  ui.element('field-config_json').value = '{"analysis":"tabular","model_path":"/assets/models/m.joblib"}';
  await ui.submitWorkflow();
  assert.equal(ui.element('status').textContent, 'Add or upload dataset_path before running this analysis.');
  assert.equal(submitted.length, 0);
});

test('LSTM settings must match the uploaded tensor shape', async () => {
  const ui = await setup(async (options, url) => {
    if (url === '/uploads') return reply({path: `/data/uploads/${options.body.get('file').name}`});
    if (url === '/uploads/inspect') return reply({kind: 'tensor', shape: [50, 6, 12], feature_names: [], message: 'Tensor.'});
    return reply({workflow_id: 'wf', status: 'running', outputs: []});
  });
  ui.element('field-config_json').value = JSON.stringify({
    analysis: 'timeseries', model_type: 'lstm', model_path: '/m.pth', background_data_path: '/b.pt', look_back: 5,
  });
  await ui.select(tensorField, {name: 'test.pt'});
  await ui.submitWorkflow();
  assert.equal(ui.element('status').textContent, 'look_back must be 6 to match test.pt.');
});

test('the assistant hint explains model types only while model_type is unknown', async () => {
  const ui = await setup(async () => reply({status: 'clarification', message: 'Which model type?', config: {}}));
  assert.match(ui.element('assistantHint').textContent, /Random Forest, XGBoost/);
  ui.element('assistantPrompt').value = 'Explain my model';
  await ui.submitAssistant();
  assert.equal(ui.element('assistantHint').hidden, false);
});
