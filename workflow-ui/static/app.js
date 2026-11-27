// Shared AI-EFFECT workflow UI.
//
// Everything workflow-specific comes from the manifest (/ui/config, built from
// the workflow's ui.json) or from plugins the manifest declares. Plugins are
// loaded before this script and register themselves on
// globalThis.WorkflowUIPlugins; see README.md for the hooks they can implement.

let manifest = null;
let workflowId = null;
let latestStatus = null;
let pollHandle = null;
let requestGeneration = 0;
let workflowSubmitting = false;
let setupConversation = [];
let assistantEnabled = false;
let assistantResult = null;
let selectedUploads = new Map();
let uploadGeneration = 0;
let pendingUploads = 0;

const plugins = globalThis.WorkflowUIPlugins || [];

const fieldsEl = document.getElementById('fields');
const formEl = document.getElementById('workflowForm');
const assistantFormEl = document.getElementById('assistantForm');
const assistantStepEl = document.getElementById('assistantStep');
const assistantTitleEl = document.getElementById('assistantTitle');
const assistantPromptEl = document.getElementById('assistantPrompt');
const assistantHintEl = document.getElementById('assistantHint');
const assistantConversationEl = document.getElementById('assistantConversation');
const assistantSubmitBtn = document.getElementById('assistantSubmitBtn');
const assistantContinueBtn = document.getElementById('assistantContinueBtn');
const skipAssistantBtn = document.getElementById('skipAssistantBtn');
const backToAssistantBtn = document.getElementById('backToAssistantBtn');
const statusEl = document.getElementById('status');
const activityLabelEl = document.getElementById('activityLabel');
const manifestWarningEl = document.getElementById('manifestWarning');
const outputEl = document.getElementById('output');
const timelineEl = document.getElementById('timeline');
const actionsEl = document.getElementById('actions');
const submitBtn = document.getElementById('submitBtn');
const resetBtn = document.getElementById('resetBtn');
const outputTitle = document.getElementById('outputTitle');
const pipelineBadge = document.getElementById('pipelineBadge');
const pageTitle = document.getElementById('pageTitle');
const pageDescription = document.getElementById('pageDescription');
const fileSetupFeedbackEl = document.getElementById('fileSetupFeedback');

const SPINNER = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>';
const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'];
const TEXT_PREVIEW_EXTENSIONS = ['md', 'markdown', 'csv', 'json', 'txt', 'log', 'yaml', 'yml'];

// ---------------------------------------------------------------------------
// Helpers shared with plugins
// ---------------------------------------------------------------------------

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char]));
}

// Recursively search for a key in an object and return its value if found.
function deepFind(value, key) {
  if (!value || typeof value !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(value, key)) return value[key];

  for (const child of Object.values(value)) {
    const found = deepFind(child, key);
    if (found !== undefined) return found;
  }
  return undefined;
}

globalThis.WorkflowUI = {escapeHtml, deepFind};

// Call a hook on every plugin that implements it and collect the results.
function callPlugins(hook, context) {
  return plugins
    .filter(plugin => typeof plugin[hook] === 'function')
    .map(plugin => plugin[hook](context));
}

function fieldByName(name) {
  return (manifest.inputs || []).find(item => item.name === name);
}

function configFieldName() {
  return manifest.upload_config_field || 'config_json';
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------

function fieldHtml(field) {
  const required = field.required ? 'required' : '';
  const placeholder = field.placeholder ? `placeholder="${escapeHtml(field.placeholder)}"` : '';
  const value = field.default ?? '';
  const hint = field.help
    ? `<details class="field-help"><summary>Help<span class="visually-hidden"> for ${escapeHtml(field.label || field.name)}</span></summary><div class="hint">${escapeHtml(field.help)}</div></details>`
    : '';
  const label = `<label class="form-label" for="field-${escapeHtml(field.name)}">${escapeHtml(field.label || field.name)}</label>`;

  let control = '';
  if (field.type === 'select') {
    const options = (field.options || [])
      .map(option => `<option value="${escapeHtml(option.value ?? option)}">${escapeHtml(option.label ?? option)}</option>`)
      .join('');
    control = `<select class="form-select" id="field-${escapeHtml(field.name)}" name="${escapeHtml(field.name)}" ${required}>${options}</select>`;
  } else if (field.type === 'number') {
    control = `<input class="form-control" id="field-${escapeHtml(field.name)}" name="${escapeHtml(field.name)}" type="number" value="${escapeHtml(value)}" ${placeholder} ${required}>`;
  } else if (field.type === 'checkbox') {
    return `<div class="form-check field"><input class="form-check-input" id="field-${escapeHtml(field.name)}" name="${escapeHtml(field.name)}" type="checkbox" ${value ? 'checked' : ''}><label class="form-check-label" for="field-${escapeHtml(field.name)}">${escapeHtml(field.label || field.name)}</label>${hint}</div>`;
  } else if (field.type === 'file') {
    const accept = field.accept ? `accept="${escapeHtml(field.accept)}"` : '';
    return `<div class="field upload-field"><label class="upload-dropzone" for="field-${escapeHtml(field.name)}"><input class="upload-input visually-hidden" id="field-${escapeHtml(field.name)}" name="${escapeHtml(field.name)}" type="file" ${accept} ${required}><span class="upload-copy"><strong class="upload-title">${escapeHtml(field.label || field.name)}</strong><span class="upload-prompt">Drop a file here or <span>browse</span></span><span class="upload-file" data-upload-file>No file selected</span></span><span class="upload-symbol" aria-hidden="true"></span></label>${hint}</div>`;
  } else if (field.type === 'textarea') {
    control = `<textarea class="form-control" id="field-${escapeHtml(field.name)}" name="${escapeHtml(field.name)}" ${placeholder} ${required}>${escapeHtml(value)}</textarea>`;
  } else {
    control = `<input class="form-control" id="field-${escapeHtml(field.name)}" name="${escapeHtml(field.name)}" type="text" value="${escapeHtml(value)}" ${placeholder} ${required}>`;
  }

  return `<div class="field">${label}${control}${hint}</div>`;
}

function hasFileInputs() {
  return (manifest.inputs || []).some(field => field.type === 'file');
}

// Show or hide fields declaring show_when: {config_key, values} based on the
// current JSON configuration. An unparseable configuration leaves them as is.
function updateFieldVisibility() {
  const configElement = document.getElementById(`field-${configFieldName()}`);
  let config;
  try {
    config = JSON.parse(configElement?.value || '{}');
  } catch {
    return;
  }
  if (!config || typeof config !== 'object' || Array.isArray(config)) return;
  for (const field of manifest.inputs || []) {
    const rule = field.show_when;
    if (!rule?.config_key || !Array.isArray(rule.values)) continue;
    const container = document.getElementById(`field-${field.name}`)?.closest('.field');
    if (container) container.hidden = !rule.values.includes(config[rule.config_key]);
  }
}

function collectInputs() {
  const result = {};
  for (const field of manifest.inputs || []) {
    const element = document.getElementById(`field-${field.name}`);
    if (!element || field.type === 'file') continue;

    result[field.name] = field.type === 'checkbox' ? element.checked : element.value;
    if (field.type === 'number' && result[field.name] !== '') {
      result[field.name] = Number(result[field.name]);
    }
  }
  return result;
}

function readConfiguration() {
  const configElement = document.getElementById(`field-${configFieldName()}`);
  if (!configElement) {
    throw new Error(`Configuration field '${configFieldName()}' was not found.`);
  }

  let config;
  try {
    config = JSON.parse(configElement.value || '{}');
  } catch {
    throw new Error('The configuration must be valid JSON before files can update it.');
  }
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    throw new Error('The configuration must be a JSON object.');
  }
  return {config, configElement};
}

function validateConfiguration() {
  const configElement = document.getElementById(`field-${configFieldName()}`);
  let config = null;
  if (configElement && fieldByName(configFieldName())) {
    try {
      config = JSON.parse(configElement.value);
    } catch {
      throw new Error('The configuration must be valid JSON.');
    }
    if (!config || typeof config !== 'object' || Array.isArray(config)) {
      throw new Error('The configuration must be a JSON object.');
    }
  }
  callPlugins('validate', {config, uploads: selectedUploads});
}

// ---------------------------------------------------------------------------
// File uploads: each file input with config_field writes its uploaded path into
// the JSON configuration field. Inputs with inspect: true are also inspected
// when the workflow provides an inspection service.
// ---------------------------------------------------------------------------

function updateUploadState(input) {
  const file = input.files?.[0];
  const fileLabel = input.closest('.upload-dropzone')?.querySelector('[data-upload-file]');
  if (!fileLabel) return;

  if (!file) {
    fileLabel.textContent = 'No file selected';
    fileLabel.classList.remove('has-file');
    return;
  }

  const size = file.size < 1024 * 1024
    ? `${Math.max(1, Math.round(file.size / 1024))} KB`
    : `${(file.size / (1024 * 1024)).toFixed(1)} MB`;
  fileLabel.textContent = `${file.name} · ${size}`;
  fileLabel.classList.add('has-file');
}

function wireUploadControls() {
  for (const input of fieldsEl.querySelectorAll('input[type="file"]')) {
    const dropzone = input.closest('.upload-dropzone');
    input.addEventListener('change', () => {
      updateUploadState(input);
      const field = fieldByName(input.name);
      if (field) updateSelectedFile(field);
    });

    dropzone.addEventListener('dragover', event => {
      event.preventDefault();
      dropzone.classList.add('is-dragging');
    });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('is-dragging'));
    dropzone.addEventListener('drop', event => {
      event.preventDefault();
      dropzone.classList.remove('is-dragging');
      const files = event.dataTransfer?.files;
      if (!files?.length) return;

      const transfer = new DataTransfer();
      transfer.items.add(files[0]);
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
    });
  }
}

function applyUploadedFiles() {
  const {config, configElement} = readConfiguration();
  for (const [name, state] of selectedUploads) {
    const field = fieldByName(name);
    if (state.path && field?.config_field) config[field.config_field] = state.path;
  }
  callPlugins('applyUploads', {config, uploads: selectedUploads, fieldByName});
  configElement.value = JSON.stringify(config, null, 2);
  updateFieldVisibility();
  renderUploadFeedback(config);
}

function formatShape(metadata) {
  return metadata.kind === 'tensor' && metadata.shape.length === 3
    ? `${metadata.shape[0]} samples / ${metadata.shape[1]} time steps / ${metadata.shape[2]} features`
    : `Shape: (${metadata.shape.join(', ')})`;
}

function renderUploadFeedback(config) {
  const notes = [...selectedUploads.entries()].filter(([, state]) => state.note);
  const pluginGuidance = callPlugins('uploadGuidance', {config, uploads: selectedUploads}).filter(Boolean);
  const pluginMessages = pluginGuidance.flatMap(result => result.messages || []);
  const needsAttention = pluginGuidance.some(result => result.attention)
    || [...selectedUploads.values()].some(state => state.inspectionError || state.metadata?.kind === 'unavailable');
  const detailsWereOpen = fileSetupFeedbackEl.querySelector('details')?.open === true;
  fileSetupFeedbackEl.replaceChildren();

  if (notes.length || pluginMessages.length) {
    const content = needsAttention ? fileSetupFeedbackEl : document.createElement('details');
    const heading = document.createElement(needsAttention ? 'strong' : 'summary');
    heading.textContent = needsAttention ? 'Attention:' : `File information: ${notes.length} inspected`;
    content.append(heading);
    if (!needsAttention) {
      content.open = detailsWereOpen;
      fileSetupFeedbackEl.append(content);
    }
    const list = document.createElement('ul');
    list.className = 'file-feedback-list';
    const guidance = new Set();
    for (const [name, state] of notes) {
      const field = fieldByName(name);
      const item = document.createElement('li');
      const label = document.createElement('strong');
      label.textContent = field?.label || field?.config_field || name;
      const filename = document.createElement('span');
      filename.className = 'file-feedback-name';
      filename.textContent = state.file.name;
      item.append(label, filename);
      if (state.metadata?.shape) {
        const shape = document.createElement('span');
        shape.className = 'file-feedback-shape';
        shape.textContent = formatShape(state.metadata);
        item.append(shape);
      }
      if (state.inspectionError || state.metadata?.kind === 'unavailable') {
        const warning = document.createElement('p');
        warning.className = 'file-feedback-guidance';
        warning.textContent = state.note;
        item.append(warning);
      } else {
        guidance.add(state.note);
      }
      list.append(item);
    }
    if (notes.length) content.append(list);
    for (const message of pluginMessages) guidance.add(message);
    for (const message of guidance) {
      const paragraph = document.createElement('p');
      paragraph.className = 'file-feedback-guidance';
      paragraph.textContent = message;
      content.append(paragraph);
    }
  }
  fileSetupFeedbackEl.classList.toggle('file-setup-feedback-warning', needsAttention);
  fileSetupFeedbackEl.hidden = !notes.length && !pluginMessages.length;
}

async function ensureSelectedUpload(field) {
  const file = document.getElementById(`field-${field.name}`)?.files?.[0];
  if (!file) return;
  const cached = selectedUploads.get(field.name);
  if (cached?.file === file && !cached.error) return cached.promise;
  readConfiguration();
  if (!field.config_field || !field.upload_category) {
    throw new Error(`Upload field '${field.name}' is missing configuration.`);
  }
  const generation = uploadGeneration;
  const state = {file};
  selectedUploads.set(field.name, state);
  pendingUploads += 1;
  submitBtn.disabled = true;
  setStatus(`Uploading ${file.name} and updating the configuration...`, false, 'Uploading');
  state.promise = (async () => {
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('category', field.upload_category);
      const response = await fetch('/uploads', {method: 'POST', body: formData});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `Could not upload ${file.name}.`);
      if (generation !== uploadGeneration || selectedUploads.get(field.name) !== state) return;
      state.path = data.path;
      applyUploadedFiles();
      if (field.inspect && manifest.features?.upload_inspection) {
        try {
          const inspection = await fetch('/uploads/inspect', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: state.path}),
          });
          const metadata = await inspection.json();
          if (!inspection.ok) throw new Error(metadata.detail || 'File inspection failed.');
          state.metadata = metadata;
          state.note = metadata.message;
        } catch (error) {
          state.inspectionError = true;
          state.note = `${error.message} The file remains uploaded; review the configuration manually.`;
        }
      }
      if (generation === uploadGeneration && selectedUploads.get(field.name) === state) {
        applyUploadedFiles();
      }
    } catch (error) {
      state.error = error;
      throw error;
    } finally {
      if (generation === uploadGeneration) {
        pendingUploads -= 1;
        submitBtn.disabled = pendingUploads > 0 || workflowSubmitting;
      }
    }
  })();
  return state.promise;
}

async function updateSelectedFile(field) {
  const input = document.getElementById(`field-${field.name}`);
  const generation = uploadGeneration;
  const file = input?.files?.[0];
  try {
    if (!file) {
      const previous = selectedUploads.get(field.name);
      selectedUploads.delete(field.name);
      const {config, configElement} = readConfiguration();
      if (previous?.path && config[field.config_field] === previous.path) delete config[field.config_field];
      callPlugins('uploadRemoved', {field, config});
      configElement.value = JSON.stringify(config, null, 2);
      applyUploadedFiles();
      return;
    }
    await ensureSelectedUpload(field);
    if (generation === uploadGeneration && input.files?.[0] === file
        && !pendingUploads && !workflowSubmitting) {
      const inspectionFailed = [...selectedUploads.values()].some(state => state.inspectionError);
      const uploadFailed = [...selectedUploads.values()].find(state => state.error);
      setStatus(uploadFailed ? uploadFailed.error.message : inspectionFailed
        ? 'Files uploaded, but file inspection failed for some of them. Review the file notes and configuration.'
        : 'Configuration updated from the selected files. Review it before running.',
      inspectionFailed || Boolean(uploadFailed));
    }
  } catch (error) {
    if (generation === uploadGeneration && input?.files?.[0] === file) {
      setStatus(error.message || 'File upload failed.', true);
    }
  }
}

async function uploadConfiguredFiles() {
  for (const field of manifest.inputs || []) {
    if (field.type === 'file') await ensureSelectedUpload(field);
  }
  if (selectedUploads.size) applyUploadedFiles();
}

// ---------------------------------------------------------------------------
// Setup assistant (optional): turns a free-text request into a configuration.
// ---------------------------------------------------------------------------

function addAssistantMessage(role, text) {
  const message = document.createElement('p');
  message.className = `assistant-message assistant-message-${role}`;
  message.textContent = text;
  assistantConversationEl.append(message);
}

function showAssistantHint(visible) {
  assistantHintEl.hidden = !visible;
  if (visible) {
    assistantPromptEl.setAttribute('aria-describedby', 'assistantHint');
  } else {
    assistantPromptEl.removeAttribute('aria-describedby');
  }
}

function assistantHintNeeded(result) {
  const assistant = manifest.assistant || {};
  if (result.status !== 'clarification' || !assistant.hint) return false;
  return !assistant.hint_when_missing || !result.config?.[assistant.hint_when_missing];
}

function assistantPlaceholder() {
  return manifest.assistant?.placeholder || 'Describe what you want this workflow to do.';
}

async function previewSetup(event) {
  event.preventDefault();
  const prompt = assistantPromptEl.value.trim();
  if (!prompt) return;

  setupConversation.push({role: 'user', content: prompt});
  addAssistantMessage('user', prompt);
  assistantPromptEl.value = '';
  assistantPromptEl.disabled = true;
  assistantSubmitBtn.disabled = true;
  skipAssistantBtn.disabled = true;
  assistantStepEl.setAttribute('aria-busy', 'true');
  assistantSubmitBtn.innerHTML = `${SPINNER}Checking request...`;
  assistantContinueBtn.hidden = true;
  assistantResult = null;
  setStatus('Checking your request against the supported options. The workflow has not started.', false, 'Checking request');
  const waitNotice = setTimeout(() => {
    setStatus('Still waiting for the setup assistant. It can take a few minutes to respond. The workflow has not started.', false, 'Checking request');
  }, 15000);
  const controller = new AbortController();
  const requestTimeout = setTimeout(() => controller.abort(), 210000);

  try {
    const response = await fetch('/assistant/configure', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({messages: setupConversation}),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Could not check this request.');
    }

    assistantResult = data;
    showAssistantHint(assistantHintNeeded(data));
    addAssistantMessage('assistant', data.message);
    setupConversation.push({role: 'assistant', content: data.message});
    if (data.status === 'ready') {
      assistantContinueBtn.hidden = false;
      setStatus('Setup is supported. Continue to choose files and review settings.');
    } else {
      assistantPromptEl.placeholder = data.status === 'clarification'
        ? 'Answer the question or add the missing details.'
        : 'Revise your request to use a supported option.';
      setStatus(data.status === 'unsupported'
        ? 'This request is outside the supported options.'
        : 'A little more information is needed.');
    }
  } catch (error) {
    setStatus(error.name === 'AbortError'
      ? 'The setup assistant took too long to respond. Try again or continue to manual setup.'
      : error.message || 'The setup assistant is unavailable.', true);
  } finally {
    clearTimeout(waitNotice);
    clearTimeout(requestTimeout);
    assistantPromptEl.disabled = false;
    assistantSubmitBtn.disabled = false;
    skipAssistantBtn.disabled = false;
    assistantStepEl.setAttribute('aria-busy', 'false');
    assistantSubmitBtn.textContent = 'Check my request';
    if (assistantResult?.status === 'clarification') assistantPromptEl.focus();
  }
}

function showConfigurationStep(config = null) {
  const configElement = document.getElementById(`field-${configFieldName()}`);
  if (config && configElement) {
    configElement.value = JSON.stringify(config, null, 2);
  }
  if (selectedUploads.size) applyUploadedFiles();
  updateFieldVisibility();
  assistantStepEl.hidden = true;
  formEl.hidden = false;
  backToAssistantBtn.hidden = !assistantEnabled;
  assistantPromptEl.placeholder = assistantPlaceholder();
  setStatus('Review the configuration and select or upload the required files.');
}

// ---------------------------------------------------------------------------
// Status, steps and timeline
// ---------------------------------------------------------------------------

function setStatus(text, isError = false, activity = 'Ready') {
  statusEl.textContent = text;
  statusEl.classList.toggle('alert-secondary', !isError);
  statusEl.classList.toggle('alert-danger', isError);
  activityLabelEl.textContent = isError ? 'Needs attention' : activity;
}

function taskMethod(task) {
  return task.method || task.method_name || task.node_key?.split(':')[1] || '';
}

function stepFor(task) {
  const method = taskMethod(task);
  return (manifest.steps || []).find(step => step.method === method) || null;
}

function taskPresentation(task) {
  const step = stepFor(task);
  if (step) return {title: step.title || step.method, description: step.description || ''};
  return {
    title: taskMethod(task) || task.service || task.service_name || 'Workflow task',
    description: '',
  };
}

function taskDetails(task) {
  return `<details class="technical-details"><summary>Technical details</summary>
    <div>Task ID: <code>${escapeHtml(task.task_id || '')}</code></div>
    ${task.node_key ? `<div>Operation: <code>${escapeHtml(task.node_key)}</code></div>` : ''}
  </details>`;
}

// Timeline entries follow the manifest's step order when steps are declared,
// so steps that have not started yet are shown as waiting. Tasks that match no
// declared step are appended in orchestrator order.
function timelineEntries(tasks) {
  const steps = manifest.steps || [];
  const used = new Set();
  const entries = steps.map(step => {
    const task = tasks.find(item => !used.has(item) && taskMethod(item) === step.method);
    if (task) used.add(task);
    return {step, task};
  });
  for (const task of tasks) {
    if (!used.has(task)) entries.push({step: null, task});
  }
  return entries;
}

function renderTimeline(tasks) {
  if (!tasks?.length) {
    timelineEl.innerHTML = '';
    return;
  }

  timelineEl.innerHTML = timelineEntries(tasks).map(({step, task}) => {
    const presentation = task ? taskPresentation(task) : {title: step.title || step.method, description: step.description || ''};
    const status = task?.status || 'waiting';
    return `
    <div class="list-group-item workflow-task workflow-task-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(presentation.title)}</strong>
        <div class="form-text">${escapeHtml(presentation.description)}</div>
        ${task ? taskDetails(task) : ''}
      </div>
      <span class="badge rounded-pill text-bg-light border">${escapeHtml(status)}</span>
    </div>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// Output renderers
// ---------------------------------------------------------------------------

function firstSummary(payload) {
  for (const field of manifest.output?.summary_fields || []) {
    const found = deepFind(payload, field);
    if (typeof found === 'string' && found.trim()) return found;
  }
  return '';
}

function renderImages(currentWorkflowId, payload) {
  const collectionField = manifest.output?.image_collection_field || 'drawings';
  const signatureField = manifest.output?.image_signature_field || 'signature';
  const drawings = deepFind(payload, collectionField);
  if (!Array.isArray(drawings) || !drawings.length) return '';

  const items = drawings.map((drawing, index) => {
    const signature = drawing?.[signatureField] || String(index + 1);
    return `<div class="col-12 col-md-6"><figure class="figure workflow-asset"><img class="figure-img img-fluid" src="/workflows/${encodeURIComponent(currentWorkflowId)}/assets/${encodeURIComponent(signature)}" alt="Workflow asset ${escapeHtml(signature)}"><figcaption class="figure-caption px-2 pb-2">${escapeHtml(signature)}</figcaption></figure></div>`;
  }).join('');
  return `<div class="row g-3">${items}</div>`;
}

function renderMarkdown(markdown) {
  if (!markdown) return '';
  return `<div class="report-markdown">${escapeHtml(markdown)
    .replace(/^### (.*)$/gm, '<h5>$1</h5>')
    .replace(/^## (.*)$/gm, '<h4>$1</h4>')
    .replace(/^# (.*)$/gm, '<h3>$1</h3>')
    .replace(/^- (.*)$/gm, '<div class="report-item">$1</div>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n\n/g, '<br><br>')
  }</div>`;
}

function artifactUrl(currentWorkflowId, taskId, path) {
  const filePath = [taskId, path].filter(Boolean).join('/');
  return `/workflows/${encodeURIComponent(currentWorkflowId)}/files/${filePath.split('/').map(encodeURIComponent).join('/')}`;
}

// Preview an artifact by file type: images and HTML render directly (HTML in
// a sandboxed frame with scripts disabled); text formats are fetched by
// loadArtifactPreviews.
function artifactPreview(url, path) {
  const extension = path.split('.').pop().toLowerCase();
  if (IMAGE_EXTENSIONS.includes(extension)) {
    return `<div class="artifact-preview"><img class="img-fluid" src="${escapeHtml(url)}" alt="${escapeHtml(path)}"></div>`;
  }
  if (extension === 'html' || extension === 'htm') {
    return `<div class="artifact-preview"><iframe class="artifact-frame" sandbox="" src="${escapeHtml(url)}" title="${escapeHtml(path)}"></iframe></div>`;
  }
  if (TEXT_PREVIEW_EXTENSIONS.includes(extension)) {
    return `<div class="artifact-preview" data-artifact-path="${escapeHtml(path)}" data-artifact-url="${escapeHtml(url)}"><span class="preview-loading">Loading...</span></div>`;
  }
  return '';
}

function renderArtifacts(currentWorkflowId, payload, taskId) {
  const artifacts = Array.isArray(payload.artifacts) ? payload.artifacts : [];
  if (!artifacts.length) return '';
  const links = artifacts.map(artifact => {
    const path = typeof artifact === 'string' ? artifact : artifact.path;
    if (!path) return '';
    const url = artifactUrl(currentWorkflowId, taskId, path);
    return `<li class="artifact-item"><div class="artifact-line"><span>${escapeHtml(path)}</span><a class="artifact-download" href="${escapeHtml(url)}" target="_blank" rel="noopener">Download</a></div>${artifactPreview(url, path)}</li>`;
  }).join('');
  const downloadAll = taskId
    ? `<a class="artifact-download-all" href="/workflows/${encodeURIComponent(currentWorkflowId)}/tasks/${encodeURIComponent(taskId)}/download">Download all</a>`
    : '';
  return `<div class="artifacts-heading"><h4>Artifacts</h4>${downloadAll}</div><ul>${links}</ul>`;
}

function renderCsvPreview(text) {
  const rows = text.trim().split(/\r?\n/).filter(Boolean).slice(0, 21)
    .map(row => row.split(',').map(cell => cell.trim()));
  if (!rows.length) return '<p class="preview-empty">CSV is empty.</p>';
  const header = rows[0];
  const body = rows.slice(1).map(row => `<tr>${header.map((_, index) => `<td>${escapeHtml(row[index] || '')}</td>`).join('')}</tr>`).join('');
  return `<div class="table-responsive"><table class="table table-sm artifact-table"><thead><tr>${header.map(cell => `<th>${escapeHtml(cell)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>${text.trim().split(/\r?\n/).length > 21 ? '<small class="preview-note">Showing the first 20 rows.</small>' : ''}`;
}

async function loadArtifactPreviews() {
  const previews = outputEl.querySelectorAll('.artifact-preview[data-artifact-url]');
  await Promise.all([...previews].map(async preview => {
    const path = preview.dataset.artifactPath;
    const extension = path.split('.').pop().toLowerCase();
    try {
      const response = await fetch(preview.dataset.artifactUrl);
      if (!response.ok) throw new Error(`Preview request failed (HTTP ${response.status}).`);
      const text = await response.text();
      if (extension === 'md' || extension === 'markdown') {
        preview.innerHTML = `<div class="artifact-label">Report</div>${renderMarkdown(text)}`;
      } else if (extension === 'csv') {
        preview.innerHTML = `<div class="artifact-label">Data</div>${renderCsvPreview(text)}`;
      } else if (extension === 'json') {
        preview.innerHTML = `<div class="artifact-label">JSON</div><pre>${escapeHtml(JSON.stringify(JSON.parse(text), null, 2))}</pre>`;
      } else {
        preview.innerHTML = `<div class="artifact-label">${escapeHtml(extension.toUpperCase())}</div><pre>${escapeHtml(text)}</pre>`;
      }
    } catch (error) {
      preview.innerHTML = `<span class="preview-empty">Preview unavailable: ${escapeHtml(error.message)} Use Download.</span>`;
    }
  }));
}

// ---------------------------------------------------------------------------
// Decision gates. A step with "gate" asks the user to approve its result.
// The orchestrator does not pause for gates yet, so the decision is recorded
// once the step has completed.
// ---------------------------------------------------------------------------

function buttonClass(style) {
  const classes = {
    primary: 'btn btn-primary',
    secondary: 'btn btn-outline-secondary',
    danger: 'btn btn-outline-danger',
    success: 'btn btn-outline-success',
    warning: 'btn btn-outline-warning',
  };
  return classes[style] || classes.secondary;
}

function actionButtons(actions, step = null) {
  const stepAttribute = step ? ` data-step="${escapeHtml(step)}"` : '';
  return (actions || []).map(action => (
    `<button type="button" class="${buttonClass(action.style)}" data-action="${escapeHtml(action.name)}"${stepAttribute}>${escapeHtml(action.label || action.name)}</button>`
  )).join('');
}

function gateIsOpen(task) {
  return ['completed', 'awaiting_decision'].includes(task?.status);
}

function pendingGates(statusData) {
  return timelineEntries(statusData.tasks || [])
    .filter(({step, task}) => step?.gate && gateIsOpen(task) && !statusData.decisions?.[step.method])
    .map(({step}) => step);
}

function renderGate(step, task, statusData) {
  if (!step?.gate || !gateIsOpen(task)) return '';
  const decision = statusData.decisions?.[step.method];
  if (decision) {
    const action = (step.gate.actions || []).find(item => item.name === decision.action);
    return `<div class="workflow-gate workflow-gate-decided">Decision recorded: <strong>${escapeHtml(action?.label || decision.action)}</strong>${decision.value ? ` <code>${escapeHtml(decision.value)}</code>` : ''}</div>`;
  }
  return `<div class="workflow-gate" role="group" aria-label="${escapeHtml(step.gate.label || 'Decision')}">
    <p class="workflow-gate-prompt">${escapeHtml(step.gate.prompt || 'Review this result and record your decision.')}</p>
    <div class="d-flex flex-wrap gap-2">${actionButtons(step.gate.actions, step.method)}</div>
  </div>`;
}

// A dotted value field ("report.recommended_network") is an exact path; a plain
// name is searched for anywhere in the payload.
function findField(payload, field) {
  if (!field.includes('.')) return deepFind(payload, field);
  return field.split('.').reduce((value, key) => (
    value && typeof value === 'object' ? value[key] : undefined
  ), payload);
}

// The value a decision applies to, e.g. the topology being confirmed. For a
// step gate, only that step's outputs are searched.
function selectedValue(step) {
  const gateStep = step ? (manifest.steps || []).find(item => item.method === step) : null;
  const field = gateStep?.gate?.value_field || manifest.output?.selected_value_field || 'signature';
  const outputs = (latestStatus?.outputs || []).filter(output => {
    if (!gateStep) return true;
    const task = (latestStatus.tasks || []).find(item => item.task_id === output.task_id) || output;
    return taskMethod(task) === gateStep.method;
  });
  for (const output of outputs) {
    const found = findField(output.payload, field);
    if (found !== undefined && found !== null && typeof found !== 'object') return String(found);
  }
  return null;
}

async function recordDecision(action, step = null) {
  if (!workflowId) return;

  try {
    const response = await fetch('/decision', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({workflow_id: workflowId, action, step, value: selectedValue(step)}),
    });
    const data = await response.json();
    setStatus(data.detail || data.message || data.status || 'Decision recorded.', !response.ok);
    if (response.ok && latestStatus) {
      renderOutputs({
        ...latestStatus,
        decisions: {...(latestStatus.decisions || {}), [step || 'workflow']: {action, value: data.value}},
      });
    }
  } catch (error) {
    setStatus(error.message || 'Could not record the decision.', true);
  }
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

function renderOutputs(statusData) {
  latestStatus = statusData;
  renderTimeline(statusData.tasks || []);
  outputTitle.textContent = manifest.output?.title || 'Workflow output';
  actionsEl.hidden = statusData.status !== 'completed' || !(manifest.actions || []).length;

  const outputs = statusData.outputs || [];
  if (!outputs.length) {
    outputEl.innerHTML = '<div class="empty-state">No workflow output is available yet.</div>';
    return;
  }

  const gatesShown = new Set();
  outputEl.innerHTML = outputs.map(output => {
    const task = (statusData.tasks || []).find(item => item.task_id === output.task_id) || output;
    const step = stepFor(task);
    const summary = firstSummary(output.payload);
    const images = renderImages(statusData.workflow_id, output.payload);
    const report = renderMarkdown(output.payload.report_markdown);
    const pluginHtml = callPlugins('renderOutput', {output, task, step, workflowId: statusData.workflow_id})
      .filter(item => typeof item === 'string').join('');
    const artifacts = renderArtifacts(statusData.workflow_id, output.payload, output.task_id);
    let gate = '';
    if (step?.gate && !gatesShown.has(step.method)) {
      gate = renderGate(step, task, statusData);
      if (gate) gatesShown.add(step.method);
    }
    return `<article class="card output-card">
      <div class="card-body">
      <h3 class="h5 card-title">${escapeHtml(taskPresentation(task).title)}</h3>
      ${summary ? `<p class="summary">${escapeHtml(summary)}</p>` : ''}
      ${report}
      ${images}
      ${pluginHtml}
      ${artifacts}
      ${gate}
      ${taskDetails(task)}
      <details class="technical-details"><summary>Raw output</summary><pre>${escapeHtml(JSON.stringify(output.payload, null, 2))}</pre></details>
      </div>
    </article>`;
  }).join('');
  loadArtifactPreviews();
}

// ---------------------------------------------------------------------------
// Running a workflow
// ---------------------------------------------------------------------------

async function pollWorkflow() {
  if (!workflowId) return;
  const generation = requestGeneration;
  try {
    const response = await fetch(`/workflows/${encodeURIComponent(workflowId)}`);
    const data = await response.json();
    if (generation !== requestGeneration) return;
    if (!response.ok || data.status === 'failed') {
      setStatus(data.error || data.detail || 'Workflow failed.', true);
      renderOutputs(data);
      return;
    }

    renderOutputs(data);
    if (data.status === 'completed') {
      const gates = pendingGates(data);
      setStatus(gates.length
        ? `Workflow completed. A decision is needed on: ${gates.map(step => step.title || step.method).join(', ')}.`
        : 'Workflow completed.');
      return;
    }
    const activeTask = (data.tasks || []).find(task => ['running', 'in_progress'].includes(task.status));
    setStatus(activeTask
      ? `Running ${taskPresentation(activeTask).title}...`
      : 'Workflow processing. Waiting for the next service...', false, 'Running');
    pollHandle = setTimeout(pollWorkflow, manifest.poll_interval_ms || 2000);
  } catch (error) {
    if (generation === requestGeneration) setStatus(error.message || 'Could not check workflow status.', true);
  }
}

async function submitWorkflow(event) {
  event.preventDefault();
  if (workflowSubmitting) return;
  const generation = ++requestGeneration;
  if (pollHandle) clearTimeout(pollHandle);

  workflowSubmitting = true;
  submitBtn.disabled = true;
  submitBtn.innerHTML = `${SPINNER}Submitting...`;
  formEl.setAttribute('aria-busy', 'true');
  actionsEl.hidden = true;
  outputEl.innerHTML = '<div class="empty-state">Waiting for workflow output.</div>';

  try {
    if (hasFileInputs()) {
      setStatus('Uploading selected files...', false, 'Uploading');
      await uploadConfiguredFiles();
    }
    validateConfiguration();
    setStatus('Submitting workflow...', false, 'Submitting');
    const response = await fetch('/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({inputs: collectInputs()}),
    });
    const data = await response.json();
    if (generation !== requestGeneration) return;
    if (!response.ok) throw new Error(data.detail || 'Submission failed.');

    workflowId = data.workflow_id;
    if (!workflowId) throw new Error('The server did not return a workflow ID.');
    setStatus(`Workflow started: ${workflowId}.`, false, 'Running');
    renderOutputs(data);
    pollWorkflow();
  } catch (error) {
    if (generation === requestGeneration) setStatus(error.message || 'Submission failed.', true);
  } finally {
    if (generation === requestGeneration) {
      workflowSubmitting = false;
      submitBtn.disabled = pendingUploads > 0;
      submitBtn.textContent = manifest.submit_label || 'Start workflow';
      formEl.setAttribute('aria-busy', 'false');
    }
  }
}

function resetWorkflow() {
  requestGeneration += 1;
  uploadGeneration += 1;
  selectedUploads = new Map();
  pendingUploads = 0;
  workflowSubmitting = false;
  callPlugins('reset', {});
  submitBtn.disabled = false;
  submitBtn.textContent = manifest.submit_label || 'Start workflow';
  formEl.setAttribute('aria-busy', 'false');
  fileSetupFeedbackEl.hidden = true;
  fileSetupFeedbackEl.textContent = '';
  formEl.reset();
  updateFieldVisibility();
  assistantFormEl.reset();
  fieldsEl.querySelectorAll('input[type="file"]').forEach(updateUploadState);
  setupConversation = [];
  assistantResult = null;
  showAssistantHint(false);
  assistantConversationEl.replaceChildren();
  assistantContinueBtn.hidden = true;
  assistantPromptEl.placeholder = assistantPlaceholder();
  assistantStepEl.hidden = true;
  formEl.hidden = false;
  backToAssistantBtn.hidden = !assistantEnabled;
  workflowId = null;
  latestStatus = null;
  actionsEl.hidden = true;
  if (pollHandle) clearTimeout(pollHandle);
  timelineEl.innerHTML = '';
  outputEl.innerHTML = '<div class="empty-state">Run a workflow to see results here.</div>';
  setStatus('Inputs cleared.');
}

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------

function applyTheme() {
  const theme = manifest.theme || {};
  const properties = {
    accent: '--accent',
    accent_strong: '--accent-strong',
    ink: '--ink',
    muted: '--muted',
    line: '--line',
    background: '--workflow-background',
    font_family: '--workflow-font-family',
  };

  for (const [key, property] of Object.entries(properties)) {
    if (typeof theme[key] === 'string' && theme[key].trim()) {
      document.documentElement.style.setProperty(property, theme[key]);
    }
  }

  for (const className of manifest.body_classes || []) {
    if (/^[a-zA-Z0-9_-]+$/.test(className)) {
      document.body.classList.add(className);
    }
  }
}

function renderManifest() {
  applyTheme();
  document.title = manifest.title || 'AI-EFFECT Workflow';
  pageTitle.textContent = manifest.title || 'AI-EFFECT Workflow';
  pageDescription.textContent = manifest.description || '';
  manifestWarningEl.hidden = !manifest.manifest_warning;
  manifestWarningEl.textContent = manifest.manifest_warning || '';

  assistantEnabled = manifest.features?.assistant === true;
  assistantTitleEl.textContent = manifest.assistant?.label || 'What would you like to run?';
  assistantPromptEl.placeholder = assistantPlaceholder();
  assistantHintEl.textContent = manifest.assistant?.hint || '';
  showAssistantHint(false);
  assistantStepEl.hidden = !assistantEnabled;
  formEl.hidden = assistantEnabled;
  backToAssistantBtn.hidden = true;

  fieldsEl.innerHTML = (manifest.inputs || []).map(fieldHtml).join('');
  wireUploadControls();
  updateFieldVisibility();
  document.getElementById(`field-${configFieldName()}`)?.addEventListener('input', updateFieldVisibility);
  submitBtn.textContent = manifest.submit_label || 'Start workflow';
  pipelineBadge.textContent = `${(manifest.pipeline?.services || []).length} services`;
  actionsEl.innerHTML = actionButtons(manifest.actions);
  actionsEl.hidden = true;
  callPlugins('init', {manifest});
  setStatus('Ready.');
}

async function loadManifest() {
  try {
    const response = await fetch('/ui/config');
    manifest = await response.json();
    if (!response.ok) throw new Error(manifest.detail || 'Workflow UI configuration failed to load.');
  } catch (error) {
    setStatus(error.message || 'Workflow UI configuration failed to load.', true);
    return;
  }
  renderManifest();
}

formEl.addEventListener('submit', submitWorkflow);
assistantFormEl.addEventListener('submit', previewSetup);
assistantContinueBtn.addEventListener('click', () => {
  showConfigurationStep(assistantResult?.config || null);
});
skipAssistantBtn.addEventListener('click', () => showConfigurationStep());
backToAssistantBtn.addEventListener('click', () => {
  assistantStepEl.hidden = false;
  formEl.hidden = true;
  backToAssistantBtn.hidden = true;
  assistantContinueBtn.hidden = assistantResult?.status !== 'ready';
});
assistantPromptEl.addEventListener('input', () => {
  assistantContinueBtn.hidden = true;
  assistantResult = null;
});
resetBtn.addEventListener('click', resetWorkflow);
actionsEl.addEventListener('click', event => {
  const button = event.target.closest('button[data-action]');
  if (button) recordDecision(button.dataset.action);
});
outputEl.addEventListener('click', event => {
  const button = event.target.closest('button[data-action][data-step]');
  if (button) recordDecision(button.dataset.action, button.dataset.step);
});

loadManifest();
