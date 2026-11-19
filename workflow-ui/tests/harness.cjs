// Test harness: runs the shared UI script (and optional plugins) in a VM with a
// minimal DOM stand-in. Workflow plugin tests reuse it via require().

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const uiDir = path.resolve(__dirname, '..');
const coreScript = fs.readFileSync(path.join(uiDir, 'static/app.js'), 'utf8');
const reply = data => ({ok: true, json: async () => data});

// manifest: what /ui/config returns. request(options, url): every other fetch.
// plugins: paths of plugin scripts, loaded before the core script.
async function setup({manifest = {}, request, plugins = []} = {}) {
  const inputs = manifest.inputs || [];
  const elements = new Map();
  const timers = new Map();
  let timerId = 0;
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id,
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
        const result = element(`created-${elements.size}`);
        result.tagName = tag;
        return result;
      },
      documentElement: {style: {setProperty() {}}},
      body: {classList: {add() {}}},
    },
    fetch: async (url, options) => url === '/ui/config'
      ? reply(manifest)
      : (request ? request(options, url) : reply({})),
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
  for (const plugin of plugins) vm.runInContext(fs.readFileSync(plugin, 'utf8'), context);
  vm.runInContext(coreScript, context);
  await new Promise(resolve => setImmediate(resolve));
  for (const field of inputs) {
    if (field.default !== undefined) element(`field-${field.name}`).value = field.default;
  }

  const run = (code, values = {}) => {
    Object.assign(context, values);
    return vm.runInContext(code, context);
  };
  return {
    element,
    timers,
    run,
    submitAssistant: () => run('previewSetup({preventDefault() {}})'),
    submitWorkflow: () => run('submitWorkflow({preventDefault() {}})'),
    renderArtifacts: payload => run('renderArtifacts("workflow", testPayload, "task")', {testPayload: payload}),
    renderOutputs: status => run('renderOutputs(testStatus)', {testStatus: status}),
    select: (field, file) => {
      element(`field-${field.name}`).files = file ? [file] : [];
      return run('updateSelectedFile(testField)', {testField: field});
    },
    finishUploads: () => run('uploadConfiguredFiles()'),
    reset: () => run('resetWorkflow()'),
    fieldHtml: field => run('fieldHtml(testField)', {testField: field}),
    updateFieldVisibility: () => run('updateFieldVisibility()'),
    loadPreviews: previews => {
      element('output').querySelectorAll = () => previews;
      return run('loadArtifactPreviews()');
    },
  };
}

module.exports = {setup, reply, uiDir};
