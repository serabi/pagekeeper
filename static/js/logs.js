/* ═══════════════════════════════════════════
   PageKeeper — logs page
   ═══════════════════════════════════════════
   Depends on: utils.js (escapeHtml, debounce)
   No Jinja2 vars — clean extraction.
   ═══════════════════════════════════════════ */

let autoRefreshInterval = null;
let liveRefreshInterval = null;
let currentOffset = 0;
let shownLogs = new Set();

// DOM elements
const logContent = document.getElementById('logContent');
const logLevel = document.getElementById('logLevel');
const searchInput = document.getElementById('searchInput');
const linesCount = document.getElementById('linesCount');
const autoRefresh = document.getElementById('autoRefresh');
const liveMode = document.getElementById('liveMode');
const refreshBtn = document.getElementById('refreshBtn');
const totalLinesStats = document.getElementById('totalLinesStats');
const displayedLinesStats = document.getElementById('displayedLinesStats');
const lastUpdated = document.getElementById('lastUpdated');
const scrollToTop = document.getElementById('scrollToTop');
const scrollToBottom = document.getElementById('scrollToBottom');
const loadMore = document.getElementById('loadMore');

let isAtBottom = true;
let userScrolled = false;
let filterPending = false;

function showNoLogsMessage() {
    logContent.textContent = '';
    var noLogsLine = document.createElement('div');
    noLogsLine.className = 'log-line';
    var noLogsLevel = document.createElement('span');
    noLogsLevel.className = 'log-level INFO';
    noLogsLevel.textContent = 'INFO';
    var noLogsMsg = document.createElement('span');
    noLogsMsg.className = 'log-message';
    noLogsMsg.textContent = 'No logs found matching current filters';
    noLogsLine.appendChild(noLogsLevel);
    noLogsLine.appendChild(noLogsMsg);
    logContent.appendChild(noLogsLine);
}

logContent.addEventListener('scroll', () => {
    const isCurrentlyAtBottom = logContent.scrollTop + logContent.clientHeight >= logContent.scrollHeight - 10;
    if (!isCurrentlyAtBottom && !userScrolled) {
        userScrolled = true;
    }
    isAtBottom = isCurrentlyAtBottom;
});

async function fetchLogs(offset = 0, append = false) {
    try {
        const params = new URLSearchParams({
            level: logLevel.value,
            search: searchInput.value,
            lines: linesCount.value,
            offset: offset
        });

        const response = await fetch(`/api/logs?${params}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || 'Failed to fetch logs');
        }

        displayLogs(data, append);
        updateStats(data);
        updateLastUpdated();

        return data;
    } catch (error) {
        console.error('Error fetching logs:', error);
        logContent.textContent = '';
        const line = document.createElement('div');
        line.className = 'log-line';
        const level = document.createElement('span');
        level.className = 'log-level ERROR';
        level.textContent = 'ERROR';
        const msg = document.createElement('span');
        msg.className = 'log-message';
        msg.textContent = `Failed to fetch logs: ${error.message}`;
        line.appendChild(level);
        line.appendChild(msg);
        logContent.appendChild(line);
    }
}

async function fetchLiveLogs() {
    try {
        const params = new URLSearchParams({
            level: logLevel.value,
            search: searchInput.value,
            count: 50
        });

        const response = await fetch(`/api/logs/live?${params}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || 'Failed to fetch live logs');
        }

        if (data.logs && data.logs.length > 0) {
            appendNewLogs(data.logs, filterPending);
            updateLastUpdated();
        } else if (filterPending) {
            filterPending = false;
            showNoLogsMessage();
        }

        return data;
    } catch (error) {
        console.error('Error fetching live logs:', error);
        filterPending = false;
        var errDiv = document.createElement('div');
        errDiv.className = 'log-line';
        var errLevel = document.createElement('span');
        errLevel.className = 'log-level ERROR';
        errLevel.textContent = 'ERROR';
        var errMsg = document.createElement('span');
        errMsg.className = 'log-message';
        errMsg.textContent = 'Failed to fetch live logs: ' + (error.message || error);
        errDiv.appendChild(errLevel);
        errDiv.appendChild(errMsg);
        logContent.appendChild(errDiv);
    }
}

function appendNewLogs(logs, forceShow = false) {
    if (!logs || logs.length === 0) {
        if (filterPending) {
            filterPending = false;
            showNoLogsMessage();
        }
        return;
    }

    const scrollToBottomAfter = isAtBottom;

    if (filterPending) {
        filterPending = false;
        logContent.textContent = '';
        shownLogs.clear();
        forceShow = true;
    }

    logs.forEach(log => {
        const logId = `${log.timestamp}|${log.message}`;

        if (forceShow || !shownLogs.has(logId)) {
            const logLine = document.createElement('div');
            logLine.className = 'log-line';

            var ts = document.createElement('span');
            ts.className = 'log-timestamp';
            ts.textContent = log.timestamp;
            logLine.appendChild(ts);

            var lvl = document.createElement('span');
            lvl.className = 'log-level ' + log.level;
            lvl.textContent = log.level;
            logLine.appendChild(lvl);

            var mod = document.createElement('span');
            mod.className = 'log-module';
            mod.textContent = log.module || 'unknown';
            logLine.appendChild(mod);

            var msgEl = document.createElement('span');
            msgEl.className = 'log-message';
            msgEl.textContent = log.message;
            logLine.appendChild(msgEl);

            if (!forceShow) {
                logLine.style.background = 'rgba(124, 58, 237, 0.1)';
                setTimeout(() => {
                    logLine.style.background = '';
                }, 2000);
            }

            logContent.appendChild(logLine);
            shownLogs.add(logId);
        }
    });

    if (scrollToBottomAfter) {
        setTimeout(() => {
            logContent.scrollTop = logContent.scrollHeight;
        }, 10);
    }

    const currentLines = logContent.children.length;
    displayedLinesStats.textContent = `Showing: ${currentLines} lines (live mode)`;
}

function displayLogs(data, append = false) {
    const logs = data.logs || [];

    if (!append) {
        logContent.textContent = '';
        currentOffset = 0;
        shownLogs.clear();
        userScrolled = false;
        isAtBottom = true;
    }

    if (logs.length === 0) {
        if (!append) {
            showNoLogsMessage();
        }
        return;
    }

    logs.forEach(log => {
        const logLine = document.createElement('div');
        logLine.className = 'log-line';

        var ts = document.createElement('span');
        ts.className = 'log-timestamp';
        ts.textContent = log.timestamp;
        logLine.appendChild(ts);

        var lvl = document.createElement('span');
        lvl.className = 'log-level ' + log.level;
        lvl.textContent = log.level;
        logLine.appendChild(lvl);

        var mod = document.createElement('span');
        mod.className = 'log-module';
        mod.style.cssText = 'color: rgba(255,255,255,0.6); font-size: 11px; min-width: 80px;';
        mod.textContent = log.module || 'unknown';
        logLine.appendChild(mod);

        var msgEl = document.createElement('span');
        msgEl.className = 'log-message';
        msgEl.textContent = log.message;
        logLine.appendChild(msgEl);

        logContent.appendChild(logLine);

        const logId = `${log.timestamp}|${log.message}`;
        shownLogs.add(logId);
    });

    if (liveMode.checked) {
        loadMore.style.display = 'none';
    } else if (data.has_more) {
        loadMore.style.display = 'block';
        currentOffset = data.displayed_lines;
    } else {
        loadMore.style.display = 'none';
    }

    if (!append) {
        setTimeout(() => {
            logContent.scrollTop = logContent.scrollHeight;
        }, 10);
    }
}

function updateStats(data) {
    if (data.total_lines !== undefined) {
        totalLinesStats.textContent = `Total: ${data.total_lines || 0} lines`;
    }
    if (data.displayed_lines !== undefined) {
        displayedLinesStats.textContent = `Showing: ${data.displayed_lines || 0} lines`;
    }
}

function updateLastUpdated() {
    const now = new Date();
    lastUpdated.textContent = `Last updated: ${now.toLocaleTimeString()}`;
}

/* escapeHtml and debounce are provided by utils.js */

function toggleLiveMode() {
    if (liveMode.checked) {
        fetchLogs().then(() => {
            displayedLinesStats.textContent = `Showing: ${logContent.children.length} lines (live mode)`;
        });
        liveRefreshInterval = setInterval(fetchLiveLogs, 2000);
        loadMore.style.display = 'none';

        if (autoRefresh.checked) {
            autoRefresh.checked = false;
            if (autoRefreshInterval) {
                clearInterval(autoRefreshInterval);
                autoRefreshInterval = null;
            }
        }
    } else {
        if (liveRefreshInterval) {
            clearInterval(liveRefreshInterval);
            liveRefreshInterval = null;
        }
        fetchLogs();
    }
}

// Event listeners
refreshBtn.addEventListener('click', () => {
    if (liveMode.checked) {
        fetchLiveLogs();
    } else {
        fetchLogs();
    }
});

logLevel.addEventListener('change', () => {
    if (liveMode.checked) {
        logContent.textContent = '';
        filterPending = true;
        var loadingDiv = document.createElement('div');
        loadingDiv.className = 'loading';
        var spinner = document.createElement('div');
        spinner.className = 'spinner';
        loadingDiv.appendChild(spinner);
        loadingDiv.appendChild(document.createTextNode('Switching filter...'));
        logContent.appendChild(loadingDiv);
        shownLogs.clear();
        setTimeout(() => {
            fetchLiveLogs().then(() => {
                if (filterPending) {
                    filterPending = false;
                    showNoLogsMessage();
                }
            });
        }, 500);
    } else {
        fetchLogs();
    }
});

linesCount.addEventListener('change', () => {
    if (!liveMode.checked) {
        fetchLogs();
    }
});

searchInput.addEventListener('input', debounce(() => {
    if (liveMode.checked) {
        logContent.textContent = '';
        filterPending = true;
        var loadingDiv = document.createElement('div');
        loadingDiv.className = 'loading';
        var spinner = document.createElement('div');
        spinner.className = 'spinner';
        loadingDiv.appendChild(spinner);
        loadingDiv.appendChild(document.createTextNode('Applying filter...'));
        logContent.appendChild(loadingDiv);
        shownLogs.clear();
        setTimeout(() => {
            fetchLiveLogs().then(() => {
                if (filterPending) {
                    filterPending = false;
                    showNoLogsMessage();
                }
            });
        }, 500);
    } else {
        fetchLogs();
    }
}, 500));

autoRefresh.addEventListener('change', (e) => {
    if (e.target.checked) {
        if (liveMode.checked) {
            liveMode.checked = false;
            toggleLiveMode();
        }
        autoRefreshInterval = setInterval(() => fetchLogs(), 30000);
    } else {
        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }
    }
});

liveMode.addEventListener('change', toggleLiveMode);

scrollToTop.addEventListener('click', () => {
    logContent.scrollTop = 0;
    userScrolled = true;
    isAtBottom = false;
});

scrollToBottom.addEventListener('click', () => {
    logContent.scrollTop = logContent.scrollHeight;
    userScrolled = false;
    isAtBottom = true;
});

loadMore.addEventListener('click', async () => {
    if (!liveMode.checked) {
        await fetchLogs(currentOffset, true);
    }
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey) {
        switch (e.key) {
            case 'r':
                e.preventDefault();
                if (liveMode.checked) {
                    fetchLiveLogs();
                } else {
                    fetchLogs();
                }
                break;
            case 'f':
                e.preventDefault();
                searchInput.focus();
                break;
            case 'l':
                e.preventDefault();
                liveMode.checked = !liveMode.checked;
                toggleLiveMode();
                break;
        }
    }
});

// Initial load
logLevel.value = 'INFO';
liveMode.checked = true;
autoRefresh.checked = false;
linesCount.value = '1000';
searchInput.value = '';
toggleLiveMode();

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
    }
    if (liveRefreshInterval) {
        clearInterval(liveRefreshInterval);
    }
});

// ── Tab Switching ──
const mainTabs = Array.from(document.querySelectorAll('.r-main-tab-btn'));
const mainPanels = Array.from(document.querySelectorAll('.r-main-panel'));

function setMainTab(tabName) {
    mainTabs.forEach(tab => {
        const active = tab.dataset.mainTab === tabName;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    mainPanels.forEach(panel => {
        panel.hidden = panel.dataset.mainPanel !== tabName;
    });
    if (tabName === 'hardcover' && !hcLoaded) {
        fetchHardcoverLogs();
    }
}

mainTabs.forEach(tab => {
    tab.addEventListener('click', () => setMainTab(tab.dataset.mainTab));
});

// ── Hardcover Sync Logs ──
let hcPage = 1;
let hcTotalPages = 1;
let hcLoaded = false;
const hcLogBody = document.getElementById('hcLogBody');
const hcStatsLine = document.getElementById('hcStatsLine');
const hcPageInfo = document.getElementById('hcPageInfo');
const hcPrevBtn = document.getElementById('hcPrevBtn');
const hcNextBtn = document.getElementById('hcNextBtn');

const HC_STATUS_NAMES = {1: 'Want to Read', 2: 'Currently Reading', 3: 'Read', 4: 'Paused', 5: 'DNF'};
const HC_ACTION_LABELS = {
    status_update: 'Status Update',
    status_transition: 'Status Transition',
    status_pull: 'Status Pull',
    create_user_book: 'Create User Book',
    adopt_user_book: 'Adopt User Book',
    rating: 'Rating',
    date_pull: 'Date Pull',
    date_push: 'Date Push',
    automatch: 'Automatch',
    manual_match: 'Manual Match',
    journal_note: 'Journal Note',
};
const HC_PRIVACY_NAMES = {1: 'public', 2: 'followers', 3: 'private'};

function formatHcDetail(log) {
    if (log.error_message) return log.error_message;
    const d = log.detail;
    if (!d || typeof d !== 'object') return d ? String(d) : '';
    const action = log.action || '';

    if (action === 'journal_note') {
        const privacy = HC_PRIVACY_NAMES[d.privacy] || 'private';
        const preview = d.entry_preview || '';
        const source = d.source && d.source !== 'note' ? ` [${d.source}]` : '';
        return preview
            ? `\u201c${preview}\u201d (${privacy})${source}`
            : `Pushed note (${privacy})${source}`;
    }
    if (action === 'rating') {
        return d.rating != null ? `Set rating to ${d.rating}` : 'Cleared rating';
    }
    if (action === 'status_update') {
        const label = d.status_label || '';
        const hcId = d.hc_status_id;
        const hcName = HC_STATUS_NAMES[hcId] || '';
        return label ? `${label} \u2192 ${hcName || 'HC ' + hcId}` : (hcName || '');
    }
    if (action === 'status_transition') {
        const from = HC_STATUS_NAMES[d.from] || d.from;
        const to = HC_STATUS_NAMES[d.to] || d.to;
        return `${from} \u2192 ${to}`;
    }
    if (action === 'status_pull') {
        return `${d.old_status || '?'} \u2192 ${d.new_status || '?'} (HC status ${d.hc_status_id || '?'})`;
    }
    if (action === 'date_pull' || action === 'date_push') {
        const parts = [];
        if (d.started_at) parts.push(`started: ${d.started_at}`);
        if (d.finished_at) parts.push(`finished: ${d.finished_at}`);
        return parts.join(', ') || '';
    }
    if (action === 'automatch') {
        const by = d.matched_by || 'unknown';
        return `Matched by ${by}` + (d.slug ? ` (${d.slug})` : '');
    }
    if (action === 'manual_match') {
        return `Linked to ${d.slug || d.input || 'HC ' + (d.hardcover_book_id || '?')}`;
    }
    if (action === 'adopt_user_book') {
        const status = HC_STATUS_NAMES[d.status_id] || '';
        return status ? `Adopted existing (${status})` : 'Adopted existing';
    }
    if (action === 'create_user_book') {
        const status = HC_STATUS_NAMES[d.status_id] || '';
        return status ? `Created as ${status}` : 'Created';
    }
    return Object.entries(d)
        .filter(([, v]) => v != null)
        .map(([k, v]) => k.replace(/_/g, ' ') + ': ' + v)
        .join(', ');
}

function renderHcRow(log) {
    const tr = document.createElement('tr');

    const tdTs = document.createElement('td');
    tdTs.className = 'hc-ts';
    tdTs.textContent = log.created_at ? new Date(log.created_at).toLocaleString() : '';
    tr.appendChild(tdTs);

    const tdDir = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'hc-direction-badge hc-dir-' + (log.direction || 'push');
    badge.textContent = log.direction || '';
    tdDir.appendChild(badge);
    tr.appendChild(tdDir);

    const tdAction = document.createElement('td');
    tdAction.textContent = HC_ACTION_LABELS[log.action] || (log.action || '').replace(/_/g, ' ');
    tr.appendChild(tdAction);

    const tdTitle = document.createElement('td');
    tdTitle.className = 'hc-title';
    tdTitle.textContent = log.book_title || '\u2014';
    tr.appendChild(tdTitle);

    const tdDetail = document.createElement('td');
    tdDetail.className = 'hc-detail';
    tdDetail.textContent = formatHcDetail(log);
    tr.appendChild(tdDetail);

    const tdStatus = document.createElement('td');
    const icon = document.createElement('span');
    icon.className = 'hc-status-icon ' + (log.success ? 'success' : 'failure');
    icon.title = log.success ? 'Success' : 'Failed';
    icon.textContent = log.success ? '\u2713' : '\u2717';
    tdStatus.appendChild(icon);
    tr.appendChild(tdStatus);

    return tr;
}

async function fetchHardcoverLogs() {
    const params = new URLSearchParams({ page: hcPage, per_page: 50 });
    const dir = document.getElementById('hcDirection').value;
    const act = document.getElementById('hcAction').value;
    const search = document.getElementById('hcSearch').value;
    if (dir) params.set('direction', dir);
    if (act) params.set('action', act);
    if (search) params.set('search', search);

    try {
        const res = await fetch('/api/logs/hardcover?' + params);
        if (!res.ok) throw new Error('Server returned ' + res.status);
        const data = await res.json();
        hcLoaded = true;
        hcTotalPages = data.total_pages || 1;

        const showing = data.logs ? data.logs.length : 0;
        hcStatsLine.textContent = 'Showing ' + showing + ' of ' + (data.total || 0) + ' entries';
        hcPageInfo.textContent = 'Page ' + (data.page || 1) + ' of ' + hcTotalPages;
        hcPrevBtn.disabled = (data.page || 1) <= 1;
        hcNextBtn.disabled = (data.page || 1) >= hcTotalPages;

        hcLogBody.textContent = '';
        if (!data.logs || data.logs.length === 0) {
            const emptyRow = document.createElement('tr');
            const emptyTd = document.createElement('td');
            emptyTd.colSpan = 6;
            emptyTd.className = 'hc-empty';
            emptyTd.textContent = 'No sync events recorded yet.';
            emptyRow.appendChild(emptyTd);
            hcLogBody.appendChild(emptyRow);
            return;
        }

        data.logs.forEach(log => hcLogBody.appendChild(renderHcRow(log)));
    } catch (err) {
        hcLogBody.textContent = '';
        const errRow = document.createElement('tr');
        const errTd = document.createElement('td');
        errTd.colSpan = 6;
        errTd.className = 'hc-empty';
        errTd.textContent = 'Error loading logs: ' + err.message;
        errRow.appendChild(errTd);
        hcLogBody.appendChild(errRow);
    }
}

document.getElementById('hcRefreshBtn').addEventListener('click', () => { hcPage = 1; fetchHardcoverLogs(); });
document.getElementById('hcDirection').addEventListener('change', () => { hcPage = 1; fetchHardcoverLogs(); });
document.getElementById('hcAction').addEventListener('change', () => { hcPage = 1; fetchHardcoverLogs(); });
document.getElementById('hcSearch').addEventListener('input', debounce(() => { hcPage = 1; fetchHardcoverLogs(); }, 500));
hcPrevBtn.addEventListener('click', () => { if (hcPage > 1) { hcPage--; fetchHardcoverLogs(); } });
hcNextBtn.addEventListener('click', () => { if (hcPage < hcTotalPages) { hcPage++; fetchHardcoverLogs(); } });
