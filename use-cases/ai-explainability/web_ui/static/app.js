let manifest = null;
let workflowId = null;
let latestStatus = null;
let pollHandle = null;
let setupConversation = [];
let assistantEnabled = false;
let assistantResult = null;
let selectedUploads = new Map();
let suggestedFeatures = null;
let suggestedFeatureSource = null;
let uploadGeneration = 0;
let pendingUploads = 0;
let workflowSubmitting = false;

const fieldsEl = document.getElementById('fields');
const formEl = document.getElementById('workflowForm');
const assistantFormEl = document.getElementById('assistantForm');
const assistantStepEl = document.getElementById('assistantStep');
const assistantPromptEl = document.getElementById('assistantPrompt');
const modelTypeHintEl = document.getElementById('modelTypeHint');
const assistantConversationEl = document.getElementById('assistantConversation');
const assistantSubmitBtn = document.getElementById('assistantSubmitBtn');
const assistantContinueBtn = document.getElementById('assistantContinueBtn');
const skipAssistantBtn = document.getElementById('skipAssistantBtn');
const backToAssistantBtn = document.getElementById('backToAssistantBtn');
const statusEl = document.getElementById('status');
const activityLabelEl = document.getElementById('activityLabel');
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

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char]));
}

// Generate HTML for a single form field based on its type and properties
function fieldHtml(field) {
  const required = field.required ? 'required' : '';
  const placeholder = field.placeholder ? `placeholder="${escapeHtml(field.placeholder)}"` : '';
  const value = field.default ?? '';
  const hint = field.help
    ? `<details class="field-help"><summary>Help<span class="visually-hidden"> for ${escapeHtml(field.label || field.name)}</span></summary><div class="hint">${escapeHtml(field.help)}</div></details>`
    : '';
  const label = `<label class="form-label" for="field-${field.name}">${escapeHtml(field.label || field.name)}</label>`;

  let control = '';
  if (field.type === 'select') {
    const options = (field.options || [])
      .map(option => `<option value="${escapeHtml(option.value ?? option)}">${escapeHtml(option.label ?? option)}</option>`)
      .join('');
    control = `<select class="form-select" id="field-${field.name}" name="${escapeHtml(field.name)}" ${required}>${options}</select>`;
  } else if (field.type === 'number') {
    control = `<input class="form-control" id="field-${field.name}" name="${escapeHtml(field.name)}" type="number" value="${escapeHtml(value)}" ${placeholder} ${required}>`;
  } else if (field.type === 'checkbox') {
    return `<div class="form-check field"><input class="form-check-input" id="field-${field.name}" name="${escapeHtml(field.name)}" type="checkbox" ${value ? 'checked' : ''}><label class="form-check-label" for="field-${field.name}">${escapeHtml(field.label || field.name)}</label>${hint}</div>`;
  } else if (field.type === 'file') {
    const accept = field.accept ? `accept="${escapeHtml(field.accept)}"` : '';
    return `<div class="field upload-field"><label class="upload-dropzone" for="field-${escapeHtml(field.name)}"><input class="upload-input visually-hidden" id="field-${escapeHtml(field.name)}" name="${escapeHtml(field.name)}" type="file" ${accept} ${required}><span class="upload-copy"><strong class="upload-title">${escapeHtml(field.label || field.name)}</strong><span class="upload-prompt">Drop a file here or <span>browse</span></span><span class="upload-file" data-upload-file>No file selected</span></span><span class="upload-symbol" aria-hidden="true"></span></label>${hint}</div>`;
  } else if (field.type === 'textarea') {
    control = `<textarea class="form-control" id="field-${field.name}" name="${escapeHtml(field.name)}" ${placeholder} ${required}>${escapeHtml(value)}</textarea>`;
  } else {
    control = `<input class="form-control" id="field-${field.name}" name="${escapeHtml(field.name)}" type="text" value="${escapeHtml(value)}" ${placeholder} ${required}>`;
  }

  return `<div class="field">${label}${control}${hint}</div>`;
}

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
      const field = (manifest.inputs || []).find(item => item.name === input.name);
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

function updateModelFields() {
  const configElement = document.getElementById(`field-${manifest.upload_config_field || 'config_json'}`);
  let config;
  try {
    config = JSON.parse(configElement?.value || '{}');
  } catch {
    return;
  }
  if (!config || typeof config !== 'object' || Array.isArray(config)) return;
  for (const field of manifest.inputs || []) {
    if (!field.model_types) continue;
    const container = document.getElementById(`field-${field.name}`)?.closest('.field');
    if (container) container.hidden = !field.model_types.includes(config.model_type);
  }
}

// Collect values from all input fields in the form
function collectInputs() {
  const result = {};
  for (const field of manifest.inputs || []) {
    const element = document.getElementById(`field-${field.name}`);
    if (!element) continue;
    if (field.type === 'file') continue;

    result[field.name] = field.type === 'checkbox' ? element.checked : element.value;
    if (field.type === 'number' && result[field.name] !== '') {
      result[field.name] = Number(result[field.name]);
    }
  }
  return result;
}

function readAnalysisConfiguration() {
  const configFieldName = manifest.upload_config_field || 'config_json';
  const configElement = document.getElementById(`field-${configFieldName}`);
  if (!configElement) {
    throw new Error(`Upload configuration field '${configFieldName}' was not found.`);
  }

  let config;
  try {
    config = JSON.parse(configElement.value || '{}');
  } catch {
    throw new Error('Analysis configuration must be valid JSON before files can update it.');
  }
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    throw new Error('Analysis configuration must be a JSON object.');
  }

  return {config, configElement};
}

function applyUploadedFiles() {
  const {config, configElement} = readAnalysisConfiguration();
  if ([...selectedUploads.values()].some(state => state.path)) {
    for (const key of ['analysis', 'model_type']) {
      if (!Object.hasOwn(config, key)) config[key] = '';
    }
  }
  for (const [name, state] of selectedUploads) {
    const field = (manifest.inputs || []).find(item => item.name === name);
    if (state.path && field) config[field.config_field] = state.path;
  }
  const tensorSource = ['test_data_upload', 'dataset_upload', 'background_data_upload']
    .find(name => selectedUploads.get(name)?.metadata?.kind === 'tensor');
  const source = config.model_type === 'lstm' ? tensorSource : 'dataset_upload';
  const metadata = selectedUploads.get(source)?.metadata;
  const canSuggest = metadata?.kind === 'table'
    ? config.analysis !== 'timeseries' && !['lstm', 'arima'].includes(config.model_type)
    : metadata?.kind === 'tensor' && config.model_type === 'lstm';
  const namesAreManaged = !Object.hasOwn(config, 'feature_names')
    || (suggestedFeatures !== null && JSON.stringify(config.feature_names) === JSON.stringify(suggestedFeatures));
  if (canSuggest && namesAreManaged) {
    config.feature_names = metadata.feature_names;
    suggestedFeatures = [...metadata.feature_names];
    suggestedFeatureSource = source;
  }
  configElement.value = JSON.stringify(config, null, 2);
  updateModelFields();
  const notes = [...selectedUploads.entries()].filter(([, state]) => state.note);
  const needsAttention = (!config.model_type && selectedUploads.size > 0)
    || [...selectedUploads.values()].some(state => state.inspectionError || state.metadata?.kind === 'unavailable');
  const detailsWereOpen = fileSetupFeedbackEl.querySelector('details')?.open === true;
  fileSetupFeedbackEl.replaceChildren();
  const showModelHint = !config.model_type && selectedUploads.size > 0;
  if (notes.length || showModelHint) {
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
      const field = (manifest.inputs || []).find(item => item.name === name);
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
        shape.textContent = state.metadata.kind === 'tensor' && state.metadata.shape.length === 3
          ? `${state.metadata.shape[0]} samples / ${state.metadata.shape[1]} time steps / ${state.metadata.shape[2]} features`
          : `Shape: (${state.metadata.shape.join(', ')})`;
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
    if (showModelHint) {
      guidance.add('Specify model_type in the configuration or use the assistant; file extensions do not identify the algorithm.');
    }
    for (const message of guidance) {
      const paragraph = document.createElement('p');
      paragraph.className = 'file-feedback-guidance';
      paragraph.textContent = message;
      content.append(paragraph);
    }
  }
  fileSetupFeedbackEl.classList.toggle('file-setup-feedback-warning', needsAttention);
  fileSetupFeedbackEl.hidden = !notes.length && !showModelHint;
}

async function ensureSelectedUpload(field) {
  const file = document.getElementById(`field-${field.name}`)?.files?.[0];
  if (!file) return;
  const cached = selectedUploads.get(field.name);
  if (cached?.file === file && !cached.error) return cached.promise;
  readAnalysisConfiguration();
  if (!field.config_field || !field.upload_category) {
    throw new Error(`Upload field '${field.name}' is missing configuration.`);
  }
  const generation = uploadGeneration;
  const state = {file};
  selectedUploads.set(field.name, state);
  pendingUploads += 1;
  submitBtn.disabled = true;
  setStatus(`Uploading ${file.name} and updating the configuration...`, false, true);
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
      if (field.upload_category === 'data') {
        try {
          const inspection = await fetch('/uploads/inspect', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: state.path}),
          });
          const metadata = await inspection.json();
          if (!inspection.ok) throw new Error(metadata.detail || 'Feature detection failed.');
          state.metadata = metadata;
          state.note = metadata.message;
        } catch (error) {
          state.inspectionError = true;
          state.note = `${error.message} The file remains uploaded; review features manually.`;
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
    if (!input?.files?.[0]) {
      const previous = selectedUploads.get(field.name);
      selectedUploads.delete(field.name);
      const {config, configElement} = readAnalysisConfiguration();
      if (previous?.path && config[field.config_field] === previous.path) delete config[field.config_field];
      if (suggestedFeatureSource === field.name
          && JSON.stringify(config.feature_names) === JSON.stringify(suggestedFeatures)) {
        delete config.feature_names;
        suggestedFeatures = null;
        suggestedFeatureSource = null;
      }
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
        ? 'Files uploaded, but some feature detection failed. Review the file notes and configuration.'
        : 'Configuration updated from selected files. Review model type and feature names before running.',
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

function validateAnalysisConfiguration() {
  const configFieldName = manifest.upload_config_field || 'config_json';
  const configElement = document.getElementById(`field-${configFieldName}`);
  let config;
  try {
    config = JSON.parse(configElement.value);
  } catch {
    throw new Error('Analysis configuration must be valid JSON.');
  }
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    throw new Error('Analysis configuration must be a JSON object.');
  }

  const required = ['model_path'];
  if (config.analysis === 'tabular') required.push('dataset_path');
  if (config.model_type === 'lstm') required.push('background_data_path', 'test_data_path');
  const missing = required.filter(field => !config[field]);
  if (missing.length) {
    throw new Error(`Add or upload ${missing.join(', ')} before running this analysis.`);
  }
  if (config.model_type === 'lstm') {
    for (const state of selectedUploads.values()) {
      if (state.metadata?.kind !== 'tensor') continue;
      if (![config.test_data_path, config.background_data_path].includes(state.path)) continue;
      const [, steps, features] = state.metadata.shape;
      if (config.feature_names?.length && config.feature_names.length !== features) {
        throw new Error(`feature_names must have ${features} entries to match ${state.file.name}.`);
      }
      if (config.look_back !== undefined && config.look_back !== steps) {
        throw new Error(`look_back must be ${steps} to match ${state.file.name}.`);
      }
      if (config.input_dim !== undefined && config.input_dim !== features) {
        throw new Error(`input_dim must be ${features} to match ${state.file.name}.`);
      }
    }
  }
}

function addAssistantMessage(role, text) {
  const message = document.createElement('p');
  message.className = `assistant-message assistant-message-${role}`;
  message.textContent = text;
  assistantConversationEl.append(message);
}

function showModelTypeHint(visible) {
  modelTypeHintEl.hidden = !visible;
  if (visible) {
    assistantPromptEl.setAttribute('aria-describedby', 'modelTypeHint');
  } else {
    assistantPromptEl.removeAttribute('aria-describedby');
  }
}

async function previewAnalysisSetup(event) {
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
  assistantSubmitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Checking request...';
  assistantContinueBtn.hidden = true;
  assistantResult = null;
  setStatus('Checking your request against the supported analysis options. Analysis has not started.', false, true);
  const waitNotice = setTimeout(() => {
    setStatus('Still waiting for the setup assistant. The local model can take a few minutes to respond. Analysis has not started.', false, true);
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
      throw new Error(data.detail || 'Could not check this analysis request.');
    }

    assistantResult = data;
    showModelTypeHint(data.status === 'clarification' && !data.config?.model_type);
    addAssistantMessage('assistant', data.message);
    setupConversation.push({role: 'assistant', content: data.message});
    if (data.status === 'ready') {
      assistantContinueBtn.hidden = false;
      setStatus('Setup is supported. Continue to choose files and review settings.');
    } else {
      assistantPromptEl.placeholder = data.status === 'clarification'
        ? 'Answer the question or add the missing details.'
        : 'Revise your request to use a supported analysis.';
      setStatus(data.status === 'unsupported'
        ? 'This request is outside the supported analysis options.'
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
  const configFieldName = manifest.upload_config_field || 'config_json';
  const configElement = document.getElementById(`field-${configFieldName}`);
  if (config && configElement) {
    configElement.value = JSON.stringify(config, null, 2);
  }
  if (selectedUploads.size) applyUploadedFiles();
  updateModelFields();
  assistantStepEl.hidden = true;
  formEl.hidden = false;
  backToAssistantBtn.hidden = !assistantEnabled;
  assistantPromptEl.placeholder = 'Describe what you want explained.';
  setStatus('Review the configuration and select or upload the required files.');
}

// Update the status message displayed to the user
function setStatus(text, isError = false, isBusy = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle('alert-secondary', !isError);
  statusEl.classList.toggle('alert-danger', isError);
  activityLabelEl.textContent = isError ? 'Needs attention' : isBusy ? 'Checking request' : 'Ready';
}


// Render the timeline of tasks for the workflow
function renderTimeline(tasks) {
  if (!tasks?.length) {
    timelineEl.innerHTML = '';
    return;
  }

  timelineEl.innerHTML = tasks.map(task => `
    <div class="list-group-item workflow-task">
      <div>
        <strong>${escapeHtml(task.service || task.service_name || task.task_id || 'Task')}</strong>
        <div class="form-text">${escapeHtml(task.method || task.method_name || '')}</div>
      </div>
      <span class="badge rounded-pill text-bg-light border">${escapeHtml(task.status || 'pending')}</span>
    </div>
  `).join('');
}


// Recursively search for a key in an object and return its value if found, otherwise return undefined
function deepFind(value, key) {
  if (!value || typeof value !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(value, key)) return value[key];

  for (const child of Object.values(value)) {
    const found = deepFind(child, key);
    if (found !== undefined) return found;
  }
  return undefined;
}

// Extract the first non-empty summary field from the payload
// it is used to display a brief summary of the workflow output
function firstSummary(payload) {
  const fields = manifest.output?.summary_fields || [];
  for (const field of fields) {
    const found = deepFind(payload, field);
    if (typeof found === 'string' && found.trim()) return found;
  }
  return '';
}

// Render images associated with the workflow output
// if no images are found, an empty string is returned
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

function renderArtifacts(currentWorkflowId, payload, taskId) {
  const artifacts = Array.isArray(payload.artifacts) ? payload.artifacts : [];
  if (!artifacts.length) return '';
  const links = artifacts.map(artifact => {
    const path = typeof artifact === 'string' ? artifact : artifact.path;
    if (!path) return '';
    const extension = path.split('.').pop().toLowerCase();
    const previewable = ['md', 'markdown', 'csv', 'json'].includes(extension);
    const preview = previewable
      ? `<div class="artifact-preview" data-artifact-path="${escapeHtml(path)}" data-artifact-url="${escapeHtml(artifactUrl(currentWorkflowId, taskId, path))}"><span class="preview-loading">Loading...</span></div>`
      : '';
    const filePath = [taskId, path].filter(Boolean).join('/');
    return `<li class="artifact-item"><div class="artifact-line"><span>${escapeHtml(path)}</span><a class="artifact-download" href="/workflows/${encodeURIComponent(currentWorkflowId)}/files/${filePath.split('/').map(encodeURIComponent).join('/')}" target="_blank" rel="noopener">Download</a></div>${preview}</li>`;
  }).join('');
  const downloadAll = taskId
    ? `<a class="artifact-download-all" href="/workflows/${encodeURIComponent(currentWorkflowId)}/tasks/${encodeURIComponent(taskId)}/download">Download all</a>`
    : '';
  return `<div class="artifacts-heading"><h4>Artifacts</h4>${downloadAll}</div><ul>${links}</ul>`;
}

function artifactUrl(currentWorkflowId, taskId, path) {
  const filePath = [taskId, path].filter(Boolean).join('/');
  return `/workflows/${encodeURIComponent(currentWorkflowId)}/files/${filePath.split('/').map(encodeURIComponent).join('/')}`;
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
  const previews = outputEl.querySelectorAll('.artifact-preview');
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
      } else {
        preview.innerHTML = `<div class="artifact-label">JSON</div><pre>${escapeHtml(JSON.stringify(JSON.parse(text), null, 2))}</pre>`;
      }
    } catch (error) {
      preview.innerHTML = `<span class="preview-empty">Preview unavailable: ${escapeHtml(error.message)} Use Download.</span>`;
    }
  }));
}

// Render the workflow outputs, including summaries, images, and raw payloads
function renderOutputs(statusData) {
  latestStatus = statusData;
  renderTimeline(statusData.tasks || []);
  outputTitle.textContent = manifest.output?.title || 'Workflow output';

  const outputs = statusData.outputs || [];
  if (!outputs.length) {
    outputEl.innerHTML = '<div class="empty-state">No workflow output is available yet.</div>';
    return;
  }

  outputEl.innerHTML = outputs.map(output => {
    const summary = firstSummary(output.payload);
    const images = renderImages(statusData.workflow_id, output.payload);
    const report = renderMarkdown(output.payload.report_markdown);
    const artifacts = renderArtifacts(statusData.workflow_id, output.payload, output.task_id);
    return `<article class="card output-card">
      <div class="card-body">
      <h3 class="h5 card-title">${escapeHtml(output.service || output.task_id || 'Output')}</h3>
      ${summary ? `<p class="summary">${escapeHtml(summary)}</p>` : ''}
      ${report}
      ${images}
      ${artifacts}
      <pre>${escapeHtml(JSON.stringify(output.payload, null, 2))}</pre>
      </div>
    </article>`;
  }).join('');
  loadArtifactPreviews();
}


// Retrieve the selected value from the workflow outputs based on the configured field
function selectedValue() {
  const field = manifest.output?.selected_value_field || 'signature';
  for (const output of latestStatus?.outputs || []) {
    const found = deepFind(output.payload, field);
    if (found !== undefined) return String(found);
  }
  return null;
}

// Render action buttons based on the configured actions in the manifest
function renderActions() {
  const actions = manifest.actions || [];
  if (!actions.length) return;

  actionsEl.hidden = false;
  actionsEl.innerHTML = actions.map(action => (
    `<button type="button" class="${buttonClass(action.style)}" data-action="${escapeHtml(action.name)}">${escapeHtml(action.label || action.name)}</button>`
  )).join('');
}

// Determine the CSS class for a button based on its style
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

// Poll the workflow status periodically and update the UI accordingly
// async operation enables non-blocking network requests for polling the workflow status
// Record a decision made by the user for the current workflow
async function recordDecision(action) {
  if (!workflowId) return;

  const response = await fetch('/decision', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({workflow_id: workflowId, action, value: selectedValue()}),
  });
  const data = await response.json();
  setStatus(data.message || data.status || 'Decision recorded.', !response.ok);
}

async function pollWorkflow() {
  if (!workflowId) return;

  const response = await fetch(`/workflows/${encodeURIComponent(workflowId)}`);
  const data = await response.json();
  if (!response.ok || data.status === 'failed') {
    setStatus(data.error || data.detail || 'Workflow failed.', true);
    renderOutputs(data);
    return;
  }

  renderOutputs(data);
  if (data.status === 'completed') {
    setStatus('Workflow completed.');
    return;
  }
  pollHandle = setTimeout(pollWorkflow, manifest.poll_interval_ms || 2000);
}


// Submit the workflow with the collected input values and start polling for its status
async function submitWorkflow(event) {
  event.preventDefault();
  if (pollHandle) clearTimeout(pollHandle);

  submitBtn.disabled = true;
  workflowSubmitting = true;
  setStatus('Uploading selected files...');
  outputEl.innerHTML = '<div class="empty-state">Waiting for workflow output.</div>';

  try {
    await uploadConfiguredFiles();
    validateAnalysisConfiguration();
    setStatus('Submitting workflow...');
    const response = await fetch('/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({inputs: collectInputs()}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Submission failed.');

    workflowId = data.workflow_id;
    setStatus(`Workflow started: ${workflowId}.`);
    renderOutputs(data);
    pollWorkflow();
  } catch (error) {
    setStatus(error.message || 'Submission failed.', true);
  } finally {
    workflowSubmitting = false;
    submitBtn.disabled = pendingUploads > 0;
  }
}


// Reset the workflow state and clear the UI elements
function resetWorkflow() {
  uploadGeneration += 1;
  selectedUploads = new Map();
  suggestedFeatures = null;
  suggestedFeatureSource = null;
  pendingUploads = 0;
  workflowSubmitting = false;
  submitBtn.disabled = false;
  fileSetupFeedbackEl.hidden = true;
  fileSetupFeedbackEl.textContent = '';
  formEl.reset();
  updateModelFields();
  if (assistantFormEl) assistantFormEl.reset();
  fieldsEl.querySelectorAll('input[type="file"]').forEach(updateUploadState);
  setupConversation = [];
  assistantResult = null;
  showModelTypeHint(false);
  assistantConversationEl.replaceChildren();
  assistantContinueBtn.hidden = true;
  assistantPromptEl.placeholder = 'For example: Explain my random forest predictions using a tabular dataset.';
  assistantStepEl.hidden = true;
  formEl.hidden = false;
  backToAssistantBtn.hidden = !assistantEnabled;
  workflowId = null;
  latestStatus = null;
  if (pollHandle) clearTimeout(pollHandle);
  timelineEl.innerHTML = '';
  outputEl.innerHTML = '<div class="empty-state">Run a workflow to see results here.</div>';
  setStatus('Uploads and configuration cleared.');
}


// Render the manifest configuration and apply the theme
function renderManifest() {
  showModelTypeHint(false);
  applyTheme();
  document.title = manifest.title || 'AI-EFFECT Workflow';
  pageTitle.textContent = manifest.title || 'AI-EFFECT Workflow';
  pageDescription.textContent = manifest.description || '';
  assistantEnabled = manifest.assistant?.enabled === true;
  assistantStepEl.hidden = !assistantEnabled;
  formEl.hidden = assistantEnabled;
  backToAssistantBtn.hidden = true;
  fieldsEl.innerHTML = (manifest.inputs || []).map(fieldHtml).join('');
  wireUploadControls();
  updateModelFields();
  document.getElementById(`field-${manifest.upload_config_field || 'config_json'}`)
    ?.addEventListener('input', updateModelFields);
  submitBtn.textContent = manifest.submit_label || 'Start workflow';
  pipelineBadge.textContent = `${(manifest.pipeline?.services || []).length} services`;
  renderActions();
  setStatus('Ready.');
}

// Apply the theme specified in the manifest to the document
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

async function loadManifest() {
  const response = await fetch('/ui/config');
  manifest = await response.json();
  if (!response.ok) {
    setStatus(manifest.detail || 'Workflow UI configuration failed to load.', true);
    return;
  }
  renderManifest();
}

formEl.addEventListener('submit', submitWorkflow);
assistantFormEl.addEventListener('submit', previewAnalysisSetup);
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

loadManifest();