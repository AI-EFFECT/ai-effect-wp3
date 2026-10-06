const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const root = path.resolve(__dirname, '..');

async function setup(fetchRequest) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: 'Use the CHP', hidden: false, disabled: false,
      classList: {toggle() {}, add() {}}, attributes: {},
      setAttribute(key, value) { this.attributes[key] = value; },
      addEventListener() {}, reset() { this.wasReset = true; },
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById: element, documentElement: {style: {setProperty() {}}}, body: {classList: {add() {}}}},
    fetch: (url, options) => url === '/ui/config'
      ? Promise.resolve({ok: true, json: async () => ({
        inputs: [{name: 'prompt', type: 'textarea', help: 'Describe the equipment.'}],
        submit_label: 'Generate topology',
        actions: [{name: 'accepted', label: 'Confirm topology', style: 'primary'}, {name: 'rejected', label: 'Reject topology', style: 'danger'}],
      })}) : fetchRequest(url, options),
    setTimeout: () => 1, clearTimeout() {},
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return {element, run: code => vm.runInContext(code, context)};
}

test('uniform shell and accessible status outside the form', () => {
  const html = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8');
  for (const name of ['app-navbar', 'workspace-grid', 'input-rail', 'results-stage']) assert.ok(html.includes(name));
  assert.match(html, /<\/form>\s*<div[^>]*id="status"[^>]*aria-live="polite"/);
});

test('help and technical payloads are collapsed and topology decisions are preserved', async () => {
  const ui = await setup();
  assert.match(ui.element('fields').innerHTML, /<details class="field-help">/);
  ui.run('renderOutputs({workflow_id:"wf",status:"completed",outputs:[{task_id:"draw",payload:{drawings:[{signature:"topology"}]}}]})');
  assert.match(ui.element('output').innerHTML, /<details class="technical-details">/);
  assert.match(ui.element('output').innerHTML, /assets\/topology/);
  assert.equal(ui.element('actions').hidden, false);
  assert.match(ui.element('actions').innerHTML, /class="btn btn-primary" data-action="accepted">Confirm topology/);
  assert.match(ui.element('actions').innerHTML, /class="btn btn-outline-danger" data-action="rejected">Reject topology/);
});

test('network failure restores submit controls and displays the error', async () => {
  const ui = await setup(async () => { throw new Error('Offline'); });
  await ui.run('submitWorkflow({preventDefault(){}})');
  assert.equal(ui.element('status').textContent, 'Offline');
  assert.equal(ui.element('submitBtn').disabled, false);
  assert.equal(ui.element('workflowForm').attributes['aria-busy'], 'false');
});

test('successful submission displays progress and reveals decisions only on completion', async () => {
  const ui = await setup(async url => ({ok: true, json: async () => url === '/submit'
    ? {workflow_id: 'wf', status: 'running', outputs: []}
    : {workflow_id: 'wf', status: 'completed', outputs: [{payload: {summary: 'Done'}}]}}));
  await ui.run('submitWorkflow({preventDefault(){}})');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.element('status').textContent, 'Workflow completed.');
  assert.equal(ui.element('actions').hidden, false);
  assert.equal(ui.element('submitBtn').disabled, false);
});

test('reset prevents an outstanding submission from restoring stale results', async () => {
  let finish;
  const ui = await setup(() => new Promise(resolve => { finish = resolve; }));
  const pending = ui.run('submitWorkflow({preventDefault(){}})');
  assert.match(ui.element('submitBtn').innerHTML, /spinner-border/);
  assert.equal(ui.element('submitBtn').disabled, true);
  ui.run('resetWorkflow()');
  finish({ok: true, json: async () => ({workflow_id: 'stale', outputs: []})});
  await pending;
  assert.equal(ui.run('workflowId'), null);
  assert.equal(ui.element('actions').hidden, true);
  assert.equal(ui.element('submitBtn').disabled, false);
});
