const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const uiDir = path.resolve(__dirname, '..');
const script = fs.readFileSync(path.join(uiDir, 'static/app.js'), 'utf8');

async function setup(request, inputs = []) {
  const elements = new Map();
  const timers = new Map();
  let timerId = 0;
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        value: '',
        textContent: '',
        hidden: false,
        disabled: false,
        attributes: {},
        children: [],
        classList: {
          values: new Set(),
          toggle(name, enabled) { if (enabled) this.values.add(name); else this.values.delete(name); },
          add(name) { this.values.add(name); },
          remove(name) { this.values.delete(name); },
          contains(name) { return this.values.has(name); },
        },
        addEventListener() {},
        querySelectorAll() { return []; },
        setAttribute(key, value) { this.attributes[key] = value; },
        removeAttribute(key) { delete this.attributes[key]; },
        append(child) { this.children.push(child); },
        focus() { this.focused = true; },
        reset() {},
        replaceChildren() { this.children = []; },
      });
    }
    return elements.get(id);
  }
  const context = vm.createContext({
    document: {
      getElementById: element,
      createElement: () => element(`message-${elements.size}`),
      documentElement: {style: {setProperty() {}}},
      body: {classList: {add() {}}},
    },
    fetch: async (url, options) => url === '/ui/config'
      ? {ok: true, json: async () => ({assistant: {enabled: true}, inputs})}
      : request(options, url),
    FormData: class {
      values = new Map();
      append(key, value) { this.values.set(key, value); }
      get(key) { return this.values.get(key); }
    },
    setTimeout(callback, delay) {
      timers.set(++timerId, {callback, delay});
      return timerId;
    },
    clearTimeout: id => timers.delete(id),
    AbortController,
    console,
  });
  vm.runInContext(script, context);
  await new Promise(resolve => setImmediate(resolve));
  element('assistantPrompt').value = 'Explain my LSTM model';
  return {
    element,
    timers,
    submit: () => vm.runInContext('previewAnalysisSetup({preventDefault() {}})', context),
    renderArtifacts: payload => {
      context.artifactPayload = payload;
      return vm.runInContext('renderArtifacts("workflow", artifactPayload, "task")', context);
    },
    select: (field, file) => {
      element(`field-${field.name}`).files = file ? [file] : [];
      context.selectedField = field;
      return vm.runInContext('updateSelectedFile(selectedField)', context);
    },
    finishUploads: () => vm.runInContext('uploadConfiguredFiles()', context),
    reset: () => vm.runInContext('resetWorkflow()', context),
  };
}

test('status is outside both forms so neither setup step hides feedback', () => {
  const template = fs.readFileSync(path.join(uiDir, 'templates/index.html'), 'utf8');
  assert.match(template, /<\/form>\s*<div[^>]*id="status"/);
  assert.match(template, /id="status"[^>]*aria-live="polite"/);
});

test('assistant output does not invent report downloads', async () => {
  const ui = await setup();
  assert.equal(ui.renderArtifacts({status: 'ready', config: {}}), '');
  assert.match(ui.renderArtifacts({artifacts: ['report.md']}), /report\.md/);
});

test('pending request shows feedback, spinner, and long-wait message', async () => {
  let finish;
  const ui = await setup(() => new Promise(resolve => { finish = resolve; }));
  const pending = ui.submit();
  assert.equal(ui.element('workflowForm').hidden, true);
  assert.match(ui.element('status').textContent, /Analysis has not started/);
  assert.match(ui.element('assistantSubmitBtn').innerHTML, /spinner-border/);
  assert.equal(ui.element('activityLabel').textContent, 'Checking request');
  assert.equal(ui.element('skipAssistantBtn').disabled, true);
  [...ui.timers.values()].find(timer => timer.delay === 15000).callback();
  assert.match(ui.element('status').textContent, /Still waiting/);
  finish({ok: true, json: async () => ({status: 'ready', message: 'Supported.', config: {}})});
  await pending;
  assert.equal(ui.element('assistantContinueBtn').hidden, false);
  assert.equal(ui.element('assistantSubmitBtn').textContent, 'Check my request');
  assert.equal(ui.element('skipAssistantBtn').disabled, false);
  assert.equal(ui.element('assistantStep').attributes['aria-busy'], 'false');
  assert.equal(ui.timers.size, 0);
});

test('clarification visibly requests more information and restores input focus', async () => {
  const ui = await setup(async () => ({
    ok: true,
    json: async () => ({status: 'clarification', message: 'Which model type?'}),
  }));
  await ui.submit();
  assert.match(ui.element('status').textContent, /more information/);
  assert.equal(ui.element('assistantPrompt').focused, true);
  assert.equal(ui.element('assistantContinueBtn').hidden, true);
  assert.equal(ui.element('modelTypeHint').hidden, false);
  assert.equal(ui.element('assistantPrompt').attributes['aria-describedby'], 'modelTypeHint');
});

test('model guidance is hidden initially and after a successful revision', async () => {
  let status = 'clarification';
  const ui = await setup(async () => ({
    ok: true,
    json: async () => ({status, message: 'Setup response.', config: status === 'ready' ? {model_type: 'lstm'} : {}}),
  }));
  assert.equal(ui.element('modelTypeHint').hidden, true);
  assert.equal(ui.element('assistantPrompt').attributes['aria-describedby'], undefined);
  await ui.submit();
  assert.equal(ui.element('modelTypeHint').hidden, false);
  status = 'ready';
  ui.element('assistantPrompt').value = 'LSTM';
  await ui.submit();
  assert.equal(ui.element('modelTypeHint').hidden, true);
  assert.equal(ui.element('assistantPrompt').attributes['aria-describedby'], undefined);
});

test('clarification with a known model does not display model-type guidance', async () => {
  const ui = await setup(async () => ({
    ok: true,
    json: async () => ({status: 'clarification', message: 'Which analysis?', config: {model_type: 'lstm'}}),
  }));
  await ui.submit();
  assert.equal(ui.element('modelTypeHint').hidden, true);
});

test('service errors remain visible and restore controls', async () => {
  const ui = await setup(async () => ({
    ok: false,
    json: async () => ({detail: 'The setup assistant is unavailable.'}),
  }));
  await ui.submit();
  assert.match(ui.element('status').textContent, /unavailable/);
  assert.equal(ui.element('activityLabel').textContent, 'Needs attention');
  assert.equal(ui.element('assistantSubmitBtn').disabled, false);
  assert.equal(ui.timers.size, 0);
});

test('timeout explains the delay and offers manual setup', async () => {
  const ui = await setup(options => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => {
      const error = new Error('Aborted');
      error.name = 'AbortError';
      reject(error);
    });
  }));
  const pending = ui.submit();
  [...ui.timers.values()].find(timer => timer.delay === 210000).callback();
  await pending;
  assert.match(ui.element('status').textContent, /too long.*manual setup/);
  assert.equal(ui.element('skipAssistantBtn').disabled, false);
  assert.equal(ui.timers.size, 0);
});

const datasetField = {
      name: 'dataset_upload', type: 'file',
      config_field: 'dataset_path', upload_category: 'data',
    };
    const modelField = {
      name: 'model_upload', type: 'file',
      config_field: 'model_path', upload_category: 'models',
    };
    const tensorField = {
      name: 'test_data_upload', type: 'file',
      config_field: 'test_data_path', upload_category: 'data',
    };
    const reply = data => ({ok: true, json: async () => data});

    test('selection updates paths and headers immediately without reuploading at submission', async () => {
      let uploads = 0;
      const ui = await setup(async (options, url) => {
        if (url === '/uploads') {
          uploads += 1;
          return reply({path: `/data/uploads/${options.body.get('file').name}`});
        }
        return reply({kind: 'table', feature_names: ['Age', 'Income'], message: 'Review target columns.'});
      }, [modelField, datasetField]);
      ui.element('field-config_json').value = '{}';
      await ui.select(modelField, {name: 'model.pkl'});
      await ui.select(datasetField, {name: 'data.csv'});
      const config = JSON.parse(ui.element('field-config_json').value);
      assert.equal(config.model_path, '/data/uploads/model.pkl');
      assert.equal(config.dataset_path, '/data/uploads/data.csv');
      assert.deepEqual(config.feature_names, ['Age', 'Income']);
      assert.equal(config.model_type, undefined);
      assert.match(ui.element('fileSetupFeedback').textContent, /Specify model_type/);
      assert.match(ui.element('fileSetupFeedback').textContent, /^Attention:/);
      assert.equal(ui.element('fileSetupFeedback').classList.contains('file-setup-feedback-warning'), true);
      await ui.finishUploads();
      assert.equal(uploads, 2);
    });

    test('file suggestions preserve manual names but replace previously automatic names', async () => {
      let count = 0;
      const ui = await setup(async (options, url) => url === '/uploads'
        ? reply({path: `/data/uploads/${++count}.csv`})
        : reply({kind: 'table', feature_names: [`column${count}`], message: 'Review columns.'}),
      [datasetField]);
      ui.element('field-config_json').value = '{}';
      await ui.select(datasetField, {name: 'first.csv'});
      await ui.select(datasetField, {name: 'second.csv'});
      assert.deepEqual(JSON.parse(ui.element('field-config_json').value).feature_names, ['column2']);
      ui.element('field-config_json').value = '{"feature_names":["my feature"]}';
      await ui.select(datasetField, {name: 'third.csv'});
      assert.deepEqual(JSON.parse(ui.element('field-config_json').value).feature_names, ['my feature']);
    });

    test('LSTM tensors supply positional labels without guessing the model algorithm', async () => {
      const ui = await setup(async (options, url) => url === '/uploads'
        ? reply({path: '/data/uploads/data.pt'})
        : reply({kind: 'tensor', shape: [4, 6, 2], feature_names: ['f0', 'f1'], message: 'Positional labels.'}),
      [tensorField]);
      ui.element('field-config_json').value = '{"analysis":"timeseries","model_type":"lstm"}';
      await ui.select(tensorField, {name: 'data.pt'});
      assert.deepEqual(JSON.parse(ui.element('field-config_json').value).feature_names, ['f0', 'f1']);
      assert.match(ui.element('fileSetupFeedback').textContent, /Positional/);
      assert.match(ui.element('fileSetupFeedback').textContent, /^File information:/);
      assert.equal(ui.element('fileSetupFeedback').classList.contains('file-setup-feedback-warning'), false);
    });

    test('inspection failures retain the uploaded path and display manual-review feedback', async () => {
      const ui = await setup(async (options, url) => url === '/uploads'
        ? reply({path: '/data/uploads/data.pt'})
        : {ok: false, json: async () => ({detail: 'Invalid tensor dimensions.'})},
      [tensorField]);
      ui.element('field-config_json').value = '{"model_type":"lstm"}';
      await ui.select(tensorField, {name: 'invalid.pt'});
      assert.equal(JSON.parse(ui.element('field-config_json').value).test_data_path, '/data/uploads/data.pt');
      assert.match(ui.element('status').textContent, /feature detection failed/);
      assert.match(ui.element('fileSetupFeedback').textContent, /Invalid tensor dimensions/);
      assert.equal(ui.element('fileSetupFeedback').classList.contains('file-setup-feedback-warning'), true);
      assert.equal(ui.element('submitBtn').disabled, false);
    });

    test('obsolete uploads cannot overwrite a replacement selection', async () => {
      let finishFirst;
      const ui = await setup(async (options, url) => {
        if (url === '/uploads') {
          if (options.body.get('file').name === 'first.csv') {
            return new Promise(resolve => { finishFirst = resolve; });
          }
          return reply({path: '/data/uploads/second.csv'});
        }
        return reply({kind: 'table', feature_names: ['second'], message: 'Review columns.'});
      }, [datasetField]);
      ui.element('field-config_json').value = '{}';
      const first = ui.select(datasetField, {name: 'first.csv'});
      await ui.select(datasetField, {name: 'second.csv'});
      finishFirst(reply({path: '/data/uploads/first.csv'}));
      await first;
      assert.equal(JSON.parse(ui.element('field-config_json').value).dataset_path, '/data/uploads/second.csv');
    });

    test('reset prevents pending upload results from repopulating the configuration', async () => {
      let finish;
      const ui = await setup(() => new Promise(resolve => { finish = resolve; }), [datasetField]);
      ui.element('field-config_json').value = '{}';
      const pending = ui.select(datasetField, {name: 'data.csv'});
      ui.reset();
      finish(reply({path: '/data/uploads/stale.csv'}));
      await pending;
      assert.equal(ui.element('field-config_json').value, '{}');
      assert.equal(ui.element('submitBtn').disabled, false);
      assert.equal(ui.element('fileSetupFeedback').hidden, true);
    });
