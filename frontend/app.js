const fileInput = document.querySelector('#file-input');
const fileButton = document.querySelector('#file-button');
const dropzone = document.querySelector('#dropzone');
const fileName = document.querySelector('#file-name');
const logInput = document.querySelector('#log-input');
const charCount = document.querySelector('#char-count');
const analyzeButton = document.querySelector('#analyze-button');
const feedback = document.querySelector('#feedback');
const emptyState = document.querySelector('#empty-state');
const resultContent = document.querySelector('#result-content');
const errorList = document.querySelector('#error-list');
const patternCount = document.querySelector('#pattern-count');
const analysisStatus = document.querySelector('#analysis-status');
const analysisDiagnosis = document.querySelector('#analysis-diagnosis');
const resultState = document.querySelector('#result-state');
const providerStatus = document.querySelector('#provider-status');
const insightSection = document.querySelector('#insight-section');
const statCards = document.querySelector('#stat-cards');
const chartLegend = document.querySelector('#chart-legend');
const componentChart = document.querySelector('#component-chart');
const riskPriority = document.querySelector('#risk-priority');
const architectureCards = document.querySelector('#architecture-cards');

const CHART_CATEGORIES = [
  'Backend',
  'API',
  'Database',
  'Authentication',
  'Frontend',
  'Infrastructure',
  'Storage',
  'Networking',
  'Other',
];
const SEVERITIES = ['critical', 'high', 'medium', 'low'];

let currentInsights = null;

function setText(element, value) {
  if (!element) return;
  element.textContent = value == null ? '' : String(value);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null && text !== '') node.textContent = String(text);
  return node;
}

function formatConfidence(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 'n/a';
  const normalized = number > 1 && number <= 100 ? number / 100 : number;
  if (normalized < 0 || normalized > 1) return 'n/a';
  return `${Math.round(normalized * 100)}%`;
}

function refreshProviderStatus() {
  if (!providerStatus) return;
  fetch('/bedrock/status').then((response) => response.json()).then((data) => {
    setText(providerStatus, data.provider === 'bedrock' ? 'BEDROCK CONFIGURED' : 'LOCAL ANALYSIS');
  }).catch(() => {
    setText(providerStatus, 'SERVICE OFFLINE');
  });
}

fileButton.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));
logInput.addEventListener('input', () => {
  setText(charCount, `${logInput.value.length.toLocaleString()} characters`);
});
refreshProviderStatus();

['dragenter', 'dragover'].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.add('dragging');
}));
['dragleave', 'drop'].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.remove('dragging');
}));
dropzone.addEventListener('drop', (event) => handleFile(event.dataTransfer.files[0]));

async function handleFile(file) {
  if (!file) return;
  setText(fileName, file.name);
  if (!/\.(log|txt)$/i.test(file.name)) {
    setFeedback('Only .log and .txt files are supported.');
    return;
  }
  refreshProviderStatus();
  try {
    logInput.value = await file.text();
    logInput.dispatchEvent(new Event('input'));
    setFeedback('File loaded. Ready to analyze.');
  } catch {
    setFeedback('Could not read that file.');
  }
}

analyzeButton.addEventListener('click', async () => {
  const text = logInput.value;
  if (!text.trim()) {
    setFeedback('Add a log file or paste log text first.');
    logInput.focus();
    return;
  }
  analyzeButton.disabled = true;
  setText(resultState, 'PROCESSING');
  setFeedback('Running pattern analysis...');
  try {
    const response = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Analysis failed.');
    renderResults(data);
    setFeedback('Analysis complete.');
  } catch (error) {
    setText(resultState, 'ERROR');
    setFeedback(error.message || 'Could not reach the analysis service.');
  } finally {
    analyzeButton.disabled = false;
  }
});

function renderResults(data) {
  if (emptyState) emptyState.hidden = true;
  if (resultContent) resultContent.hidden = false;
  setText(resultState, 'COMPLETE');
  setText(patternCount, data.total_errors);
  setText(analysisStatus, (data.status || 'complete').toUpperCase());
  currentInsights = data.insights || null;
  renderAnalysisDiagnosis(data);
  renderInsights(currentInsights);
  if (!errorList) return;
  errorList.replaceChildren();
  if (!data.errors.length) {
    const emptyCard = el('div', 'error-card');
    emptyCard.appendChild(el('p', 'error-title', 'No error patterns found.'));
    const meta = el('div', 'error-meta');
    meta.appendChild(el('span', null, 'The submitted logs contain no stored warning or error records.'));
    emptyCard.appendChild(meta);
    errorList.appendChild(emptyCard);
    return;
  }
  data.errors.forEach((error) => errorList.appendChild(buildErrorCard(error)));
}

function buildErrorCard(error) {
  const card = el('article', 'error-card');
  const firstSeen = error.first_seen ? new Date(error.first_seen).toLocaleString() : 'unknown';
  const details = [
    `service: ${error.source_service || 'unknown'}`,
    `first seen: ${firstSeen}`,
    `samples: ${(error.sample_logs || []).join('\n') || 'none'}`,
  ].join('\n');

  const top = el('div', 'error-top');
  top.appendChild(el('h3', 'error-title', error.message));
  top.appendChild(el('span', 'severity', error.severity));
  card.appendChild(top);

  const meta = el('div', 'error-meta');
  meta.appendChild(el('span', 'occurrences', `${error.occurrences} occurrence${error.occurrences === 1 ? '' : 's'}`));
  meta.appendChild(el('span', 'error-type', error.error_type || 'untyped error'));
  card.appendChild(meta);
  card.appendChild(el('div', 'fingerprint', error.fingerprint));

  const actions = el('div', 'action-buttons');
  const detailToggle = el('button', 'detail-toggle', 'View evidence');
  detailToggle.type = 'button';
  const diagnoseButton = el('button', 'diagnose-button', 'Diagnose with AI');
  diagnoseButton.type = 'button';
  actions.append(detailToggle, diagnoseButton);
  card.appendChild(actions);

  const detailsElement = el('div', 'details', details);
  detailsElement.hidden = true;
  const diagnosisBox = el('div', 'diagnosis-box');
  diagnosisBox.hidden = true;
  card.append(detailsElement, diagnosisBox);

  detailToggle.addEventListener('click', () => {
    detailsElement.hidden = !detailsElement.hidden;
    setText(detailToggle, detailsElement.hidden ? 'View evidence' : 'Hide evidence');
  });

  diagnoseButton.addEventListener('click', async () => {
    diagnoseButton.disabled = true;
    diagnosisBox.hidden = false;
    diagnosisBox.replaceChildren(el('p', 'loading-diagnosis', 'Diagnosing error pattern with AI...'));
    try {
      const res = await fetch(`/errors/${error.fingerprint}/diagnose`, { method: 'POST' });
      const resData = await res.json();
      if (!res.ok) throw new Error(resData.detail || 'Diagnosis failed.');
      const diag = resData.diagnosis || resData;
      renderDiagnosisBox(diagnosisBox, diag);
      applyIssueDiagnosis(error.fingerprint, diag);
    } catch (err) {
      diagnosisBox.replaceChildren(el('p', 'error-diagnosis', err.message || 'AI diagnosis is temporarily unavailable.'));
    } finally {
      diagnoseButton.disabled = false;
    }
  });

  return card;
}

function appendSection(parent, label, value, asList) {
  if (value == null || value === '' || (Array.isArray(value) && !value.length)) return;
  const section = el('div', 'diag-section');
  section.appendChild(el('strong', null, `${label}:`));
  section.appendChild(document.createTextNode(' '));
  if (asList && Array.isArray(value)) {
    const list = document.createElement('ul');
    value.forEach((item) => {
      const text = typeof item === 'string' ? item : (item && item.observation) || JSON.stringify(item);
      list.appendChild(el('li', null, text));
    });
    section.appendChild(list);
  } else {
    section.appendChild(document.createTextNode(String(value)));
  }
  parent.appendChild(section);
}

function renderDiagnosisBox(container, diag) {
  if (!container) return;
  const content = el('div', 'diagnosis-content');
  content.appendChild(el('h4', null, 'AI Diagnosis'));
  appendSection(content, 'Summary', diag.summary);
  appendSection(content, 'What Happened', diag.what_happened);
  appendSection(content, 'Why It Happened', diag.why_it_happened);
  appendSection(content, 'Severity', diag.severity);
  appendSection(content, 'Priority', diag.priority);
  appendSection(content, 'Security Risk', diag.security_risk);
  appendSection(content, 'Root Cause', diag.root_cause);
  appendSection(content, 'Confidence', formatConfidence(diag.confidence));
  appendSection(content, 'Beginner Explanation', diag.beginner_explanation);
  appendSection(content, 'How To Fix', diag.recommended_fix);
  appendSection(content, 'Code Improvement', diag.code_improvement);
  if (diag.suggested_patch) {
    const section = el('div', 'diag-section');
    section.appendChild(el('strong', null, 'Suggested Patch:'));
    section.appendChild(el('pre', null, diag.suggested_patch));
    content.appendChild(section);
  }
  appendSection(content, 'Why It Improves Code', diag.why_this_improves_the_code);
  appendSection(content, 'Prevention Steps', diag.prevention_steps, true);
  appendSection(content, 'Verification Steps', diag.verification_steps, true);
  appendSection(content, 'Recommendations', diag.recommendations, true);
  appendSection(content, 'Possible impact', diag.impact, true);
  appendSection(content, 'Evidence', diag.evidence, true);
  appendSection(content, 'Limitations', diag.limitations, true);
  container.replaceChildren(content);
}

function renderAnalysisDiagnosis(data) {
  if (!analysisDiagnosis) return;
  if (data.diagnosis_error) {
    analysisDiagnosis.hidden = false;
    analysisDiagnosis.replaceChildren(el('p', 'error-diagnosis', data.diagnosis_error));
    return;
  }
  const diagnosis = data.diagnosis;
  if (!diagnosis) {
    analysisDiagnosis.hidden = true;
    analysisDiagnosis.replaceChildren();
    return;
  }
  analysisDiagnosis.hidden = false;
  renderDiagnosisBox(analysisDiagnosis, diagnosis);
}

function applyIssueDiagnosis(fingerprint, diag) {
  if (!currentInsights || !Array.isArray(currentInsights.issues)) return;
  const issue = currentInsights.issues.find((item) => item.fingerprint === fingerprint);
  if (issue) {
    if (diag.severity) issue.severity = String(diag.severity).toLowerCase();
    if (diag.priority) issue.priority = diag.priority;
    if (diag.security_risk) issue.security_risk = String(diag.security_risk).toLowerCase();
    if (diag.confidence != null && Number.isFinite(Number(diag.confidence))) {
      issue.confidence = Number(diag.confidence);
    }
  }
  currentInsights.stats = computeStats(currentInsights.issues);
  renderInsights(currentInsights);
}

function computeStats(issues) {
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  const riskCounts = { critical_high: 0, medium: 0, low: 0 };
  const priorityCounts = { P1: 0, P2: 0, P3: 0, P4: 0 };
  const byComponent = {};
  CHART_CATEGORIES.forEach((name) => {
    byComponent[name] = { critical: 0, high: 0, medium: 0, low: 0 };
  });
  issues.forEach((issue) => {
    const severity = SEVERITIES.includes(issue.severity) ? issue.severity : 'low';
    counts[severity] += 1;
    if (priorityCounts[issue.priority] != null) priorityCounts[issue.priority] += 1;
    if (issue.security_risk === 'critical' || issue.security_risk === 'high') riskCounts.critical_high += 1;
    else if (issue.security_risk === 'medium') riskCounts.medium += 1;
    else riskCounts.low += 1;
    const component = CHART_CATEGORIES.includes(issue.component) ? issue.component : 'Other';
    byComponent[component][severity] += 1;
  });
  const confidences = issues.map((issue) => Number(issue.confidence)).filter((value) => Number.isFinite(value));
  return {
    total: issues.length,
    critical: counts.critical,
    high: counts.high,
    medium: counts.medium,
    low: counts.low,
    high_critical_risk: riskCounts.critical_high,
    average_confidence: confidences.length ? confidences.reduce((sum, value) => sum + value, 0) / confidences.length : null,
    security_risk: riskCounts,
    priority: priorityCounts,
    by_component: byComponent,
  };
}

function renderInsights(insights) {
  if (!insightSection) return;
  if (!insights || !insights.stats) {
    insightSection.hidden = true;
    return;
  }
  insightSection.hidden = false;
  renderStatCards(insights.stats);
  renderArchitecture(insights.architecture || []);
  renderChart(insights.stats.by_component || {});
  renderRiskPriority(insights.stats);
}

function renderStatCards(stats) {
  if (!statCards) return;
  const items = [
    ['Total Issues', stats.total],
    ['Critical', stats.critical],
    ['High', stats.high],
    ['Medium', stats.medium],
    ['Low', stats.low],
    ['High/Critical Risk', stats.high_critical_risk],
    ['Average Confidence', formatConfidence(stats.average_confidence)],
  ];
  statCards.replaceChildren();
  items.forEach(([label, value]) => {
    const card = el('div', 'stat-card');
    card.appendChild(el('span', 'summary-label', label));
    card.appendChild(el('strong', null, value));
    statCards.appendChild(card);
  });
}

function renderArchitecture(items) {
  if (!architectureCards) return;
  architectureCards.replaceChildren();
  if (!items.length) {
    architectureCards.appendChild(el('p', 'architecture-empty', 'No architecture signals in the submitted logs.'));
    return;
  }
  items.forEach((item) => {
    const card = el('article', 'arch-card');
    card.appendChild(el('span', 'arch-category', item.category));
    card.appendChild(el('strong', 'arch-tech', item.technology));
    if (item.evidence) card.appendChild(el('span', 'arch-evidence', item.evidence));
    architectureCards.appendChild(card);
  });
}

function renderChart(byComponent) {
  if (!componentChart) return;
  const maxTotal = Math.max(
    1,
    ...CHART_CATEGORIES.map((name) => {
      const bucket = byComponent[name] || {};
      return SEVERITIES.reduce((sum, severity) => sum + (bucket[severity] || 0), 0);
    }),
  );
  const svgNs = 'http://www.w3.org/2000/svg';
  const width = 640;
  const height = 220;
  const padLeft = 36;
  const padBottom = 48;
  const padTop = 12;
  const plotHeight = height - padBottom - padTop;
  const groupWidth = (width - padLeft - 12) / CHART_CATEGORIES.length;

  const svg = document.createElementNS(svgNs, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Issues by architectural component and severity');

  const axis = document.createElementNS(svgNs, 'line');
  axis.setAttribute('x1', String(padLeft));
  axis.setAttribute('x2', String(width - 8));
  axis.setAttribute('y1', String(padTop + plotHeight));
  axis.setAttribute('y2', String(padTop + plotHeight));
  axis.setAttribute('class', 'chart-axis');
  svg.appendChild(axis);

  CHART_CATEGORIES.forEach((name, index) => {
    const bucket = byComponent[name] || {};
    let y = padTop + plotHeight;
    const x = padLeft + index * groupWidth + 8;
    const barWidth = Math.max(10, groupWidth - 16);
    SEVERITIES.forEach((severity) => {
      const count = bucket[severity] || 0;
      if (!count) return;
      const barHeight = (count / maxTotal) * plotHeight;
      y -= barHeight;
      const rect = document.createElementNS(svgNs, 'rect');
      rect.setAttribute('x', String(x));
      rect.setAttribute('y', String(y));
      rect.setAttribute('width', String(barWidth));
      rect.setAttribute('height', String(Math.max(barHeight, 1)));
      rect.setAttribute('class', `bar-${severity}`);
      rect.setAttribute('aria-label', `${name} ${severity}: ${count}`);
      svg.appendChild(rect);
    });
    const label = document.createElementNS(svgNs, 'text');
    label.setAttribute('x', String(x + barWidth / 2));
    label.setAttribute('y', String(height - 8));
    label.setAttribute('class', 'chart-tick');
    label.setAttribute('text-anchor', 'middle');
    label.textContent = name;
    svg.appendChild(label);
  });

  componentChart.replaceChildren(svg);
  if (chartLegend) {
    chartLegend.replaceChildren();
    SEVERITIES.forEach((severity) => {
      const item = el('span', 'legend-item');
      item.appendChild(el('i', `legend-swatch swatch-${severity}`));
      item.appendChild(document.createTextNode(severity));
      chartLegend.appendChild(item);
    });
  }
}

function renderRiskPriority(stats) {
  if (!riskPriority) return;
  riskPriority.replaceChildren();
  const risk = stats.security_risk || {};
  const priority = stats.priority || {};
  const groups = [
    ['Security risk', [
      ['Critical/High risk', risk.critical_high || 0],
      ['Medium risk', risk.medium || 0],
      ['Low risk', risk.low || 0],
    ]],
    ['Priority', [
      ['P1', priority.P1 || 0],
      ['P2', priority.P2 || 0],
      ['P3', priority.P3 || 0],
      ['P4', priority.P4 || 0],
    ]],
  ];
  groups.forEach(([title, rows]) => {
    const box = el('div', 'risk-box');
    box.appendChild(el('span', 'summary-label', title));
    rows.forEach(([label, value]) => {
      const row = el('div', 'risk-row');
      row.appendChild(el('span', null, label));
      row.appendChild(el('strong', null, value));
      box.appendChild(row);
    });
    riskPriority.appendChild(box);
  });
}

function setFeedback(message) {
  setText(feedback, message);
}
