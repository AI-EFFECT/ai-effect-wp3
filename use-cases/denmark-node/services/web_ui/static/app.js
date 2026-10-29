let manifest = null;
let workflowId = null;
let latestStatus = null;
let pollHandle = null;
let requestGeneration = 0;
let submitting = false;

const fieldsEl = document.getElementById('fields');
const formEl = document.getElementById('workflowForm');
const statusEl = document.getElementById('status');
const outputEl = document.getElementById('output');
const timelineEl = document.getElementById('timeline');
const actionsEl = document.getElementById('actions');
const submitBtn = document.getElementById('submitBtn');
const resetBtn = document.getElementById('resetBtn');
const outputTitle = document.getElementById('outputTitle');
const pipelineBadge = document.getElementById('pipelineBadge');
const pageTitle = document.getElementById('pageTitle');
const pageDescription = document.getElementById('pageDescription');
const activityLabelEl = document.getElementById('activityLabel');

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
  const hint = field.help ? `<details class="field-help"><summary>Help<span class="visually-hidden"> for ${escapeHtml(field.label || field.name)}</span></summary><div class="hint">${escapeHtml(field.help)}</div></details>` : '';
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
  } else if (field.type === 'textarea') {
    control = `<textarea class="form-control" id="field-${field.name}" name="${escapeHtml(field.name)}" ${placeholder} ${required}>${escapeHtml(value)}</textarea>`;
  } else {
    control = `<input class="form-control" id="field-${field.name}" name="${escapeHtml(field.name)}" type="text" value="${escapeHtml(value)}" ${placeholder} ${required}>`;
  }

  return `<div class="field">${label}${control}${hint}</div>`;
}

// Collect values from all input fields in the form
function collectInputs() {
  const result = {};
  for (const field of manifest.inputs || []) {
    const element = document.getElementById(`field-${field.name}`);
    if (!element) continue;

    result[field.name] = field.type === 'checkbox' ? element.checked : element.value;
    if (field.type === 'number' && result[field.name] !== '') {
      result[field.name] = Number(result[field.name]);
    }
  }
  return result;
}

// Update the status message displayed to the user
function setStatus(text, isError = false, activity = 'Ready') {
  statusEl.textContent = text;
  statusEl.classList.toggle('alert-secondary', !isError);
  statusEl.classList.toggle('alert-danger', isError);
  activityLabelEl.textContent = isError ? 'Needs attention' : activity;
}


function taskPresentation(task) {
  const method = task.method || task.method_name || task.node_key?.split(':')[1] || '';
  const labels = {
    BuildRunConfig: {
      title: 'Configuration preparation',
      description: 'Turns the request into supply and load settings.',
    },
    GenerateTopology: {
      title: 'Topology generation',
      description: 'Generates feasible networks and recommends a topology.',
    },
    RenderTopologies: {
      title: 'Topology visualization',
      description: 'Draws the generated networks for review.',
    },
  };
  return labels[method] || {
    title: method || task.service || task.service_name || 'Workflow task',
    description: '',
  };
}

function taskIdentifiers(task) {
  return `<div>Task ID: <code>${escapeHtml(task.task_id || '')}</code></div>
    ${task.node_key ? `<div>Operation: <code>${escapeHtml(task.node_key)}</code></div>` : ''}`;
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
        <strong>${escapeHtml(taskPresentation(task).title)}</strong>
        <div class="form-text">${escapeHtml(taskPresentation(task).description)}</div>
        <details class="technical-details"><summary>Technical details</summary>${taskIdentifiers(task)}</details>
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

// Render the workflow outputs, including summaries, images, and raw payloads
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

  outputEl.innerHTML = outputs.map(output => {
    const task = (statusData.tasks || []).find(task => task.task_id === output.task_id) || output;
    const summary = firstSummary(output.payload);
    const images = renderImages(statusData.workflow_id, output.payload);
    return `<article class="card output-card">
      <div class="card-body">
      <h3 class="h5 card-title">${escapeHtml(taskPresentation(task).title)}</h3>
      ${summary ? `<p class="summary">${escapeHtml(summary)}</p>` : ''}
      ${images}
      <details class="technical-details"><summary>Technical details</summary>${taskIdentifiers(task)}<pre>${escapeHtml(JSON.stringify(output.payload, null, 2))}</pre></details>
      </div>
    </article>`;
  }).join('');
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

  try {
    const response = await fetch('/decision', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({workflow_id: workflowId, action, value: selectedValue()}),
    });
    const data = await response.json();
    setStatus(data.detail || data.message || data.status || 'Decision recorded.', !response.ok);
  } catch (error) {
    setStatus(error.message || 'Could not record the decision.', true);
  }
}

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
      setStatus('Workflow completed.');
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


// Submit the workflow with the collected input values and start polling for its status
async function submitWorkflow(event) {
  event.preventDefault();
  if (submitting) return;
  const generation = ++requestGeneration;
  if (pollHandle) clearTimeout(pollHandle);

  submitBtn.disabled = true;
  submitting = true;
  submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Submitting...';
  formEl.setAttribute('aria-busy', 'true');
  actionsEl.hidden = true;
  setStatus('Submitting your experiment request...', false, 'Submitting');
  outputEl.innerHTML = '<div class="empty-state">Waiting for workflow output.</div>';

  try {
    const response = await fetch('/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({inputs: collectInputs()}),
    });
    const data = await response.json();
    if (generation !== requestGeneration) return;

    if (!response.ok) {
      setStatus(data.detail || 'Submission failed.', true);
      return;
    }

    workflowId = data.workflow_id;
    if (!workflowId) throw new Error('The server did not return a workflow ID.');
    setStatus(`Workflow started: ${workflowId}.`, false, 'Running');
    renderOutputs(data);
    pollWorkflow();
  } catch (error) {
    if (generation === requestGeneration) setStatus(error.message || 'Submission failed.', true);
  } finally {
    if (generation === requestGeneration) {
      submitting = false;
      submitBtn.disabled = false;
      submitBtn.textContent = manifest.submit_label || 'Start workflow';
      formEl.setAttribute('aria-busy', 'false');
    }
  }
}


// Reset the workflow state and clear the UI elements
function resetWorkflow() {
  requestGeneration += 1;
  submitting = false;
  submitBtn.disabled = false;
  submitBtn.textContent = manifest.submit_label || 'Start workflow';
  formEl.setAttribute('aria-busy', 'false');
  actionsEl.hidden = true;
  formEl.reset();
  workflowId = null;
  latestStatus = null;
  if (pollHandle) clearTimeout(pollHandle);
  timelineEl.innerHTML = '';
  outputEl.innerHTML = '<div class="empty-state">Run a workflow to see results here.</div>';
  setStatus('Ready.');
}


// Render the manifest configuration and apply the theme
function renderManifest() {
  applyTheme();
  document.title = manifest.title || 'AI-EFFECT Workflow';
  pageTitle.textContent = manifest.title || 'AI-EFFECT Workflow';
  pageDescription.textContent = manifest.description || '';
  fieldsEl.innerHTML = (manifest.inputs || []).map(fieldHtml).join('');
  submitBtn.textContent = manifest.submit_label || 'Start workflow';
  pipelineBadge.textContent = `${(manifest.pipeline?.services || []).length} services`;
  renderActions();
  actionsEl.hidden = true;
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
resetBtn.addEventListener('click', resetWorkflow);
actionsEl.addEventListener('click', event => {
  const button = event.target.closest('button[data-action]');
  if (button) recordDecision(button.dataset.action);
});

loadManifest();