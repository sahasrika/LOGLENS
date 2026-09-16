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
const resultState = document.querySelector('#result-state');

fileButton.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));
logInput.addEventListener('input', () => {
  charCount.textContent = `${logInput.value.length.toLocaleString()} characters`;
});

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
  fileName.textContent = file.name;
  if (!/\.(log|txt)$/i.test(file.name)) {
    setFeedback('Only .log and .txt files are supported.');
    return;
  }
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
  resultState.textContent = 'PROCESSING';
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
    resultState.textContent = 'ERROR';
    setFeedback(error.message || 'Could not reach the analysis service.');
  } finally {
    analyzeButton.disabled = false;
  }
});

function renderResults(data) {
  emptyState.hidden = true;
  resultContent.hidden = false;
  resultState.textContent = 'COMPLETE';
  patternCount.textContent = data.total_errors;
  analysisStatus.textContent = (data.status || 'complete').toUpperCase();
  errorList.replaceChildren();
  if (!data.errors.length) {
    errorList.innerHTML = '<div class="error-card"><p class="error-title">No error patterns found.</p><div class="error-meta">The submitted logs contain no stored warning or error records.</div></div>';
    return;
  }
  data.errors.forEach((error) => {
    const card = document.createElement('article');
    card.className = 'error-card';
    const firstSeen = error.first_seen ? new Date(error.first_seen).toLocaleString() : 'unknown';
    const details = [
      `service: ${error.source_service || 'unknown'}`,
      `first seen: ${firstSeen}`,
      `samples: ${(error.sample_logs || []).join('\n') || 'none'}`,
    ].join('\n');
    card.innerHTML = `
      <div class="error-top">
        <h3 class="error-title"></h3>
        <span class="severity"></span>
      </div>
      <div class="error-meta"><span class="occurrences"></span><span class="error-type"></span></div>
      <div class="fingerprint"></div>
      <div class="action-buttons">
        <button class="detail-toggle" type="button">View evidence</button>
        <button class="diagnose-button" type="button">Diagnose with AI</button>
      </div>
      <div class="details" hidden></div>
      <div class="diagnosis-box" hidden></div>`;
    card.querySelector('.error-title').textContent = error.message;
    card.querySelector('.severity').textContent = error.severity;
    card.querySelector('.occurrences').textContent = `${error.occurrences} occurrence${error.occurrences === 1 ? '' : 's'}`;
    card.querySelector('.error-type').textContent = error.error_type || 'untyped error';
    card.querySelector('.fingerprint').textContent = error.fingerprint;
    card.querySelector('.details').textContent = details;
    card.querySelector('.detail-toggle').addEventListener('click', (event) => {
      const detailsElement = card.querySelector('.details');
      detailsElement.hidden = !detailsElement.hidden;
      event.currentTarget.textContent = detailsElement.hidden ? 'View evidence' : 'Hide evidence';
    });
    card.querySelector('.diagnose-button').addEventListener('click', async (event) => {
      const button = event.currentTarget;
      const diagnosisBox = card.querySelector('.diagnosis-box');
      button.disabled = true;
      diagnosisBox.hidden = false;
      diagnosisBox.innerHTML = '<p class="loading-diagnosis">Diagnosing error pattern with AI...</p>';
      try {
        const res = await fetch(`/errors/${error.fingerprint}/diagnose`, { method: 'POST' });
        const resData = await res.json();
        if (!res.ok) throw new Error(resData.detail || 'Diagnosis failed.');
        const diag = resData.diagnosis || resData;
        diagnosisBox.innerHTML = `
          <div class="diagnosis-content">
            <h4>AI Diagnosis</h4>
            <div class="diag-section"><strong>Summary:</strong> ${diag.summary}</div>
            ${diag.what_happened ? `<div class="diag-section"><strong>What Happened:</strong> ${diag.what_happened}</div>` : ''}
            ${diag.why_it_happened ? `<div class="diag-section"><strong>Why It Happened:</strong> ${diag.why_it_happened}</div>` : ''}
            <div class="diag-section"><strong>Root Cause:</strong> ${diag.root_cause}</div>
            <div class="diag-section"><strong>Confidence:</strong> ${(diag.confidence * 100).toFixed(0)}%</div>
            ${diag.beginner_explanation ? `<div class="diag-section"><strong>Beginner Explanation:</strong> ${diag.beginner_explanation}</div>` : ''}
            ${diag.recommended_fix ? `<div class="diag-section"><strong>How To Fix:</strong> ${diag.recommended_fix}</div>` : ''}
            ${diag.code_improvement ? `<div class="diag-section"><strong>Code Improvement:</strong> ${diag.code_improvement}</div>` : ''}
            ${diag.suggested_patch ? `<div class="diag-section"><strong>Suggested Patch:</strong> <pre>${diag.suggested_patch}</pre></div>` : ''}
            ${diag.why_this_improves_the_code ? `<div class="diag-section"><strong>Why It Improves Code:</strong> ${diag.why_this_improves_the_code}</div>` : ''}
            ${diag.prevention_steps && diag.prevention_steps.length ? `<div class="diag-section"><strong>Prevention Steps:</strong><ul>${diag.prevention_steps.map(s => `<li>${s}</li>`).join('')}</ul></div>` : ''}
            ${diag.verification_steps && diag.verification_steps.length ? `<div class="diag-section"><strong>Verification Steps:</strong><ul>${diag.verification_steps.map(s => `<li>${s}</li>`).join('')}</ul></div>` : ''}
            ${diag.recommendations && diag.recommendations.length ? `<div class="diag-section"><strong>Recommendations:</strong><ul>${diag.recommendations.map(r => `<li>${r}</li>`).join('')}</ul></div>` : ''}
          </div>`;
      } catch (err) {
        diagnosisBox.innerHTML = `<p class="error-diagnosis">${err.message || 'AI diagnosis is temporarily unavailable.'}</p>`;
      } finally {
        button.disabled = false;
      }
    });
    errorList.appendChild(card);
  });
}

function setFeedback(message) {
  feedback.textContent = message;
}
