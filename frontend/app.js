// =============================================================================
// CẤU HÌNH API BASE URL
// =============================================================================
const API_BASE = '/api/v1';

// =============================================================================
// TOAST NOTIFICATIONS HELPER (Không làm chặn giao diện)
// =============================================================================

function showToast(message, type = 'success') {
    const toastEl = document.getElementById('liveToast');
    const toastBody = document.getElementById('toastMessage');
    if (!toastEl || !toastBody) {
        console.log(`[${type}] ${message}`);
        return;
    }
    
    // Set màu sắc Bootstrap: success, danger, warning, info
    toastEl.className = `toast align-items-center text-bg-${type} border-0 shadow-lg`;
    toastBody.innerHTML = message;
    
    if (window.bootstrap && bootstrap.Toast) {
        const toast = bootstrap.Toast.getOrCreateInstance(toastEl, { delay: 3000 });
        toast.show();
    }
}

// =============================================================================
// TRANG 1: BẢNG ĐIỀU KHIỂN (INDEX.HTML)
// =============================================================================

async function fetchStatus() {
    const statusText = document.getElementById('scan-status-text');
    const statusDot = document.getElementById('scan-status-dot');
    const toggle = document.getElementById('autoScanToggle');
    
    if (!statusText) return;

    try {
        const response = await fetch(`${API_BASE}/auto-scan/status`);
        const data = await response.json();
        
        if (data.is_scanning) {
            const prog = data.current_progress ? ` (${data.current_progress})` : '';
            statusText.innerHTML = `<span class="text-warning fw-bold"><span class="spinner-border spinner-border-sm me-1"></span> Đang quét & xử lý${prog}</span>`;
            statusDot.className = 'status-dot ms-2 bg-warning shadow';
        } else if (data.auto_scan_enabled) {
            statusText.textContent = `Đang quét nền tự động (Mỗi ${data.interval_seconds}s)`;
            statusDot.className = 'status-dot ms-2 status-on';
            toggle.checked = true;
        } else {
            const lastInfo = data.last_scan_message ? ` [${data.last_scan_message}]` : '';
            statusText.textContent = `Đang Tắt tự động quét${lastInfo}`;
            statusDot.className = 'status-dot ms-2 status-off';
            toggle.checked = false;
        }

        const modelSelect = document.getElementById('aiModel');
        if (modelSelect && data.active_model) {
            modelSelect.value = data.active_model;
        }
    } catch (error) {
        console.error('Lỗi khi lấy trạng thái:', error);
        statusText.textContent = 'Mất kết nối tới API Backend';
        statusDot.className = 'status-dot ms-2 status-off';
    }
}

async function toggleAutoScan() {
    const toggle = document.getElementById('autoScanToggle');
    const isEnabled = toggle.checked;

    try {
        const response = await fetch(`${API_BASE}/auto-scan/toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: isEnabled })
        });
        const data = await response.json();
        fetchStatus();
        showToast(isEnabled ? '🔔 Đã BẬT chế độ quét nền tự động!' : '🔕 Đã TẮT chế độ quét nền tự động.', 'info');
    } catch (error) {
        console.error('Lỗi toggle auto-scan:', error);
        toggle.checked = !isEnabled;
        showToast('Lỗi: Không thể kết nối tới Backend để đổi trạng thái.', 'danger');
    }
}

async function triggerScan() {
    const btn = document.getElementById('triggerBtn');
    if (!btn) return;

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang gửi yêu cầu...';

    try {
        const response = await fetch(`${API_BASE}/auto-scan/trigger`, {
            method: 'POST'
        });
        const data = await response.json();
        if (data.running) {
            showToast('🔄 Hệ thống đang trong tiến trình quét & xử lý ảnh từ Drive trong nền...', 'info');
        } else if (data.processed_count > 0) {
            showToast(`⚡ Quét hoàn tất! Đã xử lý & đẩy lên Sheet: <strong>${data.processed_count}</strong> hóa đơn mới.`, 'success');
        } else {
            showToast('ℹ️ Quét hoàn tất: Không phát hiện hóa đơn mới nào (các file trong Drive đã được xử lý hoặc bị trùng lặp).', 'info');
        }
        fetchStatus();
    } catch (error) {
        console.error('Lỗi quét tức thì:', error);
        showToast('Lỗi: Không thể kết nối tới Backend.', 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-play-fill me-1"></i> Quét Ngay';
    }
}

async function stopScan() {
    const btn = document.getElementById('stopBtn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Đang dừng...';
    }
    try {
        const response = await fetch(`${API_BASE}/auto-scan/stop`, { method: 'POST' });
        const data = await response.json();
        showToast('🛑 Đã gửi lệnh dừng quét thành công!', 'warning');
        fetchStatus();
    } catch (error) {
        console.error('Lỗi dừng quét:', error);
        showToast('Lỗi: Không thể gửi lệnh dừng quét tới máy chủ.', 'danger');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-stop-fill me-1"></i> Dừng Quét';
        }
    }
}

async function saveModel() {
    const modelSelect = document.getElementById('aiModel');
    if (!modelSelect) return;
    
    const selectedModel = modelSelect.value;
    localStorage.setItem('selected_ai_model', selectedModel);

    try {
        const response = await fetch(`${API_BASE}/config/model`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: selectedModel })
        });
        const data = await response.json();
        if (response.ok) {
            showToast(`🎉 Đã áp dụng Model [${selectedModel}] cho toàn bộ hệ thống!`, 'success');
            if (typeof fetchStatus === 'function') {
                fetchStatus();
            }
        } else {
            showToast(`Lỗi khi lưu cấu hình: ${data.detail || 'Không xác định'}`, 'danger');
        }
    } catch (e) {
        console.error('Lỗi lưu model:', e);
        showToast(`Lỗi kết nối Backend: ${e.message}`, 'danger');
    }
}


// =============================================================================
// TRANG 2: ĐỐI CHIẾU HÓA ĐƠN (VERIFY.HTML)
// =============================================================================

let cachedRecords = [];
let currentSelectedDt = null;
let currentFile = null;
let currentMode = 'history'; // 'history' hoặc 'upload'

function resetForm() {
    const fieldIds = [
        'f_dt_code', 'f_date', 'f_company', 'f_seller_address', 'f_buyer_address',
        'f_order_id', 'f_description', 'f_quantity', 'f_unit_price', 'f_vat_rate',
        'f_vat_amount', 'f_total_amount', 'f_buyer_name', 'f_notes'
    ];
    fieldIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });

    const preview = document.getElementById('receiptPreview');
    const prompt = document.getElementById('uploadPrompt');
    const promptTitle = document.getElementById('promptTitle');

    if (preview) {
        preview.src = '';
        preview.classList.add('d-none');
    }
    if (prompt) {
        prompt.classList.remove('d-none');
    }
    if (promptTitle) {
        promptTitle.textContent = currentMode === 'upload' 
            ? "Bấm vào đây để chọn ảnh hóa đơn từ máy tính" 
            : "Vui lòng chọn một hóa đơn từ danh sách trên";
    }

    // Reset 3 Status Boxes
    const boxCat = document.getElementById('boxCategoryTag') || document.getElementById('boxCategory');
    const iconCat = document.getElementById('boxCategoryIcon');
    const textCat = document.getElementById('boxCategoryText') || document.getElementById('badgeCategory');
    if (boxCat && textCat) {
        boxCat.className = 'alert alert-secondary py-2 px-3 mb-0 d-flex align-items-center gap-2';
        if (iconCat) iconCat.className = 'bi bi-tag-fill fs-5 text-secondary';
        textCat.textContent = '--';
    }

    const boxDup = document.getElementById('boxDuplicate');
    const iconDup = document.getElementById('boxDuplicateIcon') || document.getElementById('iconDuplicate');
    const textDup = document.getElementById('boxDuplicateText') || document.getElementById('textDuplicate');
    if (boxDup && textDup) {
        boxDup.className = 'alert alert-secondary py-2 px-3 mb-0 d-flex align-items-center gap-2';
        if (iconDup) iconDup.className = 'bi bi-shield-check fs-5 text-success';
        textDup.textContent = 'Chưa phát hiện sai lệch';
    }

    const boxConf = document.getElementById('boxConfirm') || document.getElementById('boxConfirmation');
    const iconConf = document.getElementById('boxConfirmIcon') || document.getElementById('iconConfirmation');
    const textConf = document.getElementById('boxConfirmText') || document.getElementById('textConfirmation');
    const btnToggle = document.getElementById('btnToggleConfirm') || document.getElementById('btnConfirmRecord');
    if (boxConf && textConf) {
        boxConf.className = 'alert alert-warning py-2 px-3 mb-0 d-flex align-items-center gap-2 border-warning';
        if (iconConf) iconConf.className = 'bi bi-hourglass-split fs-5 text-warning';
        textConf.textContent = 'Chờ Kế Toán duyệt';
        if (btnToggle) {
            btnToggle.className = 'btn btn-sm btn-success px-2 py-1 shadow-sm';
            btnToggle.innerHTML = '<i class="bi bi-check2-circle me-1"></i> Duyệt Đạt';
        }
    }
}

function switchMode(mode) {
    currentMode = mode;
    const historyToolbar = document.getElementById('historyToolbar');
    const uploadToolbar = document.getElementById('uploadToolbar');
    const btnUpdate = document.getElementById('btnUpdateRecord');
    const btnConfirm = document.getElementById('btnConfirmRecord');
    const btnSaveNew = document.getElementById('btnSaveNewSheet');
    const preview = document.getElementById('receiptPreview');
    const prompt = document.getElementById('uploadPrompt');
    const promptTitle = document.getElementById('promptTitle');

    resetForm();

    if (mode === 'history') {
        historyToolbar.classList.remove('d-none');
        uploadToolbar.classList.add('d-none');
        btnUpdate.classList.remove('d-none');
        btnConfirm.classList.remove('d-none');
        btnSaveNew.classList.add('d-none');
        promptTitle.textContent = "Vui lòng chọn một hóa đơn từ danh sách trên";
        loadHistoricalRecords();
    } else {
        historyToolbar.classList.add('d-none');
        uploadToolbar.classList.remove('d-none');
        btnUpdate.classList.add('d-none');
        btnConfirm.classList.add('d-none');
        btnSaveNew.classList.remove('d-none');
        promptTitle.textContent = "Bấm vào đây để chọn ảnh hóa đơn từ máy tính";
    }
}

// ----------------------------------------------------
// PHẦN 1: DUYỆT HÓA ĐƠN ĐÃ LƯU TRONG GOOGLE SHEET
// ----------------------------------------------------

async function loadHistoricalRecords(autoSelectFirst = true, isManualReload = false) {
    const select = document.getElementById('savedReceiptSelect');
    const btnReload = document.getElementById('btnReloadHistory');
    if (!select) return;

    if (btnReload) {
        btnReload.disabled = true;
        btnReload.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Đang tải...';
    }

    select.innerHTML = '<option value="">-- Đang nạp danh sách từ Google Sheet... --</option>';

    try {
        const response = await fetch(`${API_BASE}/sheets/records`);
        if (!response.ok) {
            throw new Error(`Mã lỗi máy chủ HTTP ${response.status}`);
        }
        const data = await response.json();
        if (!data.success) {
            throw new Error(data.detail || 'Không thể lấy dữ liệu Sheet');
        }

        cachedRecords = data.records || [];

        if (cachedRecords.length === 0) {
            select.innerHTML = '<option value="">-- Hiện chưa có hóa đơn nào trên Google Sheet --</option>';
            resetForm();
            return;
        }

        // Nạp và áp dụng bộ lọc phân loại
        applyHistoricalFilters(currentSelectedDt);

        if (isManualReload) {
            showToast(`🔄 Đã làm mới ${cachedRecords.length} hóa đơn từ Google Sheet!`, 'info');
        }

    } catch (error) {
        console.error('Lỗi khi tải lịch sử Sheet:', error);
        select.innerHTML = `<option value="">-- Lỗi không thể tải danh sách hóa đơn từ Sheet (${error.message}) --</option>`;
        showToast(`Lỗi khi tải danh sách từ Google Sheet: ${error.message}`, 'danger');
    } finally {
        if (btnReload) {
            btnReload.disabled = false;
            btnReload.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i> Tải Lại';
        }
    }
}

function parseMoney(val) {
    if (typeof val === 'number') return val;
    if (!val) return 0;
    let s = String(val).replace(/[^\d,\.-]/g, '').trim();
    if (s.includes('.') && s.includes(',')) {
        s = s.replace(/\./g, '').replace(',', '.');
    } else if (s.includes('.') && !s.includes(',')) {
        s = s.replace(/\./g, '');
    } else if (s.includes(',')) {
        s = s.replace(',', '.');
    }
    return parseFloat(s) || 0;
}

function formatMoney(amount) {
    if (typeof amount === 'string' && (amount.includes('đ') || amount.includes(','))) {
        return amount;
    }
    const num = parseMoney(amount);
    return new Intl.NumberFormat('vi-VN').format(num) + ' đ';
}

let currentFilteredRecords = [];

function checkMathDiscrepancy(record) {
    if (!record) return { hasError: false, message: '' };
    const firstItem = (record.items && record.items[0]) || {};
    const qty = parseMoney(firstItem.quantity || 1);
    const unitPrice = parseMoney(firstItem.price || 0);
    const vatAmount = parseMoney(firstItem.vat_amount || 0);
    const totalAmount = parseMoney(firstItem.row_total || 0);

    if (qty > 0 && unitPrice > 0 && totalAmount > 0) {
        const expectedTotal = (qty * unitPrice) + vatAmount;
        const diff = Math.abs(expectedTotal - totalAmount);
        if (diff > 1000) { // Lệch trên 1.000 VNĐ
            return {
                hasError: true,
                message: `Lệch tính toán: Thành tiền (${formatMoney(totalAmount)}) ≠ SL (${qty}) × Đơn giá (${formatMoney(unitPrice)}) + VAT (${formatMoney(vatAmount)}) = ${formatMoney(expectedTotal)}`
            };
        }
    }
    return { hasError: false, message: '' };
}

function applyHistoricalFilters(preferredDtCode = null) {
    const select = document.getElementById('savedReceiptSelect');
    const filterCat = document.getElementById('filterCategory')?.value || 'ALL';
    const filterStat = document.getElementById('filterStatus')?.value || 'ALL';

    if (!select || !cachedRecords) return;

    currentFilteredRecords = cachedRecords.filter(rec => {
        // Lọc theo Category DT1 - DT4
        if (filterCat !== 'ALL') {
            const dtPrefix = (rec.dt_code || '').substring(0, 3).toUpperCase();
            if (dtPrefix !== filterCat) return false;
        }

        // Lọc theo Trạng thái
        if (filterStat === 'DUPLICATE_WARNING') {
            const hasDupWarn = (rec.notes || '').toUpperCase().includes('NGHI VẤN TRÙNG') || 
                               (rec.file_info && rec.file_info.confirm_status && rec.file_info.confirm_status.includes('trùng'));
            if (!hasDupWarn) return false;
        }
        if (filterStat === 'UNCONFIRMED' && rec.confirmed) return false;
        if (filterStat === 'CONFIRMED' && !rec.confirmed) return false;
        if (filterStat === 'MATH_ERROR') {
            const mathCheck = checkMathDiscrepancy(rec);
            if (!mathCheck.hasError) return false;
        }

        return true;
    });

    if (currentFilteredRecords.length === 0) {
        select.innerHTML = '<option value="">-- Không có hóa đơn nào thỏa mãn bộ lọc hiện tại --</option>';
        resetForm();
        return;
    }

    let optionsHtml = '';
    currentFilteredRecords.forEach(rec => {
        const statusIcon = rec.confirmed ? '✅' : '⏳';
        const dtPrefix = (rec.dt_code || '').substring(0, 3).toUpperCase();
        let catIcon = '📁';
        if (dtPrefix === 'DT4') catIcon = '📝 [GIẤY]';
        else if (dtPrefix === 'DT1') catIcon = '🛒 [TMĐT]';
        else if (dtPrefix === 'DT2') catIcon = '🏪 [SIÊU THỊ]';
        else if (dtPrefix === 'DT3') catIcon = '🌾 [NÔNG SẢN]';

        const mathCheck = checkMathDiscrepancy(rec);
        const mathIcon = mathCheck.hasError ? ' ⚠️[Lệch]' : '';
        const isDupWarn = (rec.notes || '').toUpperCase().includes('NGHI VẤN TRÙNG');
        const dupIcon = isDupWarn ? ' 🛑[Nghi vấn trùng]' : '';
        const merchantName = rec.merchant || rec.order_id || 'Hóa đơn';

        optionsHtml += `<option value="${rec.dt_code}">${statusIcon} ${catIcon} [${rec.dt_code}] ${merchantName} - ${rec.datetime}${mathIcon}${dupIcon}</option>`;
    });
    select.innerHTML = optionsHtml;

    // Chọn hóa đơn mục tiêu
    let target = null;
    if (preferredDtCode) {
        target = currentFilteredRecords.find(r => r.dt_code === preferredDtCode);
    }
    if (!target) {
        target = currentFilteredRecords[0];
    }
    if (target) {
        select.value = target.dt_code;
        onSelectSavedReceipt(target.dt_code);
    }
}

function updateStatusBoxes(arg1, arg2, arg3, arg4, arg5) {
    let record = null;
    let isDuplicate = false;
    let dupMsg = '';
    let isConfirmed = false;
    let dtCode = '';

    // Signature 1: updateStatusBoxes(record, isDuplicate, dupMsg, confirmed, dtCode)
    if (arg1 && typeof arg1 === 'object') {
        record = arg1;
        isDuplicate = !!arg2;
        dupMsg = arg3 || '';
        isConfirmed = !!arg4;
        dtCode = arg5 || record.dt_code || '';
    } 
    // Signature 2: updateStatusBoxes(isDuplicate, dupMsg, isConfirmed, dtCode)
    else {
        isDuplicate = !!arg1;
        dupMsg = arg2 || '';
        isConfirmed = !!arg3;
        dtCode = arg4 || currentSelectedDt || '';
        if (Array.isArray(cachedRecords)) {
            record = cachedRecords.find(r => r.dt_code === dtCode) || null;
        }
    }

    const dtPrefix = String(dtCode || '').substring(0, 3).toUpperCase();
    const mathCheck = checkMathDiscrepancy(record || {});
    const isDupWarn = isDuplicate || (record && (record.notes || '').toUpperCase().includes('NGHI VẤN TRÙNG'));

    // 1. Cập nhật Ô 1: Phân Loại Đối Tượng
    const boxCat = document.getElementById('boxCategoryTag') || document.getElementById('boxCategory');
    const iconCat = document.getElementById('boxCategoryIcon');
    const textCat = document.getElementById('boxCategoryText') || document.getElementById('badgeCategory');

    if (boxCat && textCat) {
        if (dtPrefix === 'DT4') {
            boxCat.className = 'alert alert-danger py-2 px-3 mb-0 d-flex align-items-center gap-2';
            if (iconCat) iconCat.className = 'bi bi-pencil-square fs-5 text-danger';
            textCat.textContent = '📝 DT4: Hóa Đơn Giấy / Viết Tay';
        } else if (dtPrefix === 'DT1') {
            boxCat.className = 'alert alert-primary py-2 px-3 mb-0 d-flex align-items-center gap-2';
            if (iconCat) iconCat.className = 'bi bi-cart-check-fill fs-5 text-primary';
            textCat.textContent = '🛒 DT1: Sàn TMĐT & Vận Chuyển';
        } else if (dtPrefix === 'DT2') {
            boxCat.className = 'alert alert-success py-2 px-3 mb-0 d-flex align-items-center gap-2';
            if (iconCat) iconCat.className = 'bi bi-shop fs-5 text-success';
            textCat.textContent = '🏪 DT2: Siêu Thị & Bán Lẻ';
        } else if (dtPrefix === 'DT3') {
            boxCat.className = 'alert alert-warning py-2 px-3 mb-0 d-flex align-items-center gap-2';
            if (iconCat) iconCat.className = 'bi bi-basket2-fill fs-5 text-warning';
            textCat.textContent = '🌾 DT3: Nông Sản & Thực Phẩm';
        } else {
            boxCat.className = 'alert alert-secondary py-2 px-3 mb-0 d-flex align-items-center gap-2';
            if (iconCat) iconCat.className = 'bi bi-tag-fill fs-5 text-secondary';
            textCat.textContent = `📁 ${dtCode || 'HÓA ĐƠN'}`;
        }
    }

    // 2. Cập nhật Ô 2: Đối Soát Toán Học & Trùng Lặp
    const boxDup = document.getElementById('boxDuplicate');
    const iconDup = document.getElementById('boxDuplicateIcon') || document.getElementById('iconDuplicate');
    const textDup = document.getElementById('boxDuplicateText') || document.getElementById('textDuplicate');

    if (boxDup && textDup) {
        if (isDupWarn) {
            boxDup.className = 'alert alert-danger py-2 px-3 mb-0 d-flex align-items-center gap-2 border-danger';
            if (iconDup) iconDup.className = 'bi bi-exclamation-triangle-fill fs-5 text-danger';
            textDup.innerHTML = `<span class="fw-bold text-danger">⚠️ NGHI VẤN TRÙNG:</span> ${dupMsg || (record ? record.notes : '') || 'Mã chứng từ trùng với hóa đơn khác!'}`;
        } else if (mathCheck.hasError) {
            boxDup.className = 'alert alert-warning py-2 px-3 mb-0 d-flex align-items-center gap-2 border-warning';
            if (iconDup) iconDup.className = 'bi bi-calculator-fill fs-5 text-warning';
            textDup.innerHTML = `<span class="fw-bold text-dark">⚠️ LỆCH TOÁN HỌC:</span> ${mathCheck.message}`;
        } else {
            boxDup.className = 'alert alert-success py-2 px-3 mb-0 d-flex align-items-center gap-2 border-success';
            if (iconDup) iconDup.className = 'bi bi-shield-check fs-5 text-success';
            textDup.innerHTML = `<span class="text-success fw-bold">✓ Khớp toán học 100%</span> (Không trùng lặp)`;
        }
    }

    // 3. Cập nhật Ô 3: Trạng Thái Xác Nhận Kế Toán
    const boxConf = document.getElementById('boxConfirm') || document.getElementById('boxConfirmation');
    const iconConf = document.getElementById('boxConfirmIcon') || document.getElementById('iconConfirmation');
    const textConf = document.getElementById('boxConfirmText') || document.getElementById('textConfirmation');
    const btnToggle = document.getElementById('btnToggleConfirm') || document.getElementById('btnConfirmRecord');

    if (boxConf && textConf) {
        if (isConfirmed) {
            boxConf.className = 'alert alert-success py-2 px-3 mb-0 d-flex align-items-center gap-2 border-success';
            if (iconConf) iconConf.className = 'bi bi-patch-check-fill fs-5 text-success';
            textConf.innerHTML = `<span class="fw-bold text-success">ĐÃ DUYỆT ĐẠT (x):</span> [${dtCode || ''}]`;
            if (btnToggle) {
                btnToggle.className = 'btn btn-sm btn-outline-danger px-2 py-1 shadow-sm';
                btnToggle.innerHTML = '<i class="bi bi-x-circle me-1"></i> Bỏ Duyệt';
            }
        } else {
            boxConf.className = 'alert alert-warning py-2 px-3 mb-0 d-flex align-items-center gap-2 border-warning';
            if (iconConf) iconConf.className = 'bi bi-hourglass-split fs-5 text-warning';
            textConf.innerHTML = `<span class="fw-bold text-dark">CHỜ DUYỆT:</span> [${dtCode || ''}]`;
            if (btnToggle) {
                btnToggle.className = 'btn btn-sm btn-success px-2 py-1 shadow-sm';
                btnToggle.innerHTML = '<i class="bi bi-check2-circle me-1"></i> Duyệt Đạt';
            }
        }
    }
}

async function toggleConfirmStatus() {
    if (!currentSelectedDt) {
        showToast("Vui lòng chọn một hóa đơn từ danh sách trước.", "warning");
        return;
    }

    const record = cachedRecords.find(r => r.dt_code === currentSelectedDt);
    if (!record) return;

    const newStatus = record.confirmed ? "" : "x";
    const btn = document.getElementById('btnToggleConfirm');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Đang xử lý...';
    }

    try {
        const formData = getFormDataArray();
        const response = await fetch(`${API_BASE}/sheets/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dt_code: currentSelectedDt,
                status: newStatus,
                row: formData
            })
        });
        const data = await response.json();
        if (response.ok && data.success) {
            record.confirmed = (newStatus === "x");
            showToast(record.confirmed ? `✅ Đã duyệt ĐẠT cho hóa đơn [${currentSelectedDt}]!` : `ℹ️ Đã chuyển [${currentSelectedDt}] về trạng thái Chờ Duyệt.`, 'success');
            onSelectSavedReceipt(currentSelectedDt);
            applyHistoricalFilters(currentSelectedDt);
        } else {
            showToast(`Lỗi: ${data.detail || data.error || 'Không thể cập nhật trạng thái'}`, 'danger');
        }
    } catch (error) {
        console.error('Lỗi khi duyệt hóa đơn:', error);
        showToast(`Lỗi kết nối tới Server: ${error.message}`, 'danger');
    } finally {
        if (btn) btn.disabled = false;
    }
}

function navigateReceipt(offset) {
    const recordsToNav = (currentFilteredRecords && currentFilteredRecords.length > 0) ? currentFilteredRecords : cachedRecords;
    if (!recordsToNav || recordsToNav.length === 0) {
        alert("Danh sách hóa đơn chưa được tải xong.");
        return;
    }
    
    let currentIndex = recordsToNav.findIndex(r => r.dt_code === currentSelectedDt);
    if (currentIndex === -1) {
        currentIndex = (offset > 0) ? 0 : recordsToNav.length - 1;
    } else {
        currentIndex += offset;
    }

    if (currentIndex < 0) {
        alert("Bạn đang ở hóa đơn đầu tiên trong bộ lọc.");
        currentIndex = 0;
    } else if (currentIndex >= recordsToNav.length) {
        alert("Bạn đang ở hóa đơn cuối cùng trong bộ lọc.");
        currentIndex = recordsToNav.length - 1;
    }

    const targetCode = recordsToNav[currentIndex].dt_code;
    const select = document.getElementById('savedReceiptSelect');
    if (select) {
        select.value = targetCode;
    }
    onSelectSavedReceipt(targetCode);
    updateNavButtonsState(currentIndex);
}

function updateNavButtonsState(index) {
    const btnPrev = document.getElementById('btnPrevReceipt');
    const btnNext = document.getElementById('btnNextReceipt');
    const recordsToNav = (currentFilteredRecords && currentFilteredRecords.length > 0) ? currentFilteredRecords : cachedRecords;
    if (btnPrev && btnNext && recordsToNav) {
        btnPrev.disabled = (index <= 0);
        btnNext.disabled = (index >= recordsToNav.length - 1);
    }
}

function prefetchAdjacentImages(currentIndex) {
    const recordsToNav = (currentFilteredRecords && currentFilteredRecords.length > 0) ? currentFilteredRecords : cachedRecords;
    if (!recordsToNav || recordsToNav.length === 0 || currentIndex < 0) return;
    const indices = [currentIndex + 1, currentIndex + 2, currentIndex - 1];
    indices.forEach(idx => {
        if (idx >= 0 && idx < recordsToNav.length) {
            const linkUrl = recordsToNav[idx].file_info?.link || '';
            const match = linkUrl.match(/\/file\/d\/([a-zA-Z0-9_-]+)/);
            if (match && match[1]) {
                const img = new Image();
                img.src = `${API_BASE}/drive/image/${match[1]}`;
            }
        }
    });
}

function onSelectSavedReceipt(dtCode) {
    if (!dtCode) {
        resetForm();
        return;
    }

    currentSelectedDt = dtCode;
    const record = cachedRecords.find(r => r.dt_code === dtCode);
    if (!record) return;

    const preview = document.getElementById('receiptPreview');
    const prompt = document.getElementById('uploadPrompt');

    // 1. Tải ảnh từ Google Drive (có Cache & Tải trước)
    let driveFileId = null;
    const linkUrl = record.file_info?.link || '';
    const match = linkUrl.match(/\/file\/d\/([a-zA-Z0-9_-]+)/);
    if (match && match[1]) {
        driveFileId = match[1];
    }

    if (driveFileId) {
        preview.classList.add('opacity-50');
        preview.onload = () => {
            preview.classList.remove('opacity-50');
        };
        preview.onerror = () => {
            preview.classList.remove('opacity-50');
        };
        preview.src = `${API_BASE}/drive/image/${driveFileId}`;
        preview.classList.remove('d-none');
        prompt.classList.add('d-none');

        const curIdx = cachedRecords.findIndex(r => r.dt_code === dtCode);
        prefetchAdjacentImages(curIdx);
    } else {
        preview.classList.add('d-none');
        prompt.classList.remove('d-none');
        document.getElementById('promptTitle').textContent = "Không tìm thấy link ảnh Google Drive cho hóa đơn này";
    }

    // 2. Điền dữ liệu vào form
    document.getElementById('f_dt_code').value = record.dt_code || '';
    document.getElementById('f_date').value = record.datetime || '';
    document.getElementById('f_company').value = record.merchant || '';
    document.getElementById('f_seller_address').value = record.address_seller || '';
    document.getElementById('f_buyer_address').value = record.address_buyer || '';
    document.getElementById('f_order_id').value = record.order_id || '';

    const firstItem = (record.items && record.items[0]) || {};
    document.getElementById('f_description').value = firstItem.item_name || '';
    document.getElementById('f_quantity').value = firstItem.quantity || 1;
    document.getElementById('f_unit_price').value = firstItem.price || '0 đ';
    document.getElementById('f_vat_rate').value = firstItem.vat_rate || '0%';
    document.getElementById('f_vat_amount').value = firstItem.vat_amount || '0 đ';
    document.getElementById('f_total_amount').value = firstItem.row_total || '0 đ';

    document.getElementById('f_buyer_name').value = record.customer || '';
    document.getElementById('f_notes').value = record.notes || '';

    // 3. Kiểm tra trùng lặp trong bộ nhớ Sheet
    let isDuplicate = false;
    let dupMsg = '';
    const currentOrderId = (record.order_id || '').trim();
    if (currentOrderId && currentOrderId.length >= 4) {
        const duplicates = cachedRecords.filter(r => r.dt_code !== record.dt_code && (r.order_id || '').trim() === currentOrderId);
        if (duplicates.length > 0) {
            isDuplicate = true;
            dupMsg = `Mã [${currentOrderId}] trùng với ${duplicates.map(d => d.dt_code).join(', ')}`;
        }
    }

    // 4. Cập nhật 3 Ô trạng thái
    updateStatusBoxes(record, isDuplicate, dupMsg, record.confirmed, record.dt_code);

    // 5. Cập nhật trạng thái nút chuyển mũi tên
    const curIdx = (currentFilteredRecords && currentFilteredRecords.length > 0)
        ? currentFilteredRecords.findIndex(r => r.dt_code === dtCode)
        : cachedRecords.findIndex(r => r.dt_code === dtCode);
    updateNavButtonsState(curIdx);
}

function getFormDataArray() {
    return [
        document.getElementById('f_dt_code').value.trim(),
        document.getElementById('f_date').value.trim(),
        document.getElementById('f_company').value.trim(),
        document.getElementById('f_seller_address').value.trim(),
        document.getElementById('f_buyer_address').value.trim(),
        document.getElementById('f_order_id').value.trim(),
        document.getElementById('f_description').value.trim(),
        document.getElementById('f_quantity').value.trim(),
        document.getElementById('f_unit_price').value.trim(),
        document.getElementById('f_vat_rate').value.trim(),
        document.getElementById('f_vat_amount').value.trim(),
        document.getElementById('f_total_amount').value.trim(),
        document.getElementById('f_buyer_name').value.trim(),
        document.getElementById('f_notes').value.trim()
    ];
}

async function updateHistoricalRecord() {
    if (!currentSelectedDt) {
        showToast("Vui lòng chọn một hóa đơn từ danh sách trước.", "warning");
        return;
    }

    const btn = document.getElementById('btnUpdateRecord');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Đang cập nhật...';

    const row = getFormDataArray();

    try {
        const response = await fetch(`${API_BASE}/sheets/record`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dt_code: currentSelectedDt,
                row: row
            })
        });
        const data = await response.json();
        if (response.ok) {
            showToast(`✅ Cập nhật thành công cho mã [${currentSelectedDt}]!`, 'success');
        } else {
            showToast(`Lỗi: ${data.detail || 'Không thể cập nhật Sheet'}`, 'danger');
        }
    } catch (error) {
        console.error('Lỗi cập nhật:', error);
        showToast(`Lỗi kết nối tới Server: ${error.message}`, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-check2-circle me-1"></i> Cập Nhật Lên Sheet';
    }
}

async function confirmHistoricalRecord(status = 'x') {
    if (!currentSelectedDt) {
        showToast("Vui lòng chọn một hóa đơn từ danh sách trước.", "warning");
        return;
    }

    const btn = document.getElementById('btnConfirmRecord');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Đang lưu xác nhận...';

    const row = getFormDataArray();

    try {
        const response = await fetch(`${API_BASE}/sheets/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dt_code: currentSelectedDt,
                status: status,
                row: row
            })
        });
        const data = await response.json();
        if (response.ok) {
            showToast(`🎉 Đã xác nhận ĐẠT cho hóa đơn [${currentSelectedDt}]!`, 'success');
            
            // Cập nhật trạng thái tức thì không cần đợi reload
            const rec = cachedRecords.find(r => r.dt_code === currentSelectedDt);
            if (rec) rec.confirmed = true;
            updateStatusBoxes(false, '', true, currentSelectedDt);

            // Đổi icon trong Dropdown
            const select = document.getElementById('savedReceiptSelect');
            if (select && select.selectedOptions[0]) {
                select.selectedOptions[0].textContent = select.selectedOptions[0].textContent.replace('⏳', '✅');
            }
        } else {
            showToast(`Lỗi: ${data.detail || 'Không thể xác nhận'}`, 'danger');
        }
    } catch (error) {
        console.error('Lỗi xác nhận:', error);
        showToast(`Lỗi kết nối tới Server: ${error.message}`, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-patch-check me-1"></i> Xác Nhận Hóa Đơn (Đạt)';
    }
}

async function deleteCurrentRecord() {
    if (!currentSelectedDt) {
        showToast("Vui lòng chọn một hóa đơn từ danh sách để xóa.", "warning");
        return;
    }

    const rec = cachedRecords.find(r => r.dt_code === currentSelectedDt);
    const merchantName = rec ? (rec.merchant || rec.order_id || '') : '';
    const confirmPrompt = `⚠️ BẠN CÓ CHẮC CHẮN MUỐN XÓA?\n\nHóa đơn [${currentSelectedDt}] ${merchantName ? '(' + merchantName + ')' : ''} sẽ bị XÓA VĨNH VIỄN khỏi Google Sheet (cả Bảng Kê và Link Ảnh).\n\nHành động này không thể hoàn tác!`;

    if (!confirm(confirmPrompt)) {
        return;
    }

    const btnDelete = document.getElementById('btnDeleteRecord');
    if (btnDelete) {
        btnDelete.disabled = true;
        btnDelete.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Đang xóa...';
    }

    try {
        const response = await fetch(`${API_BASE}/sheets/record/${encodeURIComponent(currentSelectedDt)}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (response.ok && data.success) {
            showToast(`🗑️ Đã xóa thành công hóa đơn [${currentSelectedDt}] khỏi Google Sheet!`, 'success');
            
            const deletedCode = currentSelectedDt;
            cachedRecords = cachedRecords.filter(r => r.dt_code !== deletedCode);
            currentFilteredRecords = currentFilteredRecords.filter(r => r.dt_code !== deletedCode);

            if (currentFilteredRecords.length > 0) {
                applyHistoricalFilters();
            } else {
                loadHistoricalRecords();
            }
        } else {
            showToast(`Lỗi khi xóa: ${data.detail || data.error || 'Không xác định'}`, 'danger');
        }
    } catch (error) {
        console.error('Lỗi khi xóa hóa đơn:', error);
        showToast(`Lỗi kết nối Server: ${error.message}`, 'danger');
    } finally {
        if (btnDelete) {
            btnDelete.disabled = false;
            btnDelete.innerHTML = '<i class="bi bi-trash3-fill me-1"></i> Xóa Khỏi Sheet';
        }
    }
}


// ----------------------------------------------------
// PHẦN 2: TẢI ẢNH MỚI LÊN ĐỐI CHIẾU
// ----------------------------------------------------

function handleFileSelected(event) {
    const file = event.target.files[0];
    if (!file) return;

    currentFile = file;
    const preview = document.getElementById('receiptPreview');
    const prompt = document.getElementById('uploadPrompt');
    const btnOcr = document.getElementById('btnRunOcr');

    const reader = new FileReader();
    reader.onload = function(e) {
        preview.src = e.target.result;
        preview.classList.remove('d-none');
        prompt.classList.add('d-none');
        btnOcr.disabled = false;
    };
    reader.readAsDataURL(file);
}

async function startOcrProcess() {
    if (!currentFile) {
        alert('Vui lòng chọn một ảnh hóa đơn trước.');
        return;
    }

    const btnOcr = document.getElementById('btnRunOcr');
    const badge = document.getElementById('engineBadge');
    const alertBox = document.getElementById('alertContainer');
    const modelSelect = document.getElementById('verifyAiModel');
    const selectedModel = modelSelect ? modelSelect.value : 'gemini-2.5-flash-lite';

    btnOcr.disabled = true;
    btnOcr.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang trích xuất AI...';
    badge.className = 'badge bg-warning text-dark';
    badge.textContent = 'AI Đang đọc...';
    alertBox.innerHTML = '';

    const formData = new FormData();
    formData.append('file', currentFile);
    formData.append('mode', 'auto');
    formData.append('model', selectedModel);

    try {
        const response = await fetch(`${API_BASE}/receipt-ocr`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        populateVerificationForm(data);

        badge.className = 'badge bg-success';
        badge.textContent = `Thành công (${data.engine_used || selectedModel})`;

    } catch (error) {
        console.error('Lỗi khi OCR hóa đơn:', error);
        badge.className = 'badge bg-danger';
        badge.textContent = 'Trích xuất thất bại';
        alertBox.innerHTML = `<div class="alert alert-danger py-2 small">Lỗi kết nối tới Server AI: ${error.message}</div>`;
    } finally {
        btnOcr.disabled = false;
        btnOcr.innerHTML = '<i class="bi bi-stars me-1"></i> Trích Xuất AI';
    }
}

function populateVerificationForm(data) {
    const raw = data.raw_parsed_data || {};
    const cat = data.category || {};
    const warns = data.warnings || {};
    const alertBox = document.getElementById('alertContainer');

    let alertHtml = '';
    alertHtml += `<div class="d-flex gap-2 align-items-center mb-2">
        <span class="badge bg-primary fs-6">${cat.code_prefix || 'DT1'}</span>
        <span class="fw-bold text-dark">${cat.name || 'Sàn TMĐT / Vận chuyển'}</span>
    </div>`;

    if (warns.is_duplicate) {
        alertHtml += `<div class="alert alert-danger py-2 small mb-2"><i class="bi bi-exclamation-triangle-fill me-1"></i> <strong>CẢNH BÁO TRÙNG LẶP:</strong> ${warns.duplicate_message || 'Mã hóa đơn/vận đơn đã tồn tại trong Sheet!'}</div>`;
    }
    if (warns.has_vat) {
        alertHtml += `<div class="alert alert-warning py-2 small mb-2"><i class="bi bi-receipt me-1"></i> ${warns.vat_alert || 'Hóa đơn có thuế VAT'}</div>`;
    }
    alertBox.innerHTML = alertHtml;

    document.getElementById('f_dt_code').value = cat.code_prefix || 'DT1';
    document.getElementById('f_date').value = raw.date || raw.invoice_date || new Date().toLocaleDateString('en-GB');
    document.getElementById('f_order_id').value = raw.order_id || raw.tracking_number || raw.invoice_number || '';
    document.getElementById('f_company').value = raw.merchant_name || raw.seller_name || '';
    document.getElementById('f_seller_address').value = raw.merchant_address || raw.seller_address || '';
    document.getElementById('f_buyer_address').value = raw.customer_address || raw.buyer_address || '';

    let items = raw.items || [];
    let itemName = '';
    let itemQty = 1;
    let itemPrice = 0;
    
    if (items.length > 0) {
        itemName = items.map(i => i.name || i.description).join('; ');
        itemQty = items[0].quantity || 1;
        itemPrice = items[0].unit_price || items[0].price || 0;
    } else {
        itemName = raw.description || 'Chi phí mua sắm vật tư';
    }

    document.getElementById('f_description').value = itemName;
    document.getElementById('f_quantity').value = itemQty;
    document.getElementById('f_unit_price').value = formatMoney(itemPrice);

    let vatRate = raw.tax_rate || raw.vat_rate || 0;
    document.getElementById('f_vat_rate').value = vatRate > 0 ? `${(vatRate*100).toFixed(0)}%` : '0%';
    document.getElementById('f_vat_amount').value = formatMoney(raw.tax_amount || raw.vat_amount || 0);
    document.getElementById('f_total_amount').value = formatMoney(raw.total_amount || (itemQty * itemPrice));

    document.getElementById('f_buyer_name').value = raw.customer_name || raw.buyer_name || '';
    
    let noteText = '';
    if (warns.is_duplicate) noteText += `[TRÙNG LẶP] ${warns.duplicate_message}. `;
    if (warns.has_vat) noteText += `[VAT] ${warns.vat_alert}. `;
    document.getElementById('f_notes').value = noteText.trim();
    // Cập nhật 2 Ô trạng thái
    updateStatusBoxes(warns.is_duplicate, warns.duplicate_message, false, cat.code_prefix);
}

function recomputeAmounts() {
    const qty = parseMoney(document.getElementById('f_quantity').value) || 1;
    const price = parseMoney(document.getElementById('f_unit_price').value) || 0;
    const vatAmt = parseMoney(document.getElementById('f_vat_amount').value) || 0;
    
    const subtotal = qty * price;
    document.getElementById('f_total_amount').value = formatMoney(subtotal + vatAmt);
}

async function saveReceiptData() {
    const btnSave = document.getElementById('btnSaveNewSheet');
    const row = getFormDataArray();

    btnSave.disabled = true;
    btnSave.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang lưu vào Sheet...';

    try {
        const response = await fetch(`${API_BASE}/sheets/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rows: [row] })
        });

        const data = await response.json();
        if (response.ok) {
            showToast(`🎉 Đã lưu thành công [${row[0]} - ${row[2]}] vào Google Sheet!`, 'success');
        } else {
            showToast(`Lỗi khi lưu lên Sheet: ${data.detail || 'Không xác định'}`, 'danger');
        }
    } catch (error) {
        console.error('Lỗi ghi Sheet:', error);
        showToast(`Lỗi kết nối tới Server: ${error.message}`, 'danger');
    } finally {
        btnSave.disabled = false;
        btnSave.innerHTML = '<i class="bi bi-cloud-arrow-up me-1"></i> Lưu Dòng Mới Vào Sheet';
    }
}

function resetForm() {
    document.getElementById('verifyForm').reset();
    document.getElementById('engineBadge').className = 'badge bg-secondary';
    document.getElementById('engineBadge').textContent = 'Chưa chọn';
    
    const boxDup = document.getElementById('boxDuplicate');
    const iconDup = document.getElementById('boxDuplicateIcon');
    const textDup = document.getElementById('boxDuplicateText');
    const boxConf = document.getElementById('boxConfirm');
    const iconConf = document.getElementById('boxConfirmIcon');
    const textConf = document.getElementById('boxConfirmText');
    if (boxDup && iconDup && textDup) {
        boxDup.className = 'alert alert-secondary py-2 px-3 mb-0 d-flex align-items-center gap-2';
        iconDup.className = 'bi bi-shield-check fs-5 text-success';
        textDup.textContent = 'Chưa phát hiện trùng lặp';
    }
    if (boxConf && iconConf && textConf) {
        boxConf.className = 'alert alert-warning py-2 px-3 mb-0 d-flex align-items-center gap-2';
        iconConf.className = 'bi bi-hourglass-split fs-5 text-warning';
        textConf.textContent = 'Chưa xác nhận (Chờ Kế Toán)';
    }

    const preview = document.getElementById('receiptPreview');
    const prompt = document.getElementById('uploadPrompt');
    preview.src = '';
    preview.classList.add('d-none');
    prompt.classList.remove('d-none');
    currentFile = null;
    currentSelectedDt = null;
}
