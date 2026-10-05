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
        _textContent: '',
        get textContent() { return this._textContent + this.children.map(child => child.textContent).join(''); },
        set textContent(value) { this._textContent = value; this.children = []; },
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
        closest() { return null; },
        querySelector(tag) { return this.children.find(child => child.tagName === tag) || null; },
        setAttribute(key, value) { this.attributes[key] = value; },
        removeAttribute(key) { delete this.attributes[key]; },
        append(...children) { this.children.push(...children); },
        focus() { this.focused = true; },
        reset() {
          if (id === 'workflowForm') {
            for (const field of inputs) {
              const control = element(`field-${field.name}`);
              if (field.type === 'file') control.files = [];
              else control.value = field.default ?? '';
            }
          }
        },
        replaceChildren(...children) { this._textContent = ''; this.children = children; },
      });
    }
    return elements.get(id);
  }
  const context = vm.createContext({
    document: {
      getElementById: element,
      createElement: tag => {
        const result = element(`message-${elements.size}`);
        result.tagName = tag;
        return result;
      },
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
    fieldHtml: field => {
      context.testField = field;
      return vm.runInContext('fieldHtml(testField)', context);
    },
    updateModelFields: () => vm.runInContext('updateModelFields()', context),
    loadPreviews: previews => {
      element('output').querySelectorAll = () => previews;
      return vm.runInContext('loadArtifactPreviews()', context);
    },
  };
}

test('status is outside both forms so neither setup step hides feedback', () => {
  const template = fs.readFileSync(path.join(uiDir, 'templates/index.html'), 'utf8');
  assert.match(template, /<\/form>\s*<div[^>]*id="status"/);
  assert.match(template, /id="status"[^>]*aria-live="polite"/);
  assert.doesNotMatch(template, /class="rail-intro"/);
});

test('field guidance uses collapsed accessible help instead of always-visible paragraphs', async () => {
  const ui = await setup();
  const html = ui.fieldHtml({name: 'model', label: 'Model file', type: 'file', help: 'Check model type.'});
  assert.match(html, /<details class="field-help"><summary>Help/);
  assert.match(html, /visually-hidden"> for Model file/);
  assert.match(html, /Check model type/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
});

test('model-specific uploads follow every supported model and reset to hidden', async () => {
  const inputs = JSON.parse(fs.readFileSync(path.join(uiDir, '../ui.json'), 'utf8')).inputs;
  const ui = await setup(undefined, inputs);
  const background = {hidden: false};
  const tensors = {hidden: false};
  ui.element('field-background_data_upload').closest = () => background;
  ui.element('field-test_data_upload').closest = () => tensors;
  for (const model of ['', 'random_forest', 'xgboost', 'arima', 'lstm', 'feedforward', 'mlp', 'neural_net']) {
    ui.element('field-config_json').value = JSON.stringify({model_type: model});
    ui.updateModelFields();
    assert.equal(tensors.hidden, model !== 'lstm', model);
    assert.equal(background.hidden, !['lstm', 'feedforward', 'mlp', 'neural_net'].includes(model), model);
  }
  ui.element('field-config_json').value = '{';
  ui.updateModelFields();
  assert.equal(background.hidden, false);
  ui.reset();
  assert.equal(background.hidden, true);
  assert.equal(tensors.hidden, true);
});

test('assistant output does not invent report downloads', async () => {
  const ui = await setup();
  assert.equal(ui.renderArtifacts({status: 'ready', config: {}}), '');
  assert.match(ui.renderArtifacts({artifacts: ['report.md']}), /report\.md/);
});

test('previews fetch each producing task rather than the first workflow task', async () => {
  const urls = [];
  const ui = await setup(async (_, url) => {
    urls.push(url);
    return {ok: true, text: async () => url.endsWith('.csv') ? 'Age,Income\n30,100' : '# Explanation'};
  });
  const html = ui.renderArtifacts({artifacts: ['report.md']});
  assert.match(html, /data-artifact-url="\/workflows\/workflow\/files\/task\/report.md"/);
  const previews = [
    {dataset: {artifactPath: 'report.md', artifactUrl: '/workflows/workflow/files/explainer/report.md'}},
    {dataset: {artifactPath: 'explanation.csv', artifactUrl: '/workflows/workflow/files/other/explanation.csv'}},
  ];
  await ui.loadPreviews(previews);
  assert.deepEqual(urls, previews.map(preview => preview.dataset.artifactUrl));
  assert.match(previews[0].innerHTML, /Explanation/);
  assert.match(previews[1].innerHTML, /<table/);
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
      name: 'dataset_upload', label: 'Dataset file', type: 'file',
      config_field: 'dataset_path', upload_category: 'data',
    };
    const modelField = {
      name: 'model_upload', type: 'file',
      config_field: 'model_path', upload_category: 'models',
    };
    const tensorField = {
      name: 'test_data_upload', label: 'LSTM test data', type: 'file',
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
      assert.deepEqual(JSON.parse(ui.element('field-config_json').value), {
        analysis: '', model_type: '', model_path: '/data/uploads/model.pkl',
      });
      await ui.select(datasetField, {name: 'data.csv'});
      const config = JSON.parse(ui.element('field-config_json').value);
      assert.equal(config.model_path, '/data/uploads/model.pkl');
      assert.equal(config.dataset_path, '/data/uploads/data.csv');
      assert.deepEqual(config.feature_names, ['Age', 'Income']);
      assert.equal(config.model_type, '');
      assert.equal(config.analysis, '');
      assert.match(ui.element('fileSetupFeedback').textContent, /Specify model_type/);
      assert.match(ui.element('fileSetupFeedback').textContent, /^Attention:/);
      assert.equal(ui.element('fileSetupFeedback').classList.contains('file-setup-feedback-warning'), true);
      assert.equal(ui.element('fileSetupFeedback').querySelector('details'), null);
      await ui.finishUploads();
      assert.equal(uploads, 2);
    });

    test('upload placeholders preserve provided settings and are not added after failed uploads', async () => {
      const ui = await setup(async () => reply({path: '/data/uploads/model.pth'}), [modelField]);
      ui.element('field-config_json').value = '{"analysis":"timeseries","model_type":"lstm","hidden_size":32}';
      await ui.select(modelField, {name: 'model.pth'});
      assert.deepEqual(JSON.parse(ui.element('field-config_json').value), {
        analysis: 'timeseries', model_type: 'lstm', hidden_size: 32, model_path: '/data/uploads/model.pth',
      });
      const failed = await setup(async () => ({ok: false, json: async () => ({detail: 'Upload failed.'})}), [modelField]);
      failed.element('field-config_json').value = '{}';
      await failed.select(modelField, {name: 'model.pth'});
      assert.equal(failed.element('field-config_json').value, '{}');
      assert.equal(failed.element('status').textContent, 'Upload failed.');
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

    test('file notes separate upload roles and dimensions and deduplicate guidance safely', async () => {
      const message = 'Positional labels; provide training feature names.';
      const filename = '<img src=x onerror=alert(1)>.pt';
      const ui = await setup(async (options, url) => url === '/uploads'
        ? reply({path: `/data/uploads/${options.body.get('file').name}`})
        : reply({kind: 'tensor', shape: [50, 6, 12], feature_names: ['f0'], message}),
      [datasetField, tensorField]);
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

    test('reset clears completed uploads and configuration without returning to the assistant', async () => {
      const configField = {name: 'config_json', type: 'textarea', default: '{}'};
      const ui = await setup(async (options, url) => url === '/uploads'
        ? reply({path: '/data/uploads/data.csv'})
        : reply({kind: 'table', feature_names: ['Age'], message: 'Review columns.'}),
      [datasetField, configField]);
      ui.element('field-config_json').value = '{"model_type":"random_forest"}';
      await ui.select(datasetField, {name: 'data.csv'});
      ui.reset();
      assert.deepEqual(ui.element('field-dataset_upload').files, []);
      assert.equal(ui.element('field-config_json').value, '{}');
      assert.equal(ui.element('assistantStep').hidden, true);
      assert.equal(ui.element('workflowForm').hidden, false);
      assert.equal(ui.element('backToAssistantBtn').hidden, false);
      assert.equal(ui.element('fileSetupFeedback').hidden, true);
      assert.equal(ui.element('status').textContent, 'Uploads and configuration cleared.');
      await ui.finishUploads();
      assert.equal(ui.element('field-config_json').value, '{}');
    });
