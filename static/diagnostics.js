/* Readable diagnostics retain original evidence; they never infer job success. */
(() => {
    'use strict';
    const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
    const control = id => document.getElementById(id);
    const filters = () => ({
        query: (control('log-search')?.value || '').trim().toLocaleLowerCase('vi'),
        level: (control('log-level')?.value || 'all').toLowerCase(),
        display: control('log-display')?.value || 'readable',
        live: control('log-live')?.checked !== false,
    });
    const levelOf = level => ({error: 'error', critical: 'error', fatal: 'error',
        warning: 'warning', warn: 'warning', info: 'info', debug: 'debug'}[level.toLowerCase()] || 'unknown');
    const levelLabel = {error: 'Lỗi', warning: 'Cảnh báo', info: 'Thông tin', debug: 'Gỡ lỗi', unknown: 'Nhật ký'};
    function parse(raw, category = '') {
        const result = [];
        const lines = String(raw ?? '').replace(/\r\n/g, '\n').split('\n');
        // Stack frames and provider JSON belong to their original event. A
        // matching word on a continuation line must retain the full traceback.
        for (const line of lines) {
            const match = line.match(/^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+\[([A-Za-z]+)\](?:\s+\[([^\]]+)\])?\s*:?\s*(.*)$/);
            if (match) {
                result.push({time: match[1], level: levelOf(match[2]), category: match[3] || category,
                    message: match[4], raw: line});
            } else if (result.length) {
                result[result.length - 1].raw += '\n' + line;
            } else if (line.trim()) {
                result.push({time: '', level: 'unknown', category, message: line, raw: line});
            }
        }
        return result;
    }
    function description(entry) {
        const text = entry.message;
        const sentence = text.match(/\bsegment_id=(\d+)\b/);
        // Segment IDs are identifiers, not guaranteed one-based row numbers.
        const suffix = sentence ? ` · Mã câu ${sentence[1]}` : '';
        const mappings = [
            [/PacingReviewRejected/, 'Bản rút gọn chưa vượt qua kiểm tra chất lượng'],
            [/SpeechBudgetError/, 'Lời đọc vượt thời lượng cho phép'],
            [/EdgeTTSRequestError/, 'Dịch vụ giọng đọc chưa trả được âm thanh'],
            [/TimeoutError|\bTIMEOUT\b|timed out|timeout/i, 'Yêu cầu xử lý quá thời gian chờ'],
            [/\bPACING_FAILED\b|\bRECOVERY_PACING_FAILED\b/, 'Chưa căn được giọng đọc vào thời lượng câu'],
            [/\bPACING_REJECTED\b/, 'Bản rút gọn chưa được chấp nhận'],
            [/\bPACING_REWRITE\b/, 'Đang viết lại lời đọc để vừa thời lượng'],
            [/\bPACING_MEASURED\b/, 'Đã đo thời lượng giọng đọc'],
            [/\bPACING_COMPACTED\b/, 'Đã điều chỉnh khoảng nghỉ trong giọng đọc'],
            [/\bPACING_BUDGET_EXTENDED\b/, 'Đã mở rộng khoảng đọc cho câu'],
            [/\bTTS_RAW_CACHE_HIT\b/, 'Dùng lại âm thanh đã tạo'],
            [/\bTRANSCRIPT_EDIT_FAILED\b/, 'Chưa áp dụng được chỉnh sửa lời thoại'],
            [/\bRECOVERY_SPEECH_FAILED\b|\bREVIEW_AUDIO_DEFERRED\b/, 'Giọng đọc của câu vẫn cần xử lý'],
            [/\bSEGMENT_FAILED_CONTINUING\b/, 'Một câu gặp lỗi; tác vụ tiếp tục xử lý các câu khác'],
            [/\bSOURCE_RETRY_PENDING\b/, 'Đoạn nguồn đang chờ thử lại'],
            [/\bRESUMED_REVIEW_FAILED\b|\bREVIEW_FAILED\b/, 'Chưa hoàn tất rà soát bản dịch'],
        ];
        for (const [pattern, title] of mappings) {
            if (pattern.test(text)) return title + suffix;
        }
        return text || 'Nội dung nhật ký';
    }
    function filtered(entries, current) {
        const selectedLevel = current.level === 'all' || current.level === '' ? null : levelOf(current.level);
        return entries.filter(entry => (!selectedLevel || entry.level === selectedLevel)
            && (!current.query || `${entry.raw}\n${description(entry)}`.toLocaleLowerCase('vi').includes(current.query)));
    }
    function render(raw, category = '') {
        const current = filters();
        const all = parse(raw, category);
        const visible = filtered(all, current);
        const counts = {error: 0, warning: 0, info: 0, debug: 0, unknown: 0};
        for (const entry of all) counts[entry.level]++;
        const summary = `<div class="diagnostics-summary" aria-label="Thống kê phần nhật ký đã tải">`
            + `<span class="diagnostics-count">Đang hiện <strong>${visible.length}/${all.length}</strong> sự kiện</span>`
            + ['error', 'warning', 'info'].map(level => `<span class="diagnostics-count level-${level}">${levelLabel[level]} <strong>${counts[level]}</strong></span>`).join('')
            + '</div>';
        if (!visible.length) return summary + `<p class="diagnostics-empty">${all.length ? 'Không có sự kiện khớp bộ lọc.' : 'Chưa có nhật ký trong phần đã tải.'}</p>`;
        if (current.display === 'raw') return summary + `<pre class="diagnostics-raw">${escape(visible.map(entry => entry.raw).join('\n'))}</pre>`;
        return summary + '<div class="diagnostics-entries">' + visible.map(entry =>
            `<details class="diagnostics-entry level-${entry.level}"><summary>`
            + `<span class="diagnostics-meta"><time>${escape(entry.time || 'Không có thời gian')}</time>`
            + `<span class="diagnostics-level">${levelLabel[entry.level]}</span>`
            + `<span class="diagnostics-category">${escape(entry.category)}</span></span>`
            + `<span class="diagnostics-title">${escape(description(entry))}</span>`
            + '<span class="diagnostics-expand">Chi tiết kỹ thuật</span></summary>'
            + `<pre class="diagnostics-raw">${escape(entry.raw)}</pre></details>`).join('') + '</div>';
    }
    window.studioDiagnostics = Object.freeze({
        render,
        filtersKey: () => JSON.stringify(filters()),
        filteredText: raw => filtered(parse(raw), filters()).map(entry => entry.raw).join('\n'),
    });
    function bind() {
        for (const id of ['log-search', 'log-level', 'log-display', 'log-live']) {
            const element = control(id);
            if (!element || element.dataset.diagnosticsBound) continue;
            element.dataset.diagnosticsBound = 'true';
            element.addEventListener(id === 'log-search' ? 'input' : 'change', () => {
                window.dispatchEvent(new CustomEvent('studio:log-filter'));
            });
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind, {once: true});
    else bind();
})();
