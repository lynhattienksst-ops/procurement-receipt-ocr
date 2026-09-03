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

/**
 * Quản lý trạng thái hiển thị khu vực xem hóa đơn (verify.html)
 * @param {'empty' | 'image' | 'pdf'} mode - Trạng thái hiển thị
 * @param {string} [sourceUrl] - URL ảnh hoặc PDF
 */
function setVerifyViewerMode(mode, sourceUrl = '') {
    const preview = document.getElementById('receiptPreview');
    const viewport = document.getElementById('imageViewport');
    const toolbar = document.getElementById('imageToolbar');
    const pdfPreview = document.getElementById('receiptPdfPreview');
    const prompt = document.getElementById('uploadPrompt');

    if (mode === 'empty') {
        if (preview) {
            preview.src = '';
            preview.classList.add('d-none');
            preview.classList.remove('opacity-50');
        }
        if (viewport) viewport.classList.add('d-none');
        if (toolbar) toolbar.classList.add('d-none');
        if (pdfPreview) {
            pdfPreview.src = '';
            pdfPreview.classList.add('d-none');
        }
        if (prompt) prompt.classList.remove('d-none');
    } else if (mode === 'image') {
        if (pdfPreview) {
            pdfPreview.src = '';
            pdfPreview.classList.add('d-none');
        }
        if (prompt) prompt.classList.add('d-none');
        if (viewport) viewport.classList.remove('d-none');
        if (toolbar) toolbar.classList.remove('d-none');
        if (preview) {
            if (sourceUrl) preview.src = sourceUrl;
            preview.classList.remove('d-none');
        }
        resetImageTransform('verify');
    } else if (mode === 'pdf') {
        if (preview) {
            preview.src = '';
            preview.classList.add('d-none');
            preview.classList.remove('opacity-50');
        }
        if (viewport) viewport.classList.add('d-none');
        if (toolbar) toolbar.classList.add('d-none');
        if (prompt) prompt.classList.add('d-none');
        if (pdfPreview) {
            if (sourceUrl) pdfPreview.src = sourceUrl;
            pdfPreview.classList.remove('d-none');
        }
    }
}

function resetForm() {
    const form = document.getElementById('verifyForm');
    if (form) form.reset();

    const fieldIds = [
        'f_dt_code', 'f_date', 'f_company', 'f_seller_address', 'f_buyer_address',
        'f_order_id', 'f_description', 'f_quantity', 'f_unit_price', 'f_vat_rate',
        'f_vat_amount', 'f_total_amount', 'f_buyer_name', 'f_notes',
        'f_h_dt_code', 'f_h_date', 'f_h_doc_code', 'f_h_company', 'f_h_buyer_name',
        'f_h_seller_addr', 'f_h_buyer_addr', 'f_h_raw_amount', 'f_h_discount',
        'f_h_vat', 'f_h_final_payment', 'f_h_notes'
    ];
    fieldIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });

    const linesBody = document.getElementById('relationalLinesTableBody');
    if (linesBody) linesBody.innerHTML = '';

    setVerifyViewerMode('empty');

    const promptTitle = document.getElementById('promptTitle');
    if (promptTitle) {
        promptTitle.textContent = currentMode === 'upload' 
            ? "Bấm vào đây để chọn ảnh hóa đơn từ máy tính" 
            : "Vui lòng chọn một hóa đơn từ danh sách trên";
    }

    const badge = document.getElementById('engineBadge');
    if (badge) {
        badge.className = 'badge bg-secondary';
        badge.textContent = 'Chưa chọn';
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
        textConf.textContent = 'Chờ duyệt';
        if (btnToggle) {
            btnToggle.className = 'btn btn-sm btn-success px-2 py-1 shadow-sm';
            btnToggle.innerHTML = '<i class="bi bi-check2-circle me-1"></i> Duyệt Đạt';
        }
    }

    currentFile = null;
    currentSelectedDt = null;
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
    if (!s) return 0;

    if (s.includes('.') && s.includes(',')) {
        const lastDot = s.lastIndexOf('.');
        const lastComma = s.lastIndexOf(',');
        if (lastComma > lastDot) {
            s = s.replace(/\./g, '').replace(',', '.');
        } else {
            s = s.replace(/,/g, '');
        }
    } else if (s.includes(',') && !s.includes('.')) {
        const parts = s.split(',');
        if (parts.length === 2 && (parts[1].length === 1 || parts[1].length === 2)) {
            s = s.replace(',', '.');
        } else {
            s = s.replace(/,/g, '');
        }
    } else if (s.includes('.') && !s.includes(',')) {
        const parts = s.split('.');
        if (parts.length === 2) {
            if (parts[0] === '0' || parts[1].length === 1 || parts[1].length === 2) {
                // Decimal dot: e.g. 0.5, 0.50, 1.5, 2.75
            } else {
                s = s.replace(/\./g, '');
            }
        } else {
            s = s.replace(/\./g, '');
        }
    }
    return parseFloat(s) || 0;
}

function formatMoney(amount) {
    if (amount === undefined || amount === null || amount === '') return '0';
    if (typeof amount === 'number') {
        const rounded = Math.round(amount);
        return new Intl.NumberFormat('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(rounded);
    }
    let strVal = String(amount).trim();
    for (const suffix of ['đ', 'Đ', 'vnđ', 'VNĐ', 'vnd', 'VND', '₫']) {
        strVal = strVal.replace(suffix, '').trim();
    }
    const num = parseMoney(strVal);
    const rounded = Math.round(num);
    return new Intl.NumberFormat('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(rounded);
}

function parseQuantity(val) {
    if (typeof val === 'number') return val;
    if (!val) return 0;
    let s = String(val).trim();
    if (s.includes('.') && s.includes(',')) {
        const lastDot = s.lastIndexOf('.');
        const lastComma = s.lastIndexOf(',');
        if (lastComma > lastDot) {
            s = s.replace(/\./g, '').replace(',', '.');
        } else {
            s = s.replace(/,/g, '');
        }
    } else if (s.includes(',')) {
        s = s.replace(',', '.');
    }
    return parseFloat(s) || 0;
}

function formatQuantity(val) {
    const q = parseQuantity(val);
    if (isNaN(q)) return '0,0000';
    // Định dạng số thập phân luôn hiển thị đúng 4 chữ số sau dấu phẩy chuẩn Việt Nam
    return new Intl.NumberFormat('vi-VN', {
        minimumFractionDigits: 4,
        maximumFractionDigits: 4
    }).format(q);
}

function formatQuantityInput(inputEl) {
    if (!inputEl) return;
    const q = parseQuantity(inputEl.value);
    inputEl.value = formatQuantity(q);
    recomputeLineRow(inputEl);
}

let currentFilteredRecords = [];

function checkMathDiscrepancy(record) {
    if (!record) return { hasError: false, message: '' };

    const items = record.items || [];
    let sumLineTotals = 0;
    for (let idx = 0; idx < items.length; idx++) {
        sumLineTotals += parseMoney(items[idx].row_total || 0);
    }

    // 1. Kiểm tra đối soát cấp Header theo công thức chuẩn:
    // Tổng Thanh Toán = Σ(Thành Tiền các dòng) − Chiết Khấu Tổng Bill
    const headerDisc = parseMoney(record.total_discount_amount || 0); // CK Tổng Bill
    const headerTotal = parseMoney(record.total_amount || 0);

    if (sumLineTotals > 0 && headerTotal > 0) {
        const expectedHeaderTotal = sumLineTotals - headerDisc;
        const diff = Math.abs(expectedHeaderTotal - headerTotal);
        if (diff > 1000) {
            return {
                hasError: true,
                message: `Lệch tính toán Header: Tổng thanh toán (${formatMoney(headerTotal)}) ≠ Σ(Thành Tiền các dòng) (${formatMoney(sumLineTotals)}) - CK Bill (${formatMoney(headerDisc)}) = ${formatMoney(expectedHeaderTotal)}`
            };
        }
    }

    // 2. Kiểm tra đối soát từng dòng mặt hàng (Lines)
    // Công thức: Thành Tiền = (SL × Đơn Giá) − Tổng CK MH + Tiền VAT
    for (let idx = 0; idx < items.length; idx++) {
        const it = items[idx];
        const qty = parseQuantity(it.quantity || 1);
        const price = parseMoney(it.price || 0);
        const disc = parseMoney(it.discount || 0); // Tổng CK Mặt Hàng
        const vat = parseMoney(it.vat_amount || 0);
        const rowTotal = parseMoney(it.row_total || 0);

        if (qty > 0 && price > 0 && rowTotal > 0) {
            // disc = Tổng CK MH (đã là tổng dòng, không nhân SL)
            const expectedRowTotal = (qty * price) - disc + vat;
            const diff = Math.abs(expectedRowTotal - rowTotal);
            if (diff > 1000) { // Lệch trên 1.000 VNĐ
                return {
                    hasError: true,
                    message: `Dòng ${idx + 1} (${it.item_name || 'Hàng hóa'}): Thành tiền (${formatMoney(rowTotal)}) ≠ SL (${qty}) × Giá (${formatMoney(price)}) - Tổng CK MH (${formatMoney(disc)}) + VAT (${formatMoney(vat)}) = ${formatMoney(expectedRowTotal)}`
                };
            }
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
        else if (dtPrefix === 'DT2') catIcon = '🏪 [BÁN LẺ]';
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
            textCat.textContent = '🏪 DT2: Siêu Thị & Bán Lẻ Chung Quy';
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
        const response = await fetch(`${API_BASE}/sheets/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dt_code: currentSelectedDt,
                status: newStatus
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

async function confirmHistoricalRecord(status = 'x') {
    if (!currentSelectedDt) {
        showToast("Vui lòng chọn một hóa đơn từ danh sách trước.", "warning");
        return;
    }

    const record = cachedRecords.find(r => r.dt_code === currentSelectedDt);
    if (!record) return;

    const btn = document.getElementById('btnConfirmRecord');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Đang duyệt...';
    }

    try {
        const response = await fetch(`${API_BASE}/sheets/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dt_code: currentSelectedDt,
                status: status
            })
        });
        const data = await response.json();
        if (response.ok && data.success) {
            record.confirmed = (status === "x");
            showToast(`✅ Đã xác nhận hóa đơn [${currentSelectedDt}] thành công!`, 'success');
            onSelectSavedReceipt(currentSelectedDt);
            applyHistoricalFilters(currentSelectedDt);
        } else {
            showToast(`Lỗi: ${data.detail || data.error || 'Không thể cập nhật trạng thái'}`, 'danger');
        }
    } catch (error) {
        console.error('Lỗi khi duyệt hóa đơn:', error);
        showToast(`Lỗi kết nối tới Server: ${error.message}`, 'danger');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-patch-check me-1"></i> Xác Nhận Hóa Đơn (Đạt)';
        }
    }
}

async function deleteCurrentRecord() {
    if (!currentSelectedDt) {
        showToast("Vui lòng chọn một hóa đơn từ danh sách trước khi xóa.", "warning");
        return;
    }

    const confirmed = confirm(`⚠️ CẢNH BÁO XÓA:\n\nBạn có chắc chắn muốn xóa vĩnh viễn hóa đơn [${currentSelectedDt}] khỏi Google Sheet không?\n\nThao tác này sẽ xóa dòng Header và tất cả dòng Chi tiết mặt hàng liên quan.`);
    if (!confirmed) return;

    const btn = document.getElementById('btnDeleteRecord');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Đang xóa...';
    }

    try {
        const response = await fetch(`${API_BASE}/sheets/record/${encodeURIComponent(currentSelectedDt)}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (response.ok && data.success) {
            showToast(`🗑️ Đã xóa thành công hóa đơn [${currentSelectedDt}] khỏi Google Sheet!`, 'success');
            
            const targetDt = currentSelectedDt;
            cachedRecords = (cachedRecords || []).filter(r => r.dt_code !== targetDt);
            currentFilteredRecords = (currentFilteredRecords || []).filter(r => r.dt_code !== targetDt);

            currentSelectedDt = null;
            resetForm();
            applyHistoricalFilters();
        } else {
            showToast(`Lỗi xóa hóa đơn: ${data.detail || data.error || 'Không thể thực hiện'}`, 'danger');
        }
    } catch (error) {
        console.error('Lỗi khi xóa hóa đơn:', error);
        showToast(`Lỗi kết nối máy chủ: ${error.message}`, 'danger');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-trash3-fill me-1"></i> Xóa Khỏi Sheet';
        }
    }
}

function navigateReceipt(offset) {
    const recordsToNav = (currentFilteredRecords && currentFilteredRecords.length > 0) ? currentFilteredRecords : cachedRecords;
    if (!recordsToNav || recordsToNav.length === 0) {
        showToast("Danh sách hóa đơn chưa được tải xong.", "warning");
        return;
    }
    
    let currentIndex = recordsToNav.findIndex(r => r.dt_code === currentSelectedDt);
    if (currentIndex === -1) {
        currentIndex = (offset > 0) ? 0 : recordsToNav.length - 1;
    } else {
        currentIndex += offset;
    }

    if (currentIndex < 0) {
        showToast("Đang ở hóa đơn đầu tiên trong bộ lọc.", "info");
        currentIndex = 0;
    } else if (currentIndex >= recordsToNav.length) {
        showToast("Đang ở hóa đơn cuối cùng trong bộ lọc.", "info");
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

// Lắng nghe sự kiện bàn phím: Mũi tên trái (←) và Mũi tên phải (→) để chuyển hóa đơn
document.addEventListener('keydown', (e) => {
    const select = document.getElementById('savedReceiptSelect');
    if (!select) return;

    const activeEl = document.activeElement;
    const isInputActive = activeEl && (
        ['INPUT', 'TEXTAREA', 'SELECT'].includes(activeEl.tagName) ||
        activeEl.isContentEditable
    );

    // Khi không focus vào ô nhập liệu: bấm trực tiếp mũi tên Trái / Phải
    // Khi đang nhập liệu: bấm Alt + Trái / Alt + Phải hoặc Ctrl + Trái / Ctrl + Phải để không ảnh hưởng con trỏ chữ
    if (e.key === 'ArrowLeft') {
        if (!isInputActive || e.altKey || e.ctrlKey) {
            e.preventDefault();
            navigateReceipt(-1);
        }
    } else if (e.key === 'ArrowRight') {
        if (!isInputActive || e.altKey || e.ctrlKey) {
            e.preventDefault();
            navigateReceipt(1);
        }
    }
});

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

let activeViewerRequestId = 0;

function onSelectSavedReceipt(dtCode) {
    if (!dtCode) {
        resetForm();
        return;
    }

    currentSelectedDt = dtCode;
    const currentRequestId = ++activeViewerRequestId;

    const record = cachedRecords.find(r => r.dt_code === dtCode);
    if (!record) return;

    // Reset viewer tức thì sang trạng thái chờ của hóa đơn hiện tại, xóa sạch ảnh cũ
    const preview = document.getElementById('receiptPreview');
    const pdfPreview = document.getElementById('receiptPdfPreview');
    if (preview) {
        preview.src = '';
        preview.classList.add('d-none');
        preview.classList.remove('opacity-50');
    }
    if (pdfPreview) {
        pdfPreview.src = '';
        pdfPreview.classList.add('d-none');
    }

    // 1. Tải ảnh/PDF từ Google Drive (có Cache & Google Drive Preview)
    let driveFileId = null;
    const linkUrl = record.file_info?.link || record.drive_link || '';
    
    // Match /file/d/{id}, /d/{id}, or id={id}
    const match = linkUrl.match(/\/file\/d\/([a-zA-Z0-9_-]+)/) ||
                  linkUrl.match(/\/d\/([a-zA-Z0-9_-]+)/) ||
                  linkUrl.match(/[?&]id=([a-zA-Z0-9_-]+)/);
    if (match && match[1]) {
        driveFileId = match[1];
    } else if (linkUrl && /^[a-zA-Z0-9_-]{20,}$/.test(linkUrl.trim())) {
        driveFileId = linkUrl.trim();
    }

    const isPdf = linkUrl.toLowerCase().includes('.pdf') || 
                  (record.filename && record.filename.toLowerCase().endsWith('.pdf')) ||
                  (record.file_info?.name && record.file_info.name.toLowerCase().endsWith('.pdf'));

    if (driveFileId) {
        const prompt = document.getElementById('uploadPrompt');
        if (prompt) prompt.classList.add('d-none');

        // Kiểm tra Content-Type chuẩn từ server thay vì đoán bằng đuôi file (vì link lịch sử không có đuôi)
        fetch(`${API_BASE}/drive/image/${driveFileId}`, { method: 'HEAD' })
            .then(res => {
                // Chống race condition nếu người dùng đã chuyển sang hóa đơn khác
                if (currentRequestId !== activeViewerRequestId || currentSelectedDt !== dtCode) return;

                const contentType = res.headers.get('content-type') || '';
                const isActualPdf = contentType.toLowerCase().includes('pdf') || isPdf;
                
                if (isActualPdf) {
                    setVerifyViewerMode('pdf', `${API_BASE}/drive/image/${driveFileId}#toolbar=1&navpanes=1`);
                } else {
                    setVerifyViewerMode('image', `${API_BASE}/drive/image/${driveFileId}`);
                    if (preview) {
                        preview.classList.add('opacity-50');
                        preview.onload = () => {
                            if (currentRequestId === activeViewerRequestId && currentSelectedDt === dtCode) {
                                preview.classList.remove('opacity-50');
                            }
                        };
                        preview.onerror = () => {
                            if (currentRequestId === activeViewerRequestId && currentSelectedDt === dtCode) {
                                preview.classList.remove('opacity-50');
                                setVerifyViewerMode('pdf', `${API_BASE}/drive/image/${driveFileId}#toolbar=1&navpanes=1`);
                            }
                        };
                    }
                }
            })
            .catch(err => {
                if (currentRequestId !== activeViewerRequestId || currentSelectedDt !== dtCode) return;
                console.warn("HEAD check failed, defaulting to direct image load:", err);
                if (isPdf) {
                    setVerifyViewerMode('pdf', `${API_BASE}/drive/image/${driveFileId}#toolbar=1&navpanes=1`);
                } else {
                    setVerifyViewerMode('image', `${API_BASE}/drive/image/${driveFileId}`);
                }
            });

        const curIdx = cachedRecords.findIndex(r => r.dt_code === dtCode);
        prefetchAdjacentImages(curIdx);
    } else {
        setVerifyViewerMode('empty');
        const prompt = document.getElementById('uploadPrompt');
        if (prompt) {
            prompt.classList.remove('d-none');
            const promptTitle = document.getElementById('promptTitle');
            if (promptTitle) promptTitle.textContent = `Hóa đơn [${dtCode}] chưa có link ảnh Google Drive đối soát`;
        }
    }

    // 2. Điền dữ liệu vào form theo từng phân loại (DT1 vs DT2, DT3, DT4)
    const dtPrefix = (record.dt_code || '').substring(0, 3).toUpperCase();
    const flatContainer = document.getElementById('flatFormContainer');
    const relationalContainer = document.getElementById('relationalFormContainer');
    const formTitle = document.getElementById('formHeaderTitle');

    // Điền dữ liệu cơ sở vào CẢ HAI form để đảm bảo khi chuyển đổi giữa DT1 <-> DT2/3/4 không bị rỗng dữ liệu
    const firstItem = (record.items && record.items[0]) || {};
    
    // 1. Điền Form Phẳng (DT1)
    if (document.getElementById('f_dt_code')) document.getElementById('f_dt_code').value = record.dt_code || '';
    if (document.getElementById('f_date')) document.getElementById('f_date').value = record.datetime || '';
    if (document.getElementById('f_company')) document.getElementById('f_company').value = record.merchant || '';
    if (document.getElementById('f_seller_address')) document.getElementById('f_seller_address').value = record.address_seller || '';
    if (document.getElementById('f_buyer_address')) document.getElementById('f_buyer_address').value = record.address_buyer || '';
    if (document.getElementById('f_order_id')) document.getElementById('f_order_id').value = record.order_id || '';
    if (document.getElementById('f_description')) document.getElementById('f_description').value = firstItem.item_name || '';
    if (document.getElementById('f_quantity')) document.getElementById('f_quantity').value = formatQuantity(firstItem.quantity || 1);
    if (document.getElementById('f_unit_price')) document.getElementById('f_unit_price').value = formatMoney(firstItem.price || record.total_raw_amount || 0);
    if (document.getElementById('f_discount_amount')) document.getElementById('f_discount_amount').value = formatMoney(firstItem.discount || record.total_discount_amount || 0);
    if (document.getElementById('f_vat_rate')) document.getElementById('f_vat_rate').value = firstItem.vat_rate || '0%';
    if (document.getElementById('f_vat_amount')) document.getElementById('f_vat_amount').value = formatMoney(firstItem.vat_amount || record.total_vat_amount || 0);
    if (document.getElementById('f_total_amount')) document.getElementById('f_total_amount').value = formatMoney(record.total_amount || firstItem.row_total || 0);
    if (document.getElementById('f_buyer_name')) document.getElementById('f_buyer_name').value = record.customer || '';
    if (document.getElementById('f_notes')) document.getElementById('f_notes').value = record.notes || '';

    // 2. Điền Form Quan Hệ (DT2, DT3, DT4)
    if (document.getElementById('f_h_dt_code')) document.getElementById('f_h_dt_code').value = record.dt_code || '';
    if (document.getElementById('f_h_date')) document.getElementById('f_h_date').value = record.datetime || '';
    if (document.getElementById('f_h_doc_code')) document.getElementById('f_h_doc_code').value = record.order_id || '';
    if (document.getElementById('f_h_company')) document.getElementById('f_h_company').value = record.merchant || '';
    if (document.getElementById('f_h_buyer_name')) document.getElementById('f_h_buyer_name').value = record.customer || '';
    if (document.getElementById('f_h_seller_addr')) document.getElementById('f_h_seller_addr').value = record.address_seller || '';
    if (document.getElementById('f_h_buyer_addr')) document.getElementById('f_h_buyer_addr').value = record.address_buyer || '';
    if (document.getElementById('f_h_raw_amount')) document.getElementById('f_h_raw_amount').value = formatMoney(record.total_raw_amount || firstItem.price || 0);
    if (document.getElementById('f_h_discount')) document.getElementById('f_h_discount').value = formatMoney(record.total_discount_amount || firstItem.discount || 0);
    if (document.getElementById('f_h_vat')) document.getElementById('f_h_vat').value = formatMoney(record.total_vat_amount || firstItem.vat_amount || 0);
    if (document.getElementById('f_h_final_payment')) document.getElementById('f_h_final_payment').value = formatMoney(record.total_amount || 0);
    if (document.getElementById('f_h_notes')) document.getElementById('f_h_notes').value = record.notes || '';

    // Render danh sách mặt hàng
    renderLinesTable(record.items || [], true);

    if (dtPrefix === 'DT1') {
        if (flatContainer) flatContainer.classList.remove('d-none');
        if (relationalContainer) relationalContainer.classList.add('d-none');
        if (formTitle) formTitle.innerHTML = `<i class="bi bi-cart3 me-2 text-primary"></i>Đơn Hàng TMĐT & Ship [${record.dt_code}]`;
    } else {
        if (flatContainer) flatContainer.classList.add('d-none');
        if (relationalContainer) relationalContainer.classList.remove('d-none');
        
        let catLabel = 'DT2 - Bán Lẻ Chung Quy';
        if (dtPrefix === 'DT3') catLabel = 'DT3 - Cung Ứng Thực Phẩm';
        else if (dtPrefix === 'DT4') catLabel = 'DT4 - Hóa Đơn Viết Tay';
        
        const badgeCat = document.getElementById('badgeCategoryType');
        if (badgeCat) badgeCat.textContent = catLabel;
        if (formTitle) formTitle.innerHTML = `<i class="bi bi-card-checklist me-2 text-success"></i>Hóa Đơn Chi Tiết [${record.dt_code}]`;
    }

    // Sync Form Type Selector dropdown
    const selector = document.getElementById('formTypeSelector');
    if (selector) selector.value = dtPrefix;

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

function syncFormDataBetweenModes(fromMode, toMode) {
    if (fromMode === 'RELATIONAL' && toMode === 'DT1') {
        // Sao chép từ Form Quan Hệ sang Form Phẳng
        if (document.getElementById('f_date')) document.getElementById('f_date').value = document.getElementById('f_h_date')?.value || '';
        if (document.getElementById('f_order_id')) document.getElementById('f_order_id').value = document.getElementById('f_h_doc_code')?.value || '';
        if (document.getElementById('f_company')) document.getElementById('f_company').value = document.getElementById('f_h_company')?.value || '';
        if (document.getElementById('f_seller_address')) document.getElementById('f_seller_address').value = document.getElementById('f_h_seller_addr')?.value || '';
        if (document.getElementById('f_buyer_address')) document.getElementById('f_buyer_address').value = document.getElementById('f_h_buyer_addr')?.value || '';
        if (document.getElementById('f_buyer_name')) document.getElementById('f_buyer_name').value = document.getElementById('f_h_buyer_name')?.value || '';
        if (document.getElementById('f_notes')) document.getElementById('f_notes').value = document.getElementById('f_h_notes')?.value || '';
        if (document.getElementById('f_unit_price')) document.getElementById('f_unit_price').value = document.getElementById('f_h_raw_amount')?.value || '0,00 đ';
        if (document.getElementById('f_discount_amount')) document.getElementById('f_discount_amount').value = document.getElementById('f_h_discount')?.value || '0,00 đ';
        if (document.getElementById('f_vat_amount')) document.getElementById('f_vat_amount').value = document.getElementById('f_h_vat')?.value || '0,00 đ';
        if (document.getElementById('f_total_amount')) document.getElementById('f_total_amount').value = document.getElementById('f_h_final_payment')?.value || '0,00 đ';

        // Lấy tên mặt hàng đầu tiên nếu có trong bảng lines
        const firstLineName = document.querySelector('#linesTableBody .line-name')?.value?.trim();
        if (firstLineName && document.getElementById('f_description')) {
            document.getElementById('f_description').value = firstLineName;
        }
        if (document.getElementById('f_dt_code')) {
            document.getElementById('f_dt_code').value = currentSelectedDt ? `${currentSelectedDt} ➔ DT1 (Tự động cấp)` : 'DT1 (Tự động cấp)';
        }
    } else if (fromMode === 'DT1') {
        // Sao chép từ Form Phẳng sang Form Quan Hệ
        if (document.getElementById('f_h_date')) document.getElementById('f_h_date').value = document.getElementById('f_date')?.value || '';
        if (document.getElementById('f_h_doc_code')) document.getElementById('f_h_doc_code').value = document.getElementById('f_order_id')?.value || '';
        if (document.getElementById('f_h_company')) document.getElementById('f_h_company').value = document.getElementById('f_company')?.value || '';
        if (document.getElementById('f_h_seller_addr')) document.getElementById('f_h_seller_addr').value = document.getElementById('f_seller_address')?.value || '';
        if (document.getElementById('f_h_buyer_addr')) document.getElementById('f_h_buyer_addr').value = document.getElementById('f_buyer_address')?.value || '';
        if (document.getElementById('f_h_buyer_name')) document.getElementById('f_h_buyer_name').value = document.getElementById('f_buyer_name')?.value || '';
        if (document.getElementById('f_h_notes')) document.getElementById('f_h_notes').value = document.getElementById('f_notes')?.value || '';
        if (document.getElementById('f_h_raw_amount')) document.getElementById('f_h_raw_amount').value = document.getElementById('f_unit_price')?.value || document.getElementById('f_total_amount')?.value || '0,00 đ';
        if (document.getElementById('f_h_discount')) document.getElementById('f_h_discount').value = document.getElementById('f_discount_amount')?.value || '0,00 đ';
        if (document.getElementById('f_h_vat')) document.getElementById('f_h_vat').value = document.getElementById('f_vat_amount')?.value || '0,00 đ';
        if (document.getElementById('f_h_final_payment')) document.getElementById('f_h_final_payment').value = document.getElementById('f_total_amount')?.value || '0,00 đ';

        if (document.getElementById('f_h_dt_code')) {
            document.getElementById('f_h_dt_code').value = currentSelectedDt ? `${currentSelectedDt} ➔ ${toMode} (Tự động cấp)` : `${toMode} (Tự động cấp)`;
        }

        // Tạo 1 dòng mặt hàng trong bảng lines nếu bảng trống
        const tbody = document.getElementById('linesTableBody');
        if (tbody && (tbody.children.length === 0 || (tbody.children.length === 1 && tbody.children[0].children.length === 1))) {
            const desc = document.getElementById('f_description')?.value?.trim() || 'Mặt hàng';
            const qty = parseFloat(document.getElementById('f_quantity')?.value) || 1;
            const price = parseMoney(document.getElementById('f_unit_price')?.value);
            const vat = parseMoney(document.getElementById('f_vat_amount')?.value);
            const tot = parseMoney(document.getElementById('f_total_amount')?.value);
            renderLinesTable([{
                item_name: desc,
                quantity: qty,
                price: price,
                vat_amount: vat,
                row_total: tot
            }], true);
        }
    }
}

function onFormTypeChanged(val) {
    const newPrefix = (val || 'DT1').substring(0, 3).toUpperCase();
    const flatContainer = document.getElementById('flatFormContainer');
    const relationalContainer = document.getElementById('relationalFormContainer');
    const formTitle = document.getElementById('formHeaderTitle');

    const wasFlat = flatContainer && !flatContainer.classList.contains('d-none');
    const isTargetFlat = newPrefix === 'DT1';

    if (wasFlat && !isTargetFlat) {
        syncFormDataBetweenModes('DT1', newPrefix);
    } else if (!wasFlat && isTargetFlat) {
        syncFormDataBetweenModes('RELATIONAL', 'DT1');
    }

    if (newPrefix === 'DT1') {
        if (flatContainer) flatContainer.classList.remove('d-none');
        if (relationalContainer) relationalContainer.classList.add('d-none');
        if (formTitle) {
            const isMigrating = currentSelectedDt && !currentSelectedDt.startsWith('DT1');
            formTitle.innerHTML = `<i class="bi bi-cart3 me-2 text-primary"></i>Đơn Hàng TMĐT & Ship ${isMigrating ? `[${currentSelectedDt} ➔ DT1]` : (currentSelectedDt ? `[${currentSelectedDt}]` : '')}`;
        }
    } else {
        if (flatContainer) flatContainer.classList.add('d-none');
        if (relationalContainer) relationalContainer.classList.remove('d-none');
        
        let catLabel = 'DT2 - Bán Lẻ Chung Quy';
        if (newPrefix === 'DT3') catLabel = 'DT3 - Cung Ứng Thực Phẩm';
        else if (newPrefix === 'DT4') catLabel = 'DT4 - Hóa Đơn Viết Tay';
        
        const badgeCat = document.getElementById('badgeCategoryType');
        if (badgeCat) badgeCat.textContent = catLabel;
        if (formTitle) {
            const isMigrating = currentSelectedDt && !currentSelectedDt.startsWith(newPrefix);
            formTitle.innerHTML = `<i class="bi bi-card-checklist me-2 text-success"></i>Hóa Đơn Chi Tiết ${isMigrating ? `[${currentSelectedDt} ➔ ${newPrefix}]` : (currentSelectedDt ? `[${currentSelectedDt}]` : '')}`;
        }

        const tbody = document.getElementById('linesTableBody');
        if (tbody && tbody.children.length === 0) {
            addLineItemRow();
        }
    }
}

// =============================================================================
// INTERACTIVE LINES TABLE FUNCTIONS (DT2, DT3, DT4)
// =============================================================================

function renderLinesTable(items, skipHeaderUpdate = false) {
    const tbody = document.getElementById('linesTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="12" class="text-center text-muted py-2">Chưa có dòng mặt hàng nào. Nhấn "+ Thêm Mặt Hàng" để thêm.</td></tr>';
        recomputeAllLinesAndHeader(skipHeaderUpdate);
        return;
    }

    items.forEach((it, idx) => {
        addLineItemRow(it, true); // skipRecompute=true, chỉ recompute 1 lần sau khi render xong
    });

    recomputeAllLinesAndHeader(skipHeaderUpdate);
}

function addLineItemRow(itemData = null, skipRecompute = false) {
    const tbody = document.getElementById('linesTableBody');
    if (!tbody) return;

    // Remove empty placeholder if any
    if (tbody.children.length === 1 && tbody.children[0].children.length === 1) {
        tbody.innerHTML = '';
    }

    const it = itemData || {
        product_code: '',
        item_name: '',
        quantity: 1,
        measurement_unit: '',
        price: 0,
        discount: 0,
        discount_rate: '0%',
        vat_rate: '0%',
        vat_amount: 0,
        row_total: 0
    };

    const rowIdx = tbody.children.length + 1;
    const tr = document.createElement('tr');
    tr.dataset.lineIndex = rowIdx;

    const rateNum = parseFloat(String(it.discount_rate || '0').replace('%', '').trim()) || 0;
    let vatRateNum = Math.round(parseFloat(String(it.vat_rate || '0').replace('%', '').trim().replace(',', '.')) || 0);

    const initQty = parseQuantity(it.quantity) || 1;
    const initPrice = parseMoney(it.price) || 0;
    const initDisc = parseMoney(it.discount) || 0;
    const initVatAmt = parseMoney(it.vat_amount) || 0;
    const initNetAmount = Math.max(0, (initQty * initPrice) - initDisc);

    // Tự động suy luận % VAT nếu dòng có Tiền VAT > 0 nhưng % VAT = 0 (khắc phục lỗi dữ liệu Sheet cũ không nhất quán)
    if (vatRateNum === 0 && initVatAmt > 0 && initNetAmount > 0) {
        const inferredRate = Math.round((initVatAmt / initNetAmount) * 100);
        if (inferredRate > 0) {
            vatRateNum = inferredRate;
        }
    }

    tr.innerHTML = `
        <td class="text-center fw-semibold text-muted line-row-num">${rowIdx}</td>
        <td><input type="text" class="form-control form-control-sm p-1 line-sku" value="${it.product_code || ''}" placeholder="SKU"></td>
        <td><input type="text" class="form-control form-control-sm p-1 line-name" value="${it.item_name || ''}" placeholder="Tên hàng hóa"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-center line-qty" value="${formatQuantity(it.quantity || 1)}" oninput="recomputeLineRow(this)" onblur="formatQuantityInput(this)" placeholder="0,0000" title="Số lượng (4 số sau dấu phẩy)"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-center line-uom" value="${it.measurement_unit || ''}" placeholder="ĐVT"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-end line-price" value="${formatMoney(it.price || 0)}" oninput="recomputeLineRow(this)"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-end text-primary line-discount" value="${formatMoney(it.discount || 0)}" oninput="recomputeLineRow(this, 'disc')"></td>
        <td><input type="number" step="any" class="form-control form-control-sm p-1 text-center text-primary line-discount-rate" value="${rateNum}" oninput="recomputeLineRow(this, 'rate')" placeholder="0%"></td>
        <td><input type="number" min="0" step="1" class="form-control form-control-sm p-1 text-center line-vat-rate" value="${vatRateNum}" oninput="recomputeLineRow(this, 'vat-rate')" placeholder="0" title="Thuế suất VAT (%), số nguyên dương"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-end bg-white line-vat-amount" value="${formatMoney(it.vat_amount || 0)}" oninput="recomputeLineRow(this, 'manual-vat')" placeholder="0 đ"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-end fw-bold text-success bg-white line-row-total" value="${formatMoney(it.row_total || 0)}" oninput="recomputeLineRow(this, 'manual-total')" placeholder="0 đ"></td>
        <td class="text-center">
            <button type="button" class="btn btn-sm btn-outline-danger p-0 px-1" onclick="deleteLineItemRow(this)" title="Xóa dòng này">
                <i class="bi bi-trash"></i>
            </button>
        </td>
    `;

    tbody.appendChild(tr);
    // Chỉ gọi recompute khi không được bỏ qua (khi được gọi từ renderLinesTable sẽ bỏ qua để chỉ chạy 1 lần cuối)
    if (!skipRecompute) {
        recomputeAllLinesAndHeader();
    }
}

function deleteLineItemRow(btn) {
    const tr = btn.closest('tr');
    if (tr) {
        tr.remove();
        const tbody = document.getElementById('linesTableBody');
        Array.from(tbody.querySelectorAll('.line-row-num')).forEach((td, idx) => {
            td.textContent = idx + 1;
        });
        recomputeAllLinesAndHeader();
    }
}

function recomputeLineRow(inputEl, sourceField = 'general') {
    const tr = inputEl.closest('tr');
    if (!tr) return;

    const rawQty = parseQuantity(tr.querySelector('.line-qty')?.value) || 0;
    const qty = Math.round((rawQty + Number.EPSILON) * 10000) / 10000;
    const price = parseMoney(tr.querySelector('.line-price')?.value) || 0;
    // disc = Tổng CK Mặt Hàng (luôn là tổng dòng, không phải CK đơn vị)
    let disc = parseMoney(tr.querySelector('.line-discount')?.value) || 0;
    let discRate = parseFloat(tr.querySelector('.line-discount-rate')?.value) || 0;
    // % VAT là input number — parse và làm tròn số nguyên dương
    const vatRateRaw = parseFloat(tr.querySelector('.line-vat-rate')?.value) || 0;
    const vatRateInt = Math.max(0, Math.round(vatRateRaw));
    const vatRate = vatRateInt / 100.0;

    const rawLine = qty * price; // Tiền gốc = SL × Đơn Giá

    // ============================================================
    // A. Tính Ngược (Reverse Computation) — khi user sửa Thành Tiền
    // Biết: totalAmount, vatRate, disc, qty → tính ngược Đơn Giá
    // Công thức: totalAmount = netAmount * (1 + vatRate)
    //            netAmount   = SL × Đơn Giá − Tổng CK MH
    // ============================================================
    if (sourceField === 'manual-total') {
        tr.dataset.manualTotal = 'true';
        const totalAmount = parseMoney(tr.querySelector('.line-row-total')?.value) || 0;
        if (qty > 0 && totalAmount > 0) {
            let netAmount;
            if (vatRate > 0) {
                netAmount = totalAmount / (1 + vatRate);
            } else {
                const manualVat = parseMoney(tr.querySelector('.line-vat-amount')?.value) || 0;
                netAmount = totalAmount - manualVat;
            }
            const vatAmount = Math.round(totalAmount - netAmount);
            const grossFromNet = Math.max(0, netAmount + disc); // = SL × Đơn Giá
            const newPrice = Math.round(grossFromNet / qty);
            // Tính ngược Đơn Giá
            tr.querySelector('.line-price').value = formatMoney(newPrice);
            // Cập nhật Tiền VAT nếu không bị ghi đè thủ công
            if (tr.dataset.manualVat !== 'true') {
                tr.querySelector('.line-vat-amount').value = formatMoney(Math.max(0, vatAmount));
            }
            // Cập nhật CK% nếu có chiết khấu
            const newRaw = newPrice * qty;
            if (newRaw > 0 && disc > 0) {
                const newDiscRate = (disc / newRaw) * 100;
                tr.querySelector('.line-discount-rate').value = Math.round(newDiscRate * 10) / 10;
            }
        }
        recomputeAllLinesAndHeader();
        return;
    }

    // ============================================================
    // B. Đồng bộ 2 chiều: Tổng CK MH (đ) ↔ CK (%)
    // Quy ước: disc = Tổng CK Mặt Hàng (toàn bộ dòng, không phải CK đơn vị)
    // ============================================================
    if (sourceField === 'rate') {
        // User nhập CK% → tính ra Tổng CK MH (đ)
        disc = rawLine > 0 ? Math.round(rawLine * (discRate / 100.0)) : 0;
        tr.querySelector('.line-discount').value = formatMoney(disc);
    } else if (sourceField === 'disc') {
        // User nhập Tổng CK MH (đ) → tính ra CK%
        discRate = rawLine > 0 ? ((disc / rawLine) * 100.0) : 0;
        tr.querySelector('.line-discount-rate').value = discRate > 0 ? (Math.round(discRate * 10) / 10) : 0;
    } else if (sourceField === 'general') {
        // Khi SL hoặc Đơn Giá thay đổi: ưu tiên CK% nếu có
        if (discRate > 0) {
            disc = Math.round(rawLine * (discRate / 100.0));
            tr.querySelector('.line-discount').value = formatMoney(disc);
        } else if (disc > 0) {
            discRate = rawLine > 0 ? ((disc / rawLine) * 100.0) : 0;
            tr.querySelector('.line-discount-rate').value = discRate > 0 ? (Math.round(discRate * 10) / 10) : 0;
        }
    }

    // Số tiền sau khi trừ Tổng CK MH
    const netAmount = Math.max(0, rawLine - disc);

    // ============================================================
    // C. Xử lý thủ công Tiền VAT
    // ============================================================
    if (sourceField === 'manual-vat') {
        tr.dataset.manualVat = 'true';
        const manualVat = parseMoney(tr.querySelector('.line-vat-amount')?.value) || 0;
        // Nhất quán: nếu có Tiền VAT > 0 nhưng % VAT = 0 → tự suy luận %VAT
        if (manualVat > 0 && netAmount > 0 && vatRateInt === 0) {
            const inferredRate = Math.round((manualVat / netAmount) * 100);
            if (inferredRate > 0) {
                tr.querySelector('.line-vat-rate').value = inferredRate;
            }
        }
        if (tr.dataset.manualTotal !== 'true') {
            tr.querySelector('.line-row-total').value = formatMoney(netAmount + manualVat);
        }
        recomputeAllLinesAndHeader();
        return;
    }

    // ============================================================
    // D. Khi user đổi % VAT → làm tròn số nguyên, reset cờ manual
    // ============================================================
    if (sourceField === 'vat-rate') {
        tr.querySelector('.line-vat-rate').value = vatRateInt;
        delete tr.dataset.manualVat;
        delete tr.dataset.manualTotal;
    }

    // ============================================================
    // E. Tính xuôi: Tiền VAT và Thành Tiền
    // Thành Tiền = (SL × Đơn Giá − Tổng CK MH) + Tiền VAT
    // ============================================================
    const vatAmount = Math.round(netAmount * vatRate);
    const finalVat = tr.dataset.manualVat === 'true'
        ? (parseMoney(tr.querySelector('.line-vat-amount')?.value) || 0)
        : vatAmount;
    const finalAmount = netAmount + finalVat;

    if (tr.dataset.manualVat !== 'true') {
        tr.querySelector('.line-vat-amount').value = formatMoney(vatAmount);
    }
    if (tr.dataset.manualTotal !== 'true') {
        tr.querySelector('.line-row-total').value = formatMoney(finalAmount);
    }

    recomputeAllLinesAndHeader();
}

function recomputeAllLinesAndHeader(skipHeaderUpdate = false) {
    const tbody = document.getElementById('linesTableBody');
    if (!tbody) return;

    let totalQty = 0;
    let totalRaw = 0;
    let totalDisc = 0;
    let totalVat = 0;
    let totalFinal = 0;
    let lineCount = 0;

    const rows = tbody.querySelectorAll('tr');
    rows.forEach(tr => {
        if (!tr.querySelector('.line-qty')) return;
        lineCount++;
        const rawQty = parseQuantity(tr.querySelector('.line-qty')?.value) || 0;
        const qty = Math.round((rawQty + Number.EPSILON) * 10000) / 10000;
        const price = parseMoney(tr.querySelector('.line-price')?.value) || 0;
        const disc = parseMoney(tr.querySelector('.line-discount')?.value) || 0;
        const vat = parseMoney(tr.querySelector('.line-vat-amount')?.value) || 0;
        const final = parseMoney(tr.querySelector('.line-row-total')?.value) || 0;

        totalQty += qty;
        totalRaw += (qty * price);
        totalDisc += disc;
        totalVat += vat;
        totalFinal += final;
    });

    totalQty = Math.round((totalQty + Number.EPSILON) * 10000) / 10000;
    totalRaw = Math.round(totalRaw);
    totalDisc = Math.round(totalDisc);
    totalVat = Math.round(totalVat);
    totalFinal = Math.round(totalFinal);

    const tfQty = document.getElementById('tfootTotalQty');
    const tfRaw = document.getElementById('tfootTotalRaw');
    const tfDisc = document.getElementById('tfootTotalDiscount');
    const tfVat = document.getElementById('tfootTotalVat');
    const tfFinal = document.getElementById('tfootTotalFinal');
    const linesBadge = document.getElementById('linesCountBadge');

    if (tfQty) tfQty.textContent = formatQuantity(totalQty);
    if (tfRaw) tfRaw.textContent = formatMoney(totalRaw);
    if (tfDisc) tfDisc.textContent = formatMoney(totalDisc);
    if (tfVat) tfVat.textContent = formatMoney(totalVat);
    if (tfFinal) tfFinal.textContent = formatMoney(totalFinal);
    if (linesBadge) linesBadge.textContent = `${lineCount} dòng`;

    // Cập nhật Header Form nếu KHÔNG phải đang load dữ liệu lịch sử từ Sheet
    if (!skipHeaderUpdate) {
        const hRaw = document.getElementById('f_h_raw_amount');
        const hDisc = document.getElementById('f_h_discount');
        const hVat = document.getElementById('f_h_vat');
        const hFinal = document.getElementById('f_h_final_payment');

        // Tổng Tiền Gốc = Σ(SL × Đơn Giá) — tự động từ lines
        if (hRaw && hRaw.dataset.manual !== 'true') hRaw.value = formatMoney(totalRaw);
        // Tổng VAT = Σ(Tiền VAT dòng) — tự động từ lines
        if (hVat && hVat.dataset.manual !== 'true') hVat.value = formatMoney(totalVat);

        // Tổng Thanh Toán = Σ(Thành Tiền các dòng) − Chiết Khấu Tổng Bill
        // CK Tổng Bill (hDisc) hoàn toàn tách biệt khỏi CK MH trong lines
        if (hFinal && hFinal.dataset.manual !== 'true') {
            const billDisc = parseMoney(hDisc?.value) || 0;
            // Công thức chuẩn từ Kế hoạch:
            const calculatedFinal = totalFinal - billDisc;
            hFinal.value = formatMoney(Math.max(0, calculatedFinal));
        }
    }

    // Đồng bộ cập nhật Ô 2 (Đối soát toán học) theo thời gian thực trên Form
    const boxDup = document.getElementById('boxDuplicate');
    const iconDup = document.getElementById('boxDuplicateIcon') || document.getElementById('iconDuplicate');
    const textDup = document.getElementById('boxDuplicateText') || document.getElementById('textDuplicate');
    if (boxDup && textDup) {
        const isDupWarn = (document.getElementById('f_h_notes')?.value || '').toUpperCase().includes('NGHI VẤN TRÙNG');
        if (!isDupWarn) {
            const curHDisc = parseMoney(document.getElementById('f_h_discount')?.value) || 0;
            const curHFinal = parseMoney(document.getElementById('f_h_final_payment')?.value) || totalFinal;

            if (totalFinal > 0 && curHFinal > 0) {
                const expected = totalFinal - curHDisc;
                const diff = Math.abs(expected - curHFinal);
                if (diff > 1000) {
                    boxDup.className = 'alert alert-warning py-2 px-3 mb-0 d-flex align-items-center gap-2 border-warning';
                    if (iconDup) iconDup.className = 'bi bi-calculator-fill fs-5 text-warning';
                    textDup.innerHTML = `<span class="fw-bold text-dark">⚠️ LỆCH TOÁN HỌC:</span> Tổng TT (${formatMoney(curHFinal)}) ≠ Σ(Thành Tiền các dòng) (${formatMoney(totalFinal)}) - CK Bill (${formatMoney(curHDisc)}) = ${formatMoney(expected)}`;
                } else {
                    boxDup.className = 'alert alert-success py-2 px-3 mb-0 d-flex align-items-center gap-2 border-success';
                    if (iconDup) iconDup.className = 'bi bi-shield-check fs-5 text-success';
                    textDup.innerHTML = `<span class="text-success fw-bold">✓ Khớp toán học 100%</span> (Không trùng lặp)`;
                }
            }
        }
    }
}

function recomputeRelationalHeader(source = 'general') {
    const hRaw = document.getElementById('f_h_raw_amount');
    const hFinal = document.getElementById('f_h_final_payment');
    const hDisc = document.getElementById('f_h_discount');
    const hVat = document.getElementById('f_h_vat');

    if (source === 'manual-raw' && hRaw) {
        hRaw.dataset.manual = 'true';
    }
    if (source === 'manual-disc' && hDisc) {
        hDisc.dataset.manual = 'true';
    }
    if (source === 'manual-vat' && hVat) {
        hVat.dataset.manual = 'true';
    }
    if (source === 'manual-final' && hFinal) {
        hFinal.dataset.manual = 'true';
        return;
    }

    const billDisc = parseMoney(hDisc?.value) || 0;
    const totalFinal = parseMoney(document.getElementById('tfootTotalFinal')?.textContent) || 0;
    
    // Quy tắc thống nhất: Tổng Thanh Toán = Σ(Thành Tiền các dòng) − Chiết Khấu Tổng Bill
    const final = totalFinal - billDisc;

    if (hFinal && hFinal.dataset.manual !== 'true') {
        hFinal.value = formatMoney(Math.max(0, final));
    }

    // Cập nhật lại Box 2
    recomputeAllLinesAndHeader(true);
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

    const formTypeSelector = document.getElementById('formTypeSelector');
    let dtPrefix = (currentSelectedDt || '').substring(0, 3).toUpperCase();
    if (formTypeSelector && formTypeSelector.value) {
        dtPrefix = formTypeSelector.value;
    }

    let payload = { 
        dt_code: currentSelectedDt,
        category: dtPrefix,
        form_type: dtPrefix
    };
    
    const rec = cachedRecords.find(r => r.dt_code === currentSelectedDt);
    const driveLink = rec ? (rec.drive_link || rec.file_info?.link || '') : '';

    if (dtPrefix === 'DT1') {
        const rawRow = getFormDataArray();
        rawRow[0] = currentSelectedDt; // Đảm bảo luôn sử dụng currentSelectedDt làm khóa mỏ neo
        payload.row = rawRow;
        payload.drive_link = driveLink;
    } else {
        let docCodeVal = document.getElementById('f_h_doc_code').value.trim();
        if (docCodeVal.startsWith('0') && docCodeVal.length > 1) {
            docCodeVal = "'" + docCodeVal;
        }

        const header_row = [
            currentSelectedDt, // Đảm bảo luôn sử dụng currentSelectedDt làm khóa mỏ neo
            document.getElementById('f_h_date').value.trim(),
            document.getElementById('f_h_company').value.trim(),
            document.getElementById('f_h_seller_addr').value.trim(),
            document.getElementById('f_h_buyer_addr').value.trim(),
            docCodeVal,
            parseMoney(document.getElementById('f_h_raw_amount').value),
            parseMoney(document.getElementById('f_h_discount').value),
            parseMoney(document.getElementById('f_h_vat').value),
            parseMoney(document.getElementById('f_h_final_payment').value),
            document.getElementById('f_h_buyer_name').value.trim(),
            driveLink,
            document.getElementById('f_h_notes').value.trim()
        ];

        const line_rows = [];
        const tbody = document.getElementById('linesTableBody');
        if (tbody) {
            tbody.querySelectorAll('tr').forEach(tr => {
                if (!tr.querySelector('.line-name')) return;
                let skuVal = tr.querySelector('.line-sku')?.value.trim() || '';
                if (skuVal.startsWith('0') && skuVal.length > 1) {
                    skuVal = "'" + skuVal;
                }
                line_rows.push([
                    currentSelectedDt,
                    skuVal,
                    tr.querySelector('.line-name')?.value.trim() || '',
                    Math.round((parseQuantity(tr.querySelector('.line-qty')?.value || 1) + Number.EPSILON) * 10000) / 10000,
                    tr.querySelector('.line-uom')?.value.trim() || '',
                    parseMoney(tr.querySelector('.line-price')?.value),
                    parseMoney(tr.querySelector('.line-discount')?.value),
                    parseFloat(tr.querySelector('.line-discount-rate')?.value) || 0,
                    (tr.querySelector('.line-vat-rate')?.value || '0%').replace('%', '').trim(),
                    parseMoney(tr.querySelector('.line-vat-amount')?.value),
                    parseMoney(tr.querySelector('.line-row-total')?.value),
                    ''
                ]);
            });
        }

        payload.header_row = header_row;
        payload.line_rows = line_rows;
        payload.drive_link = driveLink;
    }

    try {
        const response = await fetch(`${API_BASE}/sheets/record`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
            showToast(`✅ ${data.message || 'Cập nhật thành công!'}`, 'success');
            if (data.new_dt_code) {
                currentSelectedDt = data.new_dt_code;
                const filterCat = document.getElementById('filterCategory');
                if (filterCat) filterCat.value = 'ALL';
            }
            await loadHistoricalRecords(false, false);
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

function populateVerificationForm(data) {
    const raw = data.raw_parsed_data || {};
    const cat = data.category || {};
    const warns = data.warnings || {};
    const alertBox = document.getElementById('alertContainer');
    const catPrefix = cat.code_prefix || 'DT1';

    let alertHtml = '';
    alertHtml += `<div class="d-flex gap-2 align-items-center mb-2">
        <span class="badge bg-primary fs-6">${catPrefix}</span>
        <span class="fw-bold text-dark">${cat.name || 'Hóa đơn'}</span>
    </div>`;

    if (warns.is_duplicate) {
        alertHtml += `<div class="alert alert-danger py-2 small mb-2"><i class="bi bi-exclamation-triangle-fill me-1"></i> <strong>CẢNH BÁO TRÙNG LẶP:</strong> ${warns.duplicate_message || 'Mã hóa đơn/vận đơn đã tồn tại trong Sheet!'}</div>`;
    }
    if (warns.has_vat) {
        alertHtml += `<div class="alert alert-warning py-2 small mb-2"><i class="bi bi-receipt me-1"></i> ${warns.vat_alert || 'Hóa đơn có thuế VAT'}</div>`;
    }
    if (alertBox) alertBox.innerHTML = alertHtml;

    const flatContainer = document.getElementById('flatFormContainer');
    const relationalContainer = document.getElementById('relationalFormContainer');
    const formTitle = document.getElementById('formHeaderTitle');
    const formTypeSel = document.getElementById('formTypeSelector');
    if (formTypeSel) formTypeSel.value = catPrefix;

    if (catPrefix === 'DT1') {
        if (flatContainer) flatContainer.classList.remove('d-none');
        if (relationalContainer) relationalContainer.classList.add('d-none');
        if (formTitle) formTitle.innerHTML = `<i class="bi bi-cart3 me-2 text-primary"></i>Đơn Hàng TMĐT & Ship (${catPrefix})`;

        document.getElementById('f_dt_code').value = catPrefix;
        document.getElementById('f_date').value = raw.date || raw.invoice_date || new Date().toLocaleDateString('en-GB');
        document.getElementById('f_order_id').value = raw.order_id || raw.tracking_number || raw.invoice_number || '';
        document.getElementById('f_company').value = raw.merchant_name || raw.seller_name || '';
        document.getElementById('f_seller_address').value = raw.merchant_address || raw.seller_address || '';
        document.getElementById('f_buyer_address').value = raw.customer_address || raw.buyer_address || '';

        let items = raw.line_items || raw.items || [];
        let itemName = items.length > 0 ? items.map(i => i.item_name || i.name).join('; ') : (raw.description || 'Đơn hàng TMĐT');

        document.getElementById('f_description').value = itemName;
        document.getElementById('f_quantity').value = items.length > 0 ? (items[0].item_quantity || items[0].quantity || 1) : 1;
        document.getElementById('f_unit_price').value = formatMoney(raw.subtotal_amount || raw.total_amount || 0);
        document.getElementById('f_discount_amount').value = formatMoney(raw.discount_amount || 0);

        let vatRate = raw.tax_rate || raw.vat_rate || 0;
        document.getElementById('f_vat_rate').value = vatRate > 0 ? `${(vatRate*100).toFixed(0)}%` : '0%';
        document.getElementById('f_vat_amount').value = formatMoney(raw.tax_amount || raw.vat_amount || 0);
        document.getElementById('f_total_amount').value = formatMoney(raw.total_amount || 0);
        document.getElementById('f_buyer_name').value = raw.customer_name || raw.buyer_name || '';
        document.getElementById('f_notes').value = (raw.notes || '').trim();
    } else {
        if (flatContainer) flatContainer.classList.add('d-none');
        if (relationalContainer) relationalContainer.classList.remove('d-none');
        if (formTitle) formTitle.innerHTML = `<i class="bi bi-card-checklist me-2 text-success"></i>Hóa Đơn Chi Tiết (${catPrefix})`;

        document.getElementById('f_h_dt_code').value = catPrefix;
        document.getElementById('f_h_date').value = raw.date || raw.invoice_date || new Date().toLocaleDateString('en-GB');
        document.getElementById('f_h_doc_code').value = raw.invoice_number || raw.receipt_number || '';
        document.getElementById('f_h_company').value = raw.merchant_name || raw.seller_name || '';
        document.getElementById('f_h_buyer_name').value = raw.customer_name || raw.buyer_name || '';
        document.getElementById('f_h_seller_addr').value = raw.merchant_address || raw.seller_address || '';
        document.getElementById('f_h_buyer_addr').value = raw.customer_address || raw.buyer_address || '';
        document.getElementById('f_h_raw_amount').value = formatMoney(raw.subtotal_amount || raw.total_raw_amount || 0);
        document.getElementById('f_h_discount').value = formatMoney(raw.discount_amount || raw.total_discount_amount || 0);
        document.getElementById('f_h_vat').value = formatMoney(raw.tax_amount || raw.vat_amount || 0);
        document.getElementById('f_h_final_payment').value = formatMoney(raw.total_amount || 0);
        document.getElementById('f_h_notes').value = (raw.notes || '').trim();

        let items = raw.line_items || raw.items || [];
        renderLinesTable(items);
    }

    updateStatusBoxes(warns.is_duplicate, warns.duplicate_message, false, catPrefix);
}

function recomputeAmounts() {
    const qty = parseMoney(document.getElementById('f_quantity').value) || 1;
    const price = parseMoney(document.getElementById('f_unit_price').value) || 0;
    const discount = parseMoney(document.getElementById('f_discount_amount')?.value) || 0;
    const vatAmt = parseMoney(document.getElementById('f_vat_amount').value) || 0;
    
    const subtotal = (qty * price) - discount + vatAmt;
    document.getElementById('f_total_amount').value = formatMoney(subtotal);
}

async function saveReceiptData() {
    const btnSave = document.getElementById('btnSaveNewSheet');
    btnSave.disabled = true;
    btnSave.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang lưu vào Sheet...';

    const dtCode = document.getElementById('f_dt_code')?.value || 'DT10001';
    const dtPrefix = dtCode.substring(0, 3).toUpperCase();
    let payload = {};

    if (dtPrefix === 'DT1') {
        const row = getFormDataArray();
        payload = { rows: [row] };
    } else {
        const header_row = [
            document.getElementById('f_h_dt_code').value.trim(),
            document.getElementById('f_h_date').value.trim(),
            document.getElementById('f_h_company').value.trim(),
            document.getElementById('f_h_seller_addr').value.trim(),
            document.getElementById('f_h_buyer_addr').value.trim(),
            document.getElementById('f_h_doc_code').value.trim(),
            document.getElementById('f_h_raw_amount').value.trim(),
            document.getElementById('f_h_discount').value.trim(),
            document.getElementById('f_h_vat').value.trim(),
            document.getElementById('f_h_final_payment').value.trim(),
            document.getElementById('f_h_buyer_name').value.trim(),
            '',
            document.getElementById('f_h_notes').value.trim()
        ];

        const line_rows = [];
        const tbody = document.getElementById('linesTableBody');
        if (tbody) {
            tbody.querySelectorAll('tr').forEach(tr => {
                if (!tr.querySelector('.line-name')) return;
                line_rows.push([
                    header_row[0],
                    tr.querySelector('.line-sku')?.value.trim() || '',
                    tr.querySelector('.line-name')?.value.trim() || '',
                    tr.querySelector('.line-qty')?.value.trim() || '1',
                    tr.querySelector('.line-price')?.value.trim() || '0,00 đ',
                    tr.querySelector('.line-discount')?.value.trim() || '0,00 đ',
                    (tr.querySelector('.line-discount-rate')?.value.trim() || '0') + '%',
                    tr.querySelector('.line-vat-rate')?.value.trim() || '0%',
                    tr.querySelector('.line-vat-amount')?.value.trim() || '0,00 đ',
                    tr.querySelector('.line-row-total')?.value.trim() || '0,00 đ',
                    ''
                ]);
            });
        }

        payload = { headers: [header_row], lines: line_rows };
    }

    try {
        const response = await fetch(`${API_BASE}/sheets/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (response.ok) {
            showToast(`🎉 Đã lưu thành công [${dtCode}] vào Google Sheet V2!`, 'success');
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


// =============================================================================
// MANUAL ENTRY LOGIC (manual.html) - DUAL FORM SWITCHER & RELATIONAL ENTRY
// =============================================================================
let unprocessedFiles = [];

async function loadUnprocessedFiles() {
    const select = document.getElementById('unprocessedFilesSelect');
    if (!select) return;
    
    select.innerHTML = '<option value="">-- Đang tải danh sách file... --</option>';
    try {
        const response = await fetch(`${API_BASE}/drive/unprocessed-files`);
        const data = await response.json();
        
        unprocessedFiles = data.files || [];
        if (unprocessedFiles.length === 0) {
            select.innerHTML = '<option value="">-- Không có file nào trong thư mục Chưa xử lý --</option>';
            return;
        }
        
        let html = '<option value="">-- Chọn file từ thư mục Chưa xử lý --</option>';
        unprocessedFiles.forEach(f => {
            html += `<option value="${f.id}">${f.name}</option>`;
        });
        select.innerHTML = html;
    } catch (e) {
        console.error('Error loading files:', e);
        select.innerHTML = '<option value="">-- Lỗi tải danh sách file --</option>';
    }
}

function handleUnprocessedFileSelected() {
    const select = document.getElementById('unprocessedFilesSelect');
    const fileId = select.value;
    const preview = document.getElementById('manualImagePreview');
    const prompt = document.getElementById('manualUploadPrompt');
    
    if (fileId) {
        preview.src = `${API_BASE}/drive/image/${fileId}`;
        preview.classList.remove('d-none');
        resetImageTransform('manual');
        if (prompt) prompt.classList.add('d-none');
    } else {
        preview.src = '';
        preview.classList.add('d-none');
        if (prompt) prompt.classList.remove('d-none');
    }
}

function onManualFormTypeChanged(val) {
    const dtPrefix = (val || 'DT4').substring(0, 3).toUpperCase();
    const flatContainer = document.getElementById('manualFlatContainer');
    const relationalContainer = document.getElementById('manualRelationalContainer');
    const badge = document.getElementById('manualCategoryBadge');

    if (dtPrefix === 'DT1') {
        if (flatContainer) flatContainer.classList.remove('d-none');
        if (relationalContainer) relationalContainer.classList.add('d-none');
        if (badge) {
            badge.className = 'badge bg-primary';
            badge.textContent = 'DT1: Sàn TMĐT & Ship';
        }
        const dtInput = document.getElementById('m_dt_code');
        if (dtInput) dtInput.value = 'DT1 (Auto)';
    } else {
        if (flatContainer) flatContainer.classList.add('d-none');
        if (relationalContainer) relationalContainer.classList.remove('d-none');
        
        let catLabel = 'DT2: Bán Lẻ Chung Quy';
        let badgeColor = 'bg-success';
        if (dtPrefix === 'DT3') {
            catLabel = 'DT3: Nông Sản';
            badgeColor = 'bg-warning text-dark';
        } else if (dtPrefix === 'DT4') {
            catLabel = 'DT4: Viết Tay';
            badgeColor = 'bg-danger';
        }
        if (badge) {
            badge.className = `badge ${badgeColor}`;
            badge.textContent = catLabel;
        }

        const dtInput = document.getElementById('m_h_dt_code');
        if (dtInput) dtInput.value = `${dtPrefix} (Auto)`;

        const tbody = document.getElementById('manualLinesTableBody');
        if (tbody && tbody.children.length === 0) {
            addManualLineItemRow();
        }
    }
}

function addManualLineItemRow(itemData = null) {
    const tbody = document.getElementById('manualLinesTableBody');
    if (!tbody) return;

    if (tbody.children.length === 1 && tbody.children[0].children.length === 1) {
        tbody.innerHTML = '';
    }

    const it = itemData || {
        product_code: '',
        item_name: '',
        quantity: 1,
        measurement_unit: '',
        price: 0,
        discount: 0,
        discount_rate: '0%',
        vat_rate: '0%',
        vat_amount: 0,
        row_total: 0
    };

    const rowIdx = tbody.children.length + 1;
    const tr = document.createElement('tr');
    tr.dataset.lineIndex = rowIdx;

    const rateNum = parseFloat(String(it.discount_rate || '0').replace('%', '').trim()) || 0;
    const vatRateNum = Math.round(parseFloat(String(it.vat_rate || '0').replace('%', '').trim().replace(',', '.')) || 0);

    tr.innerHTML = `
        <td class="text-center fw-semibold text-muted m-line-row-num">${rowIdx}</td>
        <td><input type="text" class="form-control form-control-sm p-1 m-line-sku" value="${it.product_code || ''}" placeholder="SKU"></td>
        <td><input type="text" class="form-control form-control-sm p-1 m-line-name" value="${it.item_name || ''}" placeholder="Tên hàng hóa"></td>
        <td><input type="number" step="any" class="form-control form-control-sm p-1 text-center m-line-qty" value="${parseMoney(it.quantity) || 1}" oninput="recomputeManualLineRow(this)"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-center m-line-uom" value="${it.measurement_unit || ''}" placeholder="ĐVT"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-end m-line-price" value="${formatMoney(it.price || 0)}" oninput="recomputeManualLineRow(this)"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-end text-primary m-line-discount" value="${formatMoney(it.discount || 0)}" oninput="recomputeManualLineRow(this, 'disc')"></td>
        <td><input type="number" step="any" class="form-control form-control-sm p-1 text-center text-primary m-line-discount-rate" value="${rateNum}" oninput="recomputeManualLineRow(this, 'rate')" placeholder="0%"></td>
        <td>
            <select class="form-select form-select-sm p-1 m-line-vat-rate" onchange="recomputeManualLineRow(this, 'vat-rate')">
                <option value="0%" ${vatRateNum === 0 ? 'selected' : ''}>0%</option>
                <option value="5%" ${vatRateNum === 5 ? 'selected' : ''}>5%</option>
                <option value="8%" ${vatRateNum === 8 ? 'selected' : ''}>8%</option>
                <option value="10%" ${vatRateNum === 10 ? 'selected' : ''}>10%</option>
            </select>
        </td>
        <td><input type="text" class="form-control form-control-sm p-1 text-end bg-white m-line-vat-amount" value="${formatMoney(it.vat_amount || 0)}" oninput="recomputeManualLineRow(this, 'manual-vat')" placeholder="0 đ"></td>
        <td><input type="text" class="form-control form-control-sm p-1 text-end fw-bold text-success bg-white m-line-row-total" value="${formatMoney(it.row_total || 0)}" oninput="recomputeManualLineRow(this, 'manual-total')" placeholder="0 đ"></td>
        <td class="text-center">
            <button type="button" class="btn btn-sm btn-outline-danger p-0 px-1" onclick="deleteManualLineItemRow(this)" title="Xóa dòng">
                <i class="bi bi-trash"></i>
            </button>
        </td>
    `;

    tbody.appendChild(tr);
    recomputeAllManualLinesAndHeader();
}

function deleteManualLineItemRow(btn) {
    const tr = btn.closest('tr');
    if (tr) {
        tr.remove();
        const tbody = document.getElementById('manualLinesTableBody');
        Array.from(tbody.querySelectorAll('.m-line-row-num')).forEach((td, idx) => {
            td.textContent = idx + 1;
        });
        recomputeAllManualLinesAndHeader();
    }
}

function recomputeManualLineRow(inputEl, sourceField = 'general') {
    const tr = inputEl.closest('tr');
    if (!tr) return;

    const qty = parseFloat(tr.querySelector('.m-line-qty')?.value) || 0;
    const price = parseMoney(tr.querySelector('.m-line-price')?.value) || 0;
    let disc = parseMoney(tr.querySelector('.m-line-discount')?.value) || 0;
    let discRate = parseFloat(tr.querySelector('.m-line-discount-rate')?.value) || 0;
    const vatRateStr = tr.querySelector('.m-line-vat-rate')?.value || '0%';
    const vatRate = parseFloat(vatRateStr.replace('%', '')) / 100.0 || 0;

    const rawLine = qty * price;

    if (sourceField === 'rate') {
        disc = Math.round(rawLine * (discRate / 100.0));
        tr.querySelector('.m-line-discount').value = formatMoney(disc);
    } else if (sourceField === 'disc') {
        discRate = rawLine > 0 ? ((disc / rawLine) * 100.0) : 0;
        tr.querySelector('.m-line-discount-rate').value = discRate > 0 ? (Math.round(discRate * 10) / 10) : 0;
    } else if (sourceField === 'general') {
        if (discRate > 0) {
            disc = Math.round(rawLine * (discRate / 100.0));
            tr.querySelector('.m-line-discount').value = formatMoney(disc);
        } else if (disc > 0) {
            discRate = rawLine > 0 ? ((disc / rawLine) * 100.0) : 0;
            tr.querySelector('.m-line-discount-rate').value = discRate > 0 ? (Math.round(discRate * 10) / 10) : 0;
        }
    }

    const netAmount = Math.max(0, rawLine - disc);

    if (sourceField === 'manual-vat') {
        tr.dataset.manualVat = 'true';
        const manualVat = parseMoney(tr.querySelector('.m-line-vat-amount')?.value) || 0;
        if (tr.dataset.manualTotal !== 'true') {
            tr.querySelector('.m-line-row-total').value = formatMoney(netAmount + manualVat);
        }
        recomputeAllManualLinesAndHeader();
        return;
    }

    if (sourceField === 'manual-total') {
        tr.dataset.manualTotal = 'true';
        recomputeAllManualLinesAndHeader();
        return;
    }

    if (sourceField === 'vat-rate') {
        delete tr.dataset.manualVat;
        delete tr.dataset.manualTotal;
    }

    const vatAmount = Math.round(netAmount * vatRate);
    const finalVat = tr.dataset.manualVat === 'true' ? (parseMoney(tr.querySelector('.m-line-vat-amount')?.value) || 0) : vatAmount;
    const finalAmount = netAmount + finalVat;

    if (tr.dataset.manualVat !== 'true') {
        tr.querySelector('.m-line-vat-amount').value = formatMoney(vatAmount);
    }
    if (tr.dataset.manualTotal !== 'true') {
        tr.querySelector('.m-line-row-total').value = formatMoney(finalAmount);
    }

    recomputeAllManualLinesAndHeader();
}

function recomputeAllManualLinesAndHeader() {
    const tbody = document.getElementById('manualLinesTableBody');
    if (!tbody) return;

    let totalQty = 0;
    let totalRaw = 0;
    let totalDisc = 0;
    let totalVat = 0;
    let totalFinal = 0;
    let lineCount = 0;

    const rows = tbody.querySelectorAll('tr');
    rows.forEach(tr => {
        if (!tr.querySelector('.m-line-qty')) return;
        lineCount++;
        const qty = parseFloat(tr.querySelector('.m-line-qty')?.value) || 0;
        const price = parseMoney(tr.querySelector('.m-line-price')?.value) || 0;
        const disc = parseMoney(tr.querySelector('.m-line-discount')?.value) || 0;
        const vat = parseMoney(tr.querySelector('.m-line-vat-amount')?.value) || 0;
        const final = parseMoney(tr.querySelector('.m-line-row-total')?.value) || 0;

        totalQty += qty;
        totalRaw += (qty * price);
        totalDisc += disc;
        totalVat += vat;
        totalFinal += final;
    });

    totalQty = Math.round((totalQty + Number.EPSILON) * 100) / 100;
    totalRaw = Math.round(totalRaw);
    totalDisc = Math.round(totalDisc);
    totalVat = Math.round(totalVat);
    totalFinal = Math.round(totalFinal);

    const tfQty = document.getElementById('m_tfootTotalQty');
    const tfRaw = document.getElementById('m_tfootTotalRaw');
    const tfDisc = document.getElementById('m_tfootTotalDiscount');
    const tfVat = document.getElementById('m_tfootTotalVat');
    const tfFinal = document.getElementById('m_tfootTotalFinal');
    const linesBadge = document.getElementById('m_linesCountBadge');

    if (tfQty) tfQty.textContent = totalQty;
    if (tfRaw) tfRaw.textContent = formatMoney(totalRaw);
    if (tfDisc) tfDisc.textContent = formatMoney(totalDisc);
    if (tfVat) tfVat.textContent = formatMoney(totalVat);
    if (tfFinal) tfFinal.textContent = formatMoney(totalFinal);
    if (linesBadge) linesBadge.textContent = `${lineCount} dòng`;

    const hRaw = document.getElementById('m_h_raw_amount');
    const hDisc = document.getElementById('m_h_discount');
    const hVat = document.getElementById('m_h_vat');
    const hFinal = document.getElementById('m_h_final_payment');

    if (hRaw && hRaw.dataset.manual !== 'true') hRaw.value = formatMoney(totalRaw);
    if (hVat && hVat.dataset.manual !== 'true') hVat.value = formatMoney(totalVat);

    if (hFinal && hFinal.dataset.manual !== 'true') {
        const billDisc = parseMoney(hDisc?.value) || 0;
        const currentHeaderVat = (hVat && hVat.dataset.manual === 'true') ? parseMoney(hVat.value) : totalVat;
        const calculatedFinal = (totalFinal - billDisc) + (currentHeaderVat - totalVat);
        hFinal.value = formatMoney(Math.max(0, calculatedFinal));
    }
}

function recomputeManualRelationalHeader(source = 'general') {
    const hRaw = document.getElementById('m_h_raw_amount');
    const hFinal = document.getElementById('m_h_final_payment');
    const hDisc = document.getElementById('m_h_discount');
    const hVat = document.getElementById('m_h_vat');

    if (source === 'manual-raw' && hRaw) {
        hRaw.dataset.manual = 'true';
    }
    if (source === 'manual-disc' && hDisc) {
        hDisc.dataset.manual = 'true';
    }
    if (source === 'manual-vat' && hVat) {
        hVat.dataset.manual = 'true';
    }
    if (source === 'manual-final' && hFinal) {
        hFinal.dataset.manual = 'true';
        return;
    }

    const raw = parseMoney(hRaw?.value) || 0;
    const disc = parseMoney(hDisc?.value) || 0;
    const vat = parseMoney(hVat?.value) || 0;
    const final = raw - disc + vat;

    if (hFinal && hFinal.dataset.manual !== 'true') {
        hFinal.value = formatMoney(Math.max(0, final));
    }
}

function resetManualForm() {
    document.getElementById('manualVerifyForm')?.reset();
    
    const today = new Date().toLocaleDateString('en-GB');
    const mDate = document.getElementById('m_date');
    if (mDate) mDate.value = today;
    const mhDate = document.getElementById('m_h_date');
    if (mhDate) mhDate.value = today;
    
    const select = document.getElementById('unprocessedFilesSelect');
    if (select) select.value = '';
    handleUnprocessedFileSelected();

    const tbody = document.getElementById('manualLinesTableBody');
    if (tbody) {
        tbody.innerHTML = '';
        addManualLineItemRow();
    }
}

function recomputeManualAmounts() {
    const qty = parseMoney(document.getElementById('m_quantity')?.value) || 1;
    const price = parseMoney(document.getElementById('m_unit_price')?.value) || 0;
    const discount = parseMoney(document.getElementById('m_discount_amount')?.value) || 0;
    const vatAmt = parseMoney(document.getElementById('m_vat_amount')?.value) || 0;
    
    const subtotal = (qty * price) - discount + vatAmt;
    const totalEl = document.getElementById('m_total_amount');
    if (totalEl) totalEl.value = formatMoney(subtotal);
}

async function submitManualReceipt() {
    const select = document.getElementById('unprocessedFilesSelect');
    const fileId = select ? select.value : '';
    if (!fileId) {
        showToast('Vui lòng chọn ảnh hóa đơn từ danh sách!', 'danger');
        return;
    }
    
    const selectedOption = select.options[select.selectedIndex];
    const filename = selectedOption ? selectedOption.text : 'unknown.jpg';
    const formType = document.getElementById('manualFormTypeSelector')?.value || 'DT4';
    
    const btn = document.getElementById('btnSubmitManual');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Đang lưu vào Sheet...';
    
    try {
        let payload = {
            file_id: fileId,
            filename: filename,
            category: formType
        };

        if (formType === 'DT1') {
            payload.date = document.getElementById('m_date')?.value.trim();
            payload.company = document.getElementById('m_company')?.value.trim();
            payload.seller_address = document.getElementById('m_seller_address')?.value.trim();
            payload.buyer_address = document.getElementById('m_buyer_address')?.value.trim();
            payload.order_id = document.getElementById('m_order_id')?.value.trim();
            payload.description = document.getElementById('m_description')?.value.trim();
            payload.quantity = document.getElementById('m_quantity')?.value.trim();
            payload.unit_price = document.getElementById('m_unit_price')?.value.trim();
            payload.discount_amount = document.getElementById('m_discount_amount')?.value.trim();
            payload.vat_rate = document.getElementById('m_vat_rate')?.value.trim();
            payload.vat_amount = document.getElementById('m_vat_amount')?.value.trim();
            payload.total_amount = document.getElementById('m_total_amount')?.value.trim();
            payload.buyer_name = document.getElementById('m_buyer_name')?.value.trim();
            payload.notes = document.getElementById('m_notes')?.value.trim();
        } else {
            const header_row = [
                formType,
                document.getElementById('m_h_date')?.value.trim(),
                document.getElementById('m_h_company')?.value.trim(),
                document.getElementById('m_h_seller_addr')?.value.trim(),
                document.getElementById('m_h_buyer_addr')?.value.trim(),
                document.getElementById('m_h_doc_code')?.value.trim(),
                document.getElementById('m_h_raw_amount')?.value.trim(),
                document.getElementById('m_h_discount')?.value.trim(),
                document.getElementById('m_h_vat')?.value.trim(),
                document.getElementById('m_h_final_payment')?.value.trim(),
                document.getElementById('m_h_buyer_name')?.value.trim(),
                '',
                document.getElementById('m_h_notes')?.value.trim()
            ];

            const line_rows = [];
            const tbody = document.getElementById('manualLinesTableBody');
            if (tbody) {
                tbody.querySelectorAll('tr').forEach(tr => {
                    if (!tr.querySelector('.m-line-name')) return;
                    line_rows.push([
                        formType,
                        tr.querySelector('.m-line-sku')?.value.trim() || '',
                        tr.querySelector('.m-line-name')?.value.trim() || '',
                        tr.querySelector('.m-line-qty')?.value.trim() || '1',
                        tr.querySelector('.m-line-uom')?.value.trim() || '',
                        tr.querySelector('.m-line-price')?.value.trim() || '0,00 đ',
                        tr.querySelector('.m-line-discount')?.value.trim() || '0,00 đ',
                        (tr.querySelector('.m-line-discount-rate')?.value.trim() || '0') + '%',
                        tr.querySelector('.m-line-vat-rate')?.value.trim() || '0%',
                        tr.querySelector('.m-line-vat-amount')?.value.trim() || '0,00 đ',
                        tr.querySelector('.m-line-row-total')?.value.trim() || '0,00 đ',
                        ''
                    ]);
                });
            }

            payload.header_row = header_row;
            payload.line_rows = line_rows;
        }
        
        const response = await fetch(`${API_BASE}/manual-entry`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showToast(`✅ Lưu thành công! Hóa đơn được cấp mã: ${result.dt_code}`, 'success');
            
            const preview = document.getElementById('manualImagePreview');
            const prompt = document.getElementById('manualUploadPrompt');
            if (preview) {
                preview.src = '';
                preview.classList.add('d-none');
            }
            if (prompt) prompt.classList.remove('d-none');
            
            resetManualForm();
            loadUnprocessedFiles();
        } else {
            showToast(`Lỗi: ${result.detail || 'Không thể lưu hóa đơn'}`, 'danger');
        }
    } catch (error) {
        showToast(`Lỗi mạng: ${error.message}`, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-cloud-arrow-up me-1"></i> Lưu Hóa Đơn Vào Sheet V2';
    }
}

// =============================================================================
// UI MODULE: NÂNG CẤP THANH KÉO CHIA KHUNG (SPLIT RESIZER)
// =============================================================================

function initSplitResizers() {
    setupSingleSplitter('mainSplitContainer', 'splitResizer', 'dropZone', 'splitRightContainer');
    setupSingleSplitter('manualSplitContainer', 'manualSplitResizer', 'manualUploadZone', 'manualSplitRightContainer');
}

function setupSingleSplitter(containerId, resizerId, leftId, rightId) {
    const container = document.getElementById(containerId);
    const resizer = document.getElementById(resizerId);
    const leftPane = document.getElementById(leftId);
    const rightPane = document.getElementById(rightId);

    if (!container || !resizer || !leftPane || !rightPane) return;

    let isDragging = false;

    resizer.addEventListener('mousedown', (e) => {
        isDragging = true;
        resizer.classList.add('is-dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDragging) return;

        const containerRect = container.getBoundingClientRect();
        const offsetX = e.clientX - containerRect.left;
        const totalWidth = containerRect.width;

        let percentage = (offsetX / totalWidth) * 100;
        // Giới hạn tỷ lệ trong khoảng 20% đến 80%
        if (percentage < 20) percentage = 20;
        if (percentage > 80) percentage = 80;

        leftPane.style.width = `${percentage}%`;
        leftPane.style.flex = 'none';
        rightPane.style.flex = '1';
    });

    document.addEventListener('mouseup', () => {
        if (isDragging) {
            isDragging = false;
            resizer.classList.remove('is-dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });

    // Reset về 50% khi double click vào resizer
    resizer.addEventListener('dblclick', () => {
        leftPane.style.width = '48%';
        leftPane.style.flex = 'none';
        rightPane.style.flex = '1';
    });
}

// =============================================================================
// UI MODULE: BỘ CÔNG CỤ TƯƠNG TÁC ẢNH HÓA ĐƠN (ZOOM, PAN, ROTATE, FIT, FULLSCREEN)
// =============================================================================

const imageViewerStates = {
    verify: { zoom: 1.0, rotate: 0, panX: 0, panY: 0, isPanning: false, startX: 0, startY: 0 },
    manual: { zoom: 1.0, rotate: 0, panX: 0, panY: 0, isPanning: false, startX: 0, startY: 0 }
};

function getImageElements(mode = 'verify') {
    if (mode === 'manual') {
        return {
            img: document.getElementById('manualImagePreview'),
            viewport: document.getElementById('manualImageViewport'),
            indicator: document.getElementById('manualZoomLevelIndicator')
        };
    }
    return {
        img: document.getElementById('receiptPreview'),
        viewport: document.getElementById('imageViewport'),
        indicator: document.getElementById('zoomLevelIndicator')
    };
}

function applyImageTransform(mode = 'verify') {
    const { img, indicator } = getImageElements(mode);
    const state = imageViewerStates[mode] || imageViewerStates.verify;

    if (!img) return;

    img.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.zoom}) rotate(${state.rotate}deg)`;

    if (indicator) {
        indicator.textContent = `${Math.round(state.zoom * 100)}%`;
    }
}

function zoomImage(delta, mode = 'verify') {
    const state = imageViewerStates[mode] || imageViewerStates.verify;
    let newZoom = Math.round((state.zoom + delta) * 100) / 100;
    if (newZoom < 0.3) newZoom = 0.3;
    if (newZoom > 4.0) newZoom = 4.0;
    state.zoom = newZoom;
    applyImageTransform(mode);
}

function rotateImage(mode = 'verify') {
    const state = imageViewerStates[mode] || imageViewerStates.verify;
    state.rotate = (state.rotate + 90) % 360;
    applyImageTransform(mode);
}

function fitImageWidth(mode = 'verify') {
    const state = imageViewerStates[mode] || imageViewerStates.verify;
    state.zoom = 1.35;
    state.panX = 0;
    state.panY = 0;
    applyImageTransform(mode);
}

function resetImageTransform(mode = 'verify') {
    const state = imageViewerStates[mode] || imageViewerStates.verify;
    state.zoom = 1.0;
    state.rotate = 0;
    state.panX = 0;
    state.panY = 0;
    applyImageTransform(mode);
}

function toggleFullscreenPreview(mode = 'verify') {
    const { viewport, img } = getImageElements(mode);
    if (!viewport) return;

    if (!document.fullscreenElement) {
        viewport.requestFullscreen().catch(err => {
            console.warn('Lỗi toàn màn hình:', err);
        });
    } else {
        document.exitFullscreen().catch(err => {
            console.warn('Lỗi thoát toàn màn hình:', err);
        });
    }
}

function initImageViewportDrag() {
    ['verify', 'manual'].forEach(mode => {
        const { viewport, img } = getImageElements(mode);
        if (!viewport || !img) return;

        const state = imageViewerStates[mode];

        viewport.addEventListener('mousedown', (e) => {
            // Không kích hoạt pan nếu bấm vào button toolbar hoặc ảnh đang ẩn
            if (e.target.closest('.image-toolbar') || img.classList.contains('d-none')) return;
            state.isPanning = true;
            state.startX = e.clientX - state.panX;
            state.startY = e.clientY - state.panY;
            viewport.classList.add('is-panning');
            e.preventDefault();
        });

        document.addEventListener('mousemove', (e) => {
            if (!state.isPanning) return;
            state.panX = e.clientX - state.startX;
            state.panY = e.clientY - state.startY;
            applyImageTransform(mode);
        });

        document.addEventListener('mouseup', () => {
            if (state.isPanning) {
                state.isPanning = false;
                viewport.classList.remove('is-panning');
            }
        });

        // Hỗ trợ lăn chuột để Phóng to / Thu nhỏ
        viewport.addEventListener('wheel', (e) => {
            if (img.classList.contains('d-none')) return;
            e.preventDefault();
            const delta = e.deltaY < 0 ? 0.15 : -0.15;
            zoomImage(delta, mode);
        }, { passive: false });
    });
}

// =============================================================================
// UI MODULE: TÍNH NĂNG CO GIÃN ĐỘ RỘNG CỘT BẢNG NHƯ EXCEL (EXCEL COLUMN RESIZER)
// =============================================================================

function initTableColumnResizers() {
    document.querySelectorAll('.table-excel').forEach(table => {
        const headers = table.querySelectorAll('th');
        
        headers.forEach(th => {
            // Kiểm tra và tạo col-resizer nếu chưa có (ngoại trừ cột cuối Xóa)
            let resizer = th.querySelector('.col-resizer');
            if (!resizer && !th.textContent.trim().includes('Xóa')) {
                resizer = document.createElement('div');
                resizer.className = 'col-resizer';
                th.appendChild(resizer);
            }

            if (!resizer) return;

            resizer.addEventListener('mousedown', (e) => {
                const startX = e.pageX;
                const startWidth = th.offsetWidth;
                resizer.classList.add('is-resizing');
                document.body.style.cursor = 'col-resize';
                document.body.style.userSelect = 'none';

                const onMouseMove = (moveEvent) => {
                    const diffX = moveEvent.pageX - startX;
                    const newWidth = Math.max(35, startWidth + diffX);
                    th.style.width = `${newWidth}px`;
                    th.style.minWidth = `${newWidth}px`;
                };

                const onMouseUp = () => {
                    resizer.classList.remove('is-resizing');
                    document.body.style.cursor = '';
                    document.body.style.userSelect = '';
                    document.removeEventListener('mousemove', onMouseMove);
                    document.removeEventListener('mouseup', onMouseUp);
                };

                document.addEventListener('mousemove', onMouseMove);
                document.addEventListener('mouseup', onMouseUp);
                e.stopPropagation();
                e.preventDefault();
            });
        });
    });
}

// =============================================================================
// KHỞI TẠO TOÀN DIỆN KHI TẢI TRANG (DOM READY)
// =============================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Khởi tạo dòng đầu tiên cho bảng manualLinesTable nếu ở manual.html
    const manualTbody = document.getElementById('manualLinesTableBody');
    if (manualTbody && manualTbody.children.length === 0) {
        addManualLineItemRow();
    }

    // Khởi tạo tính năng chia khung, điều khiển ảnh và co giãn cột Excel
    initSplitResizers();
    initImageViewportDrag();
    initTableColumnResizers();
});

