/**
 * app.js — Digital Contract Hub Frontend Logic
 * Handles: navigation, contract upload, dashboard, RAG chat, PDF viewer, modal detail
 */

const API_BASE = (window.location.protocol === 'file:' || !window.location.host.includes(':8000'))
  ? 'http://127.0.0.1:8000'
  : window.location.origin;

// ── State ─────────────────────────────────────────────
let allContracts = [];
let searchDebounceTimer = null;
let currentDetailId = null;
let pdfPanelOpen = false;

// ── DOM helpers ───────────────────────────────────────
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function getApiKey() {
  return ($('#globalApiKey')?.value || '').trim();
}

// ── Navigation ────────────────────────────────────────
function showView(view) {
  $$('.view').forEach(v => v.classList.remove('active'));
  $$('.nav-item').forEach(n => n.classList.remove('active'));
  $(`#view-${view}`)?.classList.add('active');
  $(`#nav-${view}`)?.classList.add('active');
  if (view === 'dashboard') loadContracts();
  if (view === 'chat') populateScopeSelector();
}

// ── Toast Notifications ───────────────────────────────
function showToast(msg, type = 'info', duration = 3500) {
  const toast = $('#toast');
  toast.textContent = msg;
  toast.className = `toast ${type} show`;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove('show'), duration);
}

// ── Dashboard ─────────────────────────────────────────
async function loadContracts() {
  const search = $('#searchInput')?.value.trim() || '';
  const status = $('#statusFilter')?.value || 'all';

  $('#tableLoading').style.display = 'flex';
  $('#contractsTable').style.display = 'none';
  $('#emptyState').style.display   = 'none';

  try {
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (status !== 'all') params.set('status', status);

    const res = await fetch(`${API_BASE}/api/contracts?${params}`);
    const data = await res.json();
    allContracts = data.contracts || [];
    renderContracts(allContracts);
    renderStats(allContracts);
  } catch (err) {
    showToast('Không thể kết nối tới server.', 'error');
    console.error(err);
  } finally {
    $('#tableLoading').style.display = 'none';
  }
}

function renderStats(contracts) {
  const today = new Date();
  const in90  = new Date(); in90.setDate(today.getDate() + 90);

  const active  = contracts.filter(c => c.status === 'Active').length;
  const expired = contracts.filter(c => c.status === 'Expired').length;
  const soon = contracts.filter(c => {
    if (c.status !== 'Active' || !c.expiration_date) return false;
    const d = new Date(c.expiration_date);
    return d >= today && d <= in90;
  }).length;

  $('#statTotal').textContent       = contracts.length;
  $('#statActive').textContent      = active;
  $('#statExpiringSoon').textContent = soon;
  $('#statExpired').textContent     = expired;
}

function renderContracts(contracts) {
  const tbody = $('#contractsBody');
  tbody.innerHTML = '';

  if (!contracts.length) {
    $('#emptyState').style.display = 'flex';
    return;
  }

  $('#contractsTable').style.display = 'table';
  const today = new Date();
  const in90 = new Date(); in90.setDate(today.getDate() + 90);

  contracts.forEach(c => {
    const expDate = c.expiration_date ? new Date(c.expiration_date) : null;
    let statusBadge = '';
    if (c.status === 'Active') {
      if (expDate && expDate <= in90) {
        statusBadge = `<span class="badge badge-warning"><i class="fa-solid fa-clock"></i> Sắp hết hạn</span>`;
      } else {
        statusBadge = `<span class="badge badge-active"><i class="fa-solid fa-circle-dot"></i> Đang hiệu lực</span>`;
      }
    } else if (c.status === 'Expired') {
      statusBadge = `<span class="badge badge-expired"><i class="fa-solid fa-circle-xmark"></i> Hết hạn</span>`;
    } else {
      statusBadge = `<span class="badge badge-terminated">Đã chấm dứt</span>`;
    }

    const value = c.total_value
      ? `${Number(c.total_value).toLocaleString('vi-VN')} ${c.currency || ''}`
      : '<span style="color:var(--text-muted)">—</span>';

    const expDisplay = c.expiration_date
      ? `<span style="${expDate && expDate <= in90 ? 'color:var(--warning)' : ''}">${formatDate(c.expiration_date)}</span>`
      : '<span style="color:var(--text-muted)">—</span>';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="cell-filename">
        <div>${escapeHtml(c.file_name)}</div>
        <small>${c.contract_type || ''}</small>
      </td>
      <td class="cell-truncate" title="${escapeHtml(c.party_a || '')}">${escapeHtml(c.party_a || '—')}</td>
      <td class="cell-truncate" title="${escapeHtml(c.party_b || '')}">${escapeHtml(c.party_b || '—')}</td>
      <td>${escapeHtml(c.contract_type || '—')}</td>
      <td class="cell-value">${value}</td>
      <td class="cell-date">${expDisplay}</td>
      <td>${statusBadge}</td>
      <td>
        <div class="row-actions">
          <button class="btn-icon primary" title="Xem chi tiết" onclick="openDetail('${c.id}')">
            <i class="fa-solid fa-eye"></i>
          </button>
          <button class="btn-icon" title="Hỏi đáp về hợp đồng này" onclick="chatWithContract('${c.id}', '${escapeHtml(c.file_name)}')">
            <i class="fa-solid fa-comments"></i>
          </button>
          <button class="btn-icon danger" title="Xóa" onclick="deleteContract('${c.id}', '${escapeHtml(c.file_name)}')">
            <i class="fa-solid fa-trash"></i>
          </button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function debounceSearch() {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(loadContracts, 400);
}

// ── Contract Detail Modal ─────────────────────────────
async function openDetail(contractId) {
  currentDetailId = contractId;
  try {
    const res = await fetch(`${API_BASE}/api/contracts/${contractId}`);
    if (!res.ok) throw new Error('Not found');
    const c = await res.json();
    renderDetailModal(c);
    $('#detailModal').style.display = 'flex';
  } catch (err) {
    showToast('Không thể tải chi tiết hợp đồng.', 'error');
  }
}

function renderDetailModal(c) {
  $('#modalTitle').textContent = c.file_name;

  const fields = [
    { label: 'Bên A (Party A)', value: c.party_a },
    { label: 'Bên B (Party B)', value: c.party_b },
    { label: 'Loại hợp đồng', value: c.contract_type },
    { label: 'Trạng thái', value: c.status },
    { label: 'Ngày hiệu lực', value: formatDate(c.effective_date) },
    { label: 'Ngày hết hạn', value: formatDate(c.expiration_date) },
    { label: 'Thời hạn báo trước', value: c.renewal_notice_days ? `${c.renewal_notice_days} ngày` : null },
    { label: 'Giá trị hợp đồng', value: c.total_value ? `${Number(c.total_value).toLocaleString('vi-VN')} ${c.currency || ''}` : null, highlight: true },
    { label: 'Luật áp dụng', value: c.governing_law },
    { label: 'Tải lên lúc', value: c.uploaded_at },
  ];

  const gridHtml = `
    <div class="detail-grid">
      ${fields.map(f => `
        <div class="detail-field">
          <div class="detail-label">${f.label}</div>
          <div class="detail-value ${f.highlight ? 'highlight' : ''}">${f.value || '<span style="color:var(--text-muted)">—</span>'}</div>
        </div>
      `).join('')}
    </div>
  `;

  const clausesHtml = c.clauses && c.clauses.length > 0 ? `
    <div class="clauses-section">
      <h3><i class="fa-solid fa-list-check"></i> Điều khoản cốt lõi (${c.clauses.length} điều khoản)</h3>
      <div class="clause-list">
        ${c.clauses.map(cl => `
          <div class="clause-item" onclick="openClausePdf('${c.id}', ${cl.page_number})">
            <div class="clause-header">
              <span class="clause-type type-${cl.clause_type}">${cl.clause_type}</span>
              <span class="clause-page"><i class="fa-solid fa-file-lines"></i> Trang ${cl.page_number}</span>
            </div>
            <div class="clause-title">${escapeHtml(cl.section_title || '')}</div>
            <div class="clause-summary">${escapeHtml(cl.summary || '')}</div>
          </div>
        `).join('')}
      </div>
    </div>
  ` : `<p style="color:var(--text-muted);font-size:0.875rem">Chưa trích xuất được điều khoản nào.</p>`;

  const actionsHtml = `
    <div class="modal-actions">
      <a href="${API_BASE}/api/contracts/${c.id}/pdf" target="_blank" class="btn btn-secondary">
        <i class="fa-solid fa-file-pdf"></i> Xem PDF gốc
      </a>
      <button class="btn btn-primary" onclick="chatWithContract('${c.id}', '${escapeHtml(c.file_name)}'); closeDetailModal()">
        <i class="fa-solid fa-comments"></i> Hỏi đáp về hợp đồng này
      </button>
    </div>
  `;

  $('#modalBody').innerHTML = gridHtml + clausesHtml + actionsHtml;
}

function openClausePdf(contractId, pageNumber) {
  closeDetailModal();
  showView('chat');
  setTimeout(() => openPdfToPage(contractId, pageNumber), 300);
}

function closeDetailModal() {
  $('#detailModal').style.display = 'none';
}

function closeModal(event) {
  if (event.target === $('#detailModal')) closeDetailModal();
}

// ── Upload ────────────────────────────────────────────
let selectedFile = null;

function handleDragOver(e) {
  e.preventDefault();
  $('#dropZone').classList.add('drag-over');
}
function handleDragLeave(e) {
  $('#dropZone').classList.remove('drag-over');
}
function handleDrop(e) {
  e.preventDefault();
  $('#dropZone').classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
}
function handleFileSelect(e) {
  const f = e.target.files[0];
  if (f) setFile(f);
}

function setFile(f) {
  const allowed = ['application/pdf', 'image/png', 'image/jpeg', 'image/webp'];
  if (!allowed.includes(f.type) && !f.name.match(/\.(pdf|png|jpg|jpeg|webp)$/i)) {
    showToast('Định dạng file không được hỗ trợ.', 'error');
    return;
  }
  selectedFile = f;
  $('#selectedFileName').textContent = f.name;
  $('#selectedFileSize').textContent = formatBytes(f.size);
  const icon = f.type === 'application/pdf' ? 'fa-file-pdf' : 'fa-file-image';
  $('#selectedFileIcon').className = `fa-solid ${icon}`;
  $('#selectedFile').style.display = 'flex';
  $('#uploadBtn').disabled = false;
}

function clearFile() {
  selectedFile = null;
  $('#selectedFile').style.display = 'none';
  $('#uploadBtn').disabled = true;
  $('#fileInput').value = '';
}

function resetUpload() {
  clearFile();
  $('#uploadResult').style.display = 'none';
  $('#processingState').style.display = 'flex';
  $('#successState').style.display = 'none';
  $('#errorState').style.display = 'none';
  // Reset steps
  $$('.proc-step').forEach((s, i) => {
    s.className = 'proc-step' + (i === 0 ? ' active' : '');
  });
}

function advanceStep(stepIndex) {
  const steps = $$('.proc-step');
  steps.forEach((s, i) => {
    if (i < stepIndex)   s.className = 'proc-step done';
    else if (i === stepIndex) s.className = 'proc-step active';
    else                 s.className = 'proc-step';
  });
}

async function uploadContract() {
  if (!selectedFile) return;

  const apiKey = getApiKey();
  if (!apiKey) {
    showToast('Vui lòng nhập Gemini API Key ở sidebar.', 'warning');
    return;
  }

  $('#uploadResult').style.display = 'flex';
  $('#processingState').style.display = 'flex';
  $('#successState').style.display = 'none';
  $('#errorState').style.display   = 'none';
  $('#uploadBtn').disabled = true;

  advanceStep(0);

  const formData = new FormData();
  formData.append('file', selectedFile);

  try {
    advanceStep(1);
    // Simulate step advancement (actual processing is server-side)
    setTimeout(() => advanceStep(2), 3000);
    setTimeout(() => advanceStep(3), 6000);

    const url = `${API_BASE}/api/contracts/upload?gemini_api_key=${encodeURIComponent(apiKey)}`;
    const res = await fetch(url, { method: 'POST', body: formData });
    const data = await res.json();

    if (!res.ok) throw new Error(data.detail || 'Upload failed');

    // Success
    const s = data.summary;
    $('#extractedSummary').innerHTML = `
      <div class="summ-item"><span class="summ-label">Bên A</span><span class="summ-value">${s.party_a || '—'}</span></div>
      <div class="summ-item"><span class="summ-label">Bên B</span><span class="summ-value">${s.party_b || '—'}</span></div>
      <div class="summ-item"><span class="summ-label">Loại hợp đồng</span><span class="summ-value">${s.contract_type || '—'}</span></div>
      <div class="summ-item"><span class="summ-label">Trạng thái</span><span class="summ-value">${s.status}</span></div>
      <div class="summ-item"><span class="summ-label">Ngày hiệu lực</span><span class="summ-value">${formatDate(s.effective_date)}</span></div>
      <div class="summ-item"><span class="summ-label">Ngày hết hạn</span><span class="summ-value">${formatDate(s.expiration_date)}</span></div>
      <div class="summ-item"><span class="summ-label">Giá trị</span><span class="summ-value">${s.total_value ? Number(s.total_value).toLocaleString('vi-VN') + ' ' + (s.currency || '') : '—'}</span></div>
      <div class="summ-item"><span class="summ-label">Điều khoản</span><span class="summ-value">${s.clauses_found} điều khoản / ${s.pages_extracted} trang</span></div>
    `;

    $('#viewDetailBtn').onclick = () => {
      openDetail(data.contract_id);
      showView('dashboard');
    };

    $('#processingState').style.display = 'none';
    $('#successState').style.display = 'flex';
    showToast('Hợp đồng đã được số hóa thành công!', 'success');
    clearFile();
  } catch (err) {
    $('#processingState').style.display = 'none';
    $('#errorState').style.display = 'flex';
    $('#errorMsg').textContent = err.message;
    showToast('Có lỗi xảy ra khi xử lý hợp đồng.', 'error');
  }
}

// ── Delete ────────────────────────────────────────────
async function deleteContract(contractId, fileName) {
  if (!confirm(`Bạn có chắc muốn xóa hợp đồng "${fileName}"?\nThao tác này không thể hoàn tác.`)) return;
  try {
    const res = await fetch(`${API_BASE}/api/contracts/${contractId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Delete failed');
    showToast('Đã xóa hợp đồng.', 'success');
    loadContracts();
  } catch (err) {
    showToast('Không thể xóa hợp đồng.', 'error');
  }
}

// ── Chat / RAG ────────────────────────────────────────
let chatHistory = [];

function populateScopeSelector() {
  const sel = $('#chatScope');
  const current = sel.value;
  sel.innerHTML = '<option value="all">Tất cả hợp đồng</option>';
  allContracts.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = c.file_name;
    sel.appendChild(opt);
  });
  sel.value = current || 'all';
}

function chatWithContract(contractId, fileName) {
  showView('chat');
  populateScopeSelector();
  setTimeout(() => {
    const sel = $('#chatScope');
    sel.value = contractId;
    showToast(`Đã thu hẹp phạm vi: ${fileName}`, 'info', 2500);
  }, 200);
}

function handleChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
}

function askSample(btn) {
  const q = btn.textContent;
  $('#chatInput').value = q;
  sendChat();
}

async function sendChat() {
  const input = $('#chatInput');
  const question = input.value.trim();
  if (!question) return;

  const apiKey = getApiKey();
  if (!apiKey) {
    showToast('Vui lòng nhập Gemini API Key ở sidebar.', 'warning');
    return;
  }

  const scope = $('#chatScope').value;
  const contractIds = scope === 'all' ? null : [scope];

  // Clear welcome message on first question
  const welcome = $('.chat-welcome');
  if (welcome) welcome.remove();

  appendChatMessage('user', question);
  input.value = '';
  input.style.height = 'auto';
  $('#sendBtn').disabled = true;

  const typingId = appendTypingIndicator();

  try {
    const body = {
      question,
      contract_ids: contractIds,
      gemini_api_key: apiKey,
    };

    const res = await fetch(`${API_BASE}/api/chat/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    removeTypingIndicator(typingId);

    if (!res.ok) throw new Error(data.detail || 'Query failed');

    appendChatMessage('assistant', data.answer, data.sources);
  } catch (err) {
    removeTypingIndicator(typingId);
    appendChatMessage('assistant', `⚠️ Lỗi: ${err.message}`, []);
  } finally {
    $('#sendBtn').disabled = false;
    input.focus();
  }
}

function appendChatMessage(role, text, sources = []) {
  const container = $('#chatMessages');
  const time = new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });

  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;

  // Format text (turn **bold** and [citations] into html)
  let htmlText = escapeHtml(text)
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');

  let chipsHtml = '';
  if (sources && sources.length > 0) {
    chipsHtml = `<div class="citation-chips">
      ${sources.map(s => `
        <span class="citation-chip"
              onclick="openPdfToPage('${s.contract_id}', ${s.page_number})">
          <i class="fa-solid fa-file-lines"></i>
          ${escapeHtml(s.file_name)}, Trang ${s.page_number}
        </span>
      `).join('')}
    </div>`;
  }

  div.innerHTML = `
    <div class="msg-bubble">${htmlText}</div>
    ${chipsHtml}
    <div class="msg-time">${time}</div>
  `;

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

let typingCounter = 0;
function appendTypingIndicator() {
  const id = `typing-${++typingCounter}`;
  const container = $('#chatMessages');
  const div = document.createElement('div');
  div.id = id;
  div.className = 'chat-msg assistant';
  div.innerHTML = `
    <div class="typing-indicator">
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    </div>
  `;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return id;
}

function removeTypingIndicator(id) {
  document.getElementById(id)?.remove();
}

// ── PDF Viewer ────────────────────────────────────────
function openPdfToPage(contractId, pageNumber) {
  const panel = $('#pdfPanel');
  const viewer = $('#pdfViewer');
  const empty = $('#pdfEmpty');
  const layout = $('.chat-layout');

  const url = `${API_BASE}/api/contracts/${contractId}/pdf#page=${pageNumber}`;
  viewer.src = url;
  viewer.style.display = 'block';
  empty.style.display = 'none';

  const contract = allContracts.find(c => c.id === contractId);
  $('#pdfPanelTitle').textContent = contract
    ? `${contract.file_name} — Trang ${pageNumber}`
    : `Trang ${pageNumber}`;

  pdfPanelOpen = true;
  layout?.classList.add('pdf-open');
}

function closePdfPanel() {
  const viewer = $('#pdfViewer');
  const empty = $('#pdfEmpty');
  const layout = $('.chat-layout');

  viewer.src = '';
  viewer.style.display = 'none';
  empty.style.display = 'flex';
  pdfPanelOpen = false;
  layout?.classList.remove('pdf-open');
}

// ── Utility Helpers ───────────────────────────────────
function formatDate(dateStr) {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
  } catch { return dateStr; }
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ── Initialise ────────────────────────────────────────
window.addEventListener('load', () => {
  showView('dashboard');
});
