// Every use case's ui.json must be valid and consistent with its workflow.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {setup, reply} = require('./harness.cjs');

const useCasesDir = path.resolve(__dirname, '../../use-cases');
const manifests = fs.readdirSync(useCasesDir)
  .map(name => path.join(useCasesDir, name))
  .filter(dir => fs.existsSync(path.join(dir, 'ui.json')));

test('use cases with a ui.json are found', () => {
  const names = manifests.map(dir => path.basename(dir));
  for (const expected of ['ai-explainability', 'denmark-node']) assert.ok(names.includes(expected), expected);
});

for (const dir of manifests) {
  const name = path.basename(dir);

  test(`${name}: ui.json is valid and consistent with connections.json`, () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'ui.json'), 'utf8'));
    assert.equal(typeof manifest.title, 'string');
    assert.ok(Array.isArray(manifest.inputs) && manifest.inputs.length, 'at least one input');

    const connections = JSON.parse(fs.readFileSync(path.join(dir, 'connections.json'), 'utf8'));
    const methods = new Set(connections.pipeline.connections.flatMap(link => [link.from_method, link.to_method]));
    for (const step of manifest.steps || []) {
      assert.ok(methods.has(step.method), `step ${step.method} is not a method in connections.json`);
      assert.ok(step.title, `step ${step.method} has a title`);
      if (step.gate) assert.ok(step.gate.actions?.length, `gate on ${step.method} has actions`);
    }
    for (const plugin of manifest.plugins || []) {
      assert.match(plugin, /^[A-Za-z0-9_-]+\.js$/);
      assert.ok(fs.existsSync(path.join(dir, 'ui', plugin)), `plugin ui/${plugin} exists`);
    }
    for (const field of manifest.inputs) {
      if (field.show_when) {
        assert.equal(typeof field.show_when.config_key, 'string', `${field.name} show_when.config_key`);
        assert.ok(Array.isArray(field.show_when.values), `${field.name} show_when.values`);
      }
      if (field.type === 'file') assert.ok(field.config_field && field.upload_category, `${field.name} upload settings`);
    }
  });
}

test('denmark-node: confirming records the recommended topology, not the first drawing', async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(useCasesDir, 'denmark-node/ui.json'), 'utf8'));
  const bodies = [];
  const ui = await setup({
    manifest,
    request: async (options, url) => {
      if (url === '/decision') bodies.push(JSON.parse(options.body));
      return reply({status: 'accepted', value: 'B-best'});
    },
  });
  // Payload shapes as produced by the topology_generator and draw_topology services.
  ui.renderOutputs({
    workflow_id: 'wf',
    status: 'completed',
    tasks: [
      {task_id: 'gen', node_key: 'topology_generator1:GenerateTopology', status: 'completed'},
      {task_id: 'draw', node_key: 'draw_topology1:RenderTopologies', status: 'completed'},
    ],
    outputs: [
      {task_id: 'gen', payload: {networks: [{signature: 'A-first'}], recommended_network: {signature: 'B-best'}}},
      {task_id: 'draw', payload: {
        drawings: [{signature: 'A-first'}, {signature: 'B-best'}],
        recommended_network: {signature: 'B-best'},
        report: {recommended_network: 'B-best'},
      }},
    ],
  });
  assert.match(ui.element('output').innerHTML, /data-action="accepted" data-step="RenderTopologies">Confirm topology/);
  ui.run('workflowId = "wf"');
  await ui.run('recordDecision("accepted", "RenderTopologies")');
  assert.equal(bodies[0].value, 'B-best');
});
