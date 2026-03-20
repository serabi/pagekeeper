/* ═══════════════════════════════════════════
   PageKeeper — BookFusion page
   ═══════════════════════════════════════════
   Depends on: utils.js
   No Jinja2 vars — clean extraction.
   ═══════════════════════════════════════════ */

/* ── Helpers ── */

function getSpinnerHtml() {
    return '<svg class="btn-spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 14px; height: 14px; animation: spin 1s linear infinite; margin-right: 6px; display: inline-block; vertical-align: middle;"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>';
}

function isMobileViewport() {
    return window.matchMedia('(max-width: 768px)').matches;
}

function keepElementVisible(el, block) {
    if (!el || !isMobileViewport()) return;
    window.setTimeout(function () {
        try {
            el.scrollIntoView({ behavior: 'smooth', block: block || 'center', inline: 'nearest' });
        } catch (e) {
            el.scrollIntoView();
        }
    }, 180);
}

function revealFirstMobileResult(listId) {
    if (!isMobileViewport()) return;
    var active = document.activeElement;
    if (!active || (active.id !== 'bf-search-input' && active.id !== 'bf-library-search')) return;
    var first = document.querySelector('#' + listId + ' .bf-book-item, #' + listId + ' .bf-highlight-group, #' + listId + ' .bf-empty');
    if (first) keepElementVisible(first, 'nearest');
}

function scrollActiveTabIntoView(tab) {
    var activeTab = document.querySelector('.bf-tab[data-tab="' + tab + '"]');
    if (!activeTab || !isMobileViewport()) return;
    activeTab.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
}

/*
 * All user-facing text in dynamically generated HTML is passed through
 * escapeHtml() (from utils.js) before insertion.
 */

function createComboboxHtml(options, placeholder, onChangeFnName, extraAttrs) {
    extraAttrs = extraAttrs || '';
    var optionsHtml = options.map(function (opt) {
        return '<div class="bf-combobox-option" data-value="' + escapeHtml(opt.value) + '" onclick="handleComboboxSelect(this)">' + escapeHtml(opt.label) + '</div>';
    }).join('');

    var selectedOpt = options.find(function (o) { return o.selected; });
    var initialValue = selectedOpt ? escapeHtml(selectedOpt.label) : '';
    var initialDataValue = selectedOpt ? escapeHtml(selectedOpt.value) : '';

    return '<div class="bf-combobox" data-on-change="' + onChangeFnName + '" ' + extraAttrs + '>' +
        '<input type="text" class="bf-combobox-input input-inline" placeholder="' + escapeHtml(placeholder) + '"' +
        ' value="' + initialValue + '" data-selected-value="' + initialDataValue + '"' +
        ' onfocus="this.parentElement.classList.add(\'open\')"' +
        ' onblur="setTimeout(function() { this.parentElement.classList.remove(\'open\') }.bind(this), 200)"' +
        ' oninput="handleComboboxFilter(this)">' +
        '<div class="bf-combobox-dropdown">' + optionsHtml + '</div>' +
    '</div>';
}

function handleComboboxFilter(input) {
    var filter = input.value.toLowerCase();
    var options = input.nextElementSibling.querySelectorAll('.bf-combobox-option');
    options.forEach(function (opt) {
        if (opt.textContent.toLowerCase().indexOf(filter) !== -1) {
            opt.classList.remove('hidden');
        } else {
            opt.classList.add('hidden');
        }
    });
    input.dataset.selectedValue = '';
}

function handleComboboxSelect(optionEl) {
    var combobox = optionEl.closest('.bf-combobox');
    var input = combobox.querySelector('.bf-combobox-input');
    input.value = optionEl.textContent;
    input.dataset.selectedValue = optionEl.dataset.value;
    combobox.classList.remove('open');

    if (combobox.dataset.onChange) {
        window[combobox.dataset.onChange](combobox);
    }
}

var _newHighlightIds = [];

/* ── Tab switching ── */
function switchBFTab(tab) {
    _newHighlightIds = [];
    document.querySelectorAll('.bf-tab').forEach(function (t) { t.classList.remove('active'); });
    document.querySelectorAll('.bf-panel').forEach(function (p) { p.classList.remove('active'); });
    document.getElementById('bf-panel-' + tab).classList.add('active');
    document.querySelectorAll('.bf-tab').forEach(function (t) {
        if (t.dataset.tab === tab) t.classList.add('active');
    });
    scrollActiveTabIntoView(tab);

    if (tab === 'highlights') loadHighlights();
    if (tab === 'library') loadLibrary();
}

/* ── Upload Tab ── */
var searchTimer;

function debounceSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(searchBooks, 300);
}

function searchBooks() {
    var q = document.getElementById('bf-search-input').value.trim();
    var list = document.getElementById('bf-book-list');
    if (!q) {
        list.textContent = '';
        var emptyEl = document.createElement('div');
        emptyEl.className = 'bf-empty';
        var h = document.createElement('div'); h.className = 'bf-empty-heading'; h.textContent = 'Search your Booklore library';
        var d = document.createElement('div'); d.className = 'bf-empty-desc'; d.textContent = 'Type above to find books to upload';
        emptyEl.appendChild(h); emptyEl.appendChild(d);
        list.appendChild(emptyEl);
        return;
    }
    fetch('/api/bookfusion/booklore-books?q=' + encodeURIComponent(q))
        .then(function (r) {
            if (!r.ok) throw new Error('Search failed');
            return r.json();
        })
        .then(function (books) {
            list.textContent = '';
            if (!books.length) {
                var emptyEl = document.createElement('div');
                emptyEl.className = 'bf-empty';
                var icon = document.createElement('div'); icon.className = 'bf-empty-icon'; icon.textContent = '\uD83D\uDCDA';
                var h = document.createElement('div'); h.className = 'bf-empty-heading'; h.textContent = 'No books found';
                var d = document.createElement('div'); d.className = 'bf-empty-desc'; d.textContent = 'Try a different search term';
                emptyEl.appendChild(icon); emptyEl.appendChild(h); emptyEl.appendChild(d);
                list.appendChild(emptyEl);
                return;
            }
            books.forEach(function (b) {
                var item = document.createElement('div');
                item.className = 'bf-book-item';

                var info = document.createElement('div');
                info.className = 'bf-book-info';

                var titleEl = document.createElement('div');
                titleEl.className = 'bf-book-title';
                titleEl.textContent = b.title || b.fileName;

                var metaEl = document.createElement('div');
                metaEl.className = 'bf-book-meta';
                var metaText = '';
                if (b.authors) metaText += b.authors + ' \u00B7 ';
                metaText += b.fileName;
                metaEl.textContent = metaText;
                var sourceTag = document.createElement('span');
                sourceTag.className = 'bf-source-tag';
                sourceTag.textContent = b.source;
                metaEl.appendChild(document.createTextNode(' '));
                metaEl.appendChild(sourceTag);

                info.appendChild(titleEl);
                info.appendChild(metaEl);

                var btn = document.createElement('button');
                btn.className = 'bf-upload-btn';
                btn.textContent = 'Upload';
                btn.dataset.bookId = b.id;
                btn.dataset.title = b.title || '';
                btn.dataset.authors = b.authors || '';
                btn.dataset.fileName = b.fileName || '';
                btn.addEventListener('click', function () { handleUploadClick(btn); });

                item.appendChild(info);
                item.appendChild(btn);
                list.appendChild(item);
            });
            revealFirstMobileResult('bf-book-list');
        })
        .catch(function (err) {
            list.textContent = '';
            var emptyEl = document.createElement('div');
            emptyEl.className = 'bf-empty';
            var icon = document.createElement('div'); icon.className = 'bf-empty-icon'; icon.textContent = '\u26A0\uFE0F';
            var h = document.createElement('div'); h.className = 'bf-empty-heading'; h.textContent = 'Search failed';
            var d = document.createElement('div'); d.className = 'bf-empty-desc'; d.textContent = 'Please try again';
            emptyEl.appendChild(icon); emptyEl.appendChild(h); emptyEl.appendChild(d);
            list.appendChild(emptyEl);
        });
}

function handleUploadClick(btn) {
    var book = {
        id: btn.dataset.bookId,
        title: btn.dataset.title,
        authors: btn.dataset.authors,
        fileName: btn.dataset.fileName
    };
    uploadBook(btn, book);
}

function uploadBook(btn, book) {
    btn.disabled = true;
    btn.innerHTML = getSpinnerHtml() + 'Uploading\u2026';

    fetch('/api/bookfusion/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            book_id: book.id,
            title: book.title,
            authors: book.authors,
            fileName: book.fileName
        })
    })
    .then(function (r) {
        if (!r.ok) throw new Error('Upload failed');
        return r.json();
    })
    .then(function (data) {
        if (data.success) {
            btn.textContent = 'Done';
            btn.classList.add('done');
        } else {
            btn.textContent = data.error || 'Error';
            btn.classList.add('error');
            btn.disabled = false;
        }
    })
    .catch(function (err) {
        btn.textContent = err.message || 'Upload failed';
        btn.classList.add('error');
        btn.disabled = false;
    });
}

/* ── Highlights Tab ── */
function syncHighlights(fullResync) {
    var btn = document.getElementById('bf-sync-btn');
    var resyncBtn = document.getElementById('bf-resync-btn');
    var info = document.getElementById('bf-sync-info');

    var activeBtn = fullResync ? resyncBtn : btn;
    var originalText = activeBtn.textContent;

    btn.disabled = true;
    resyncBtn.disabled = true;
    activeBtn.innerHTML = getSpinnerHtml() + (fullResync ? 'Re-syncing\u2026' : 'Syncing\u2026');
    info.textContent = '';

    fetch('/api/bookfusion/sync-highlights', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ full_resync: !!fullResync })
    })
        .then(function (r) {
            if (!r.ok) throw new Error('Sync failed');
            return r.json();
        })
        .then(function (data) {
            btn.disabled = false;
            resyncBtn.disabled = false;
            activeBtn.textContent = originalText;
            if (data.success) {
                var parts = [];
                if (data.new_highlights) parts.push(data.new_highlights + ' new highlight' + (data.new_highlights !== 1 ? 's' : ''));
                if (data.books_saved) parts.push(data.books_saved + ' book' + (data.books_saved !== 1 ? 's' : '') + ' cataloged');
                info.textContent = parts.length ? 'Synced: ' + parts.join(', ') : 'Up to date';
                _newHighlightIds = data.new_ids || [];
                loadHighlights();
            } else {
                info.textContent = data.error || 'Sync failed';
            }
        })
        .catch(function (err) {
            btn.disabled = false;
            resyncBtn.disabled = false;
            activeBtn.textContent = originalText;
            info.textContent = err.message || 'Sync failed';
        });
}

var _pkBooks = [];
var _currentHighlightGroups = {};

function loadHighlights() {
    fetch('/api/bookfusion/highlights')
        .then(function (r) {
            if (!r.ok) throw new Error('Failed to load highlights');
            return r.json();
        })
        .then(function (data) {
            var container = document.getElementById('bf-highlights-container');

            _pkBooks = data.books || [];
            var groups = data.highlights;
            _currentHighlightGroups = groups;
            var bookNames = Object.keys(groups);

            if (!bookNames.length) {
                container.textContent = '';
                var emptyEl = document.createElement('div');
                emptyEl.className = 'bf-empty';
                var icon = document.createElement('div'); icon.className = 'bf-empty-icon'; icon.textContent = '\uD83D\uDCDD';
                var h = document.createElement('div'); h.className = 'bf-empty-heading';
                h.textContent = data.has_synced ? 'No highlights found' : 'Sync your highlights';
                var d = document.createElement('div'); d.className = 'bf-empty-desc';
                d.textContent = data.has_synced ? 'Your synced books have no highlights yet' : 'Click "Sync Highlights" to fetch highlights from BookFusion';
                emptyEl.appendChild(icon); emptyEl.appendChild(h); emptyEl.appendChild(d);
                container.appendChild(emptyEl);
                return;
            }

            /* Highlight data is escaped via escapeHtml before insertion */
            container.innerHTML = bookNames.map(function (book) {  // eslint-disable-line no-unsanitized/property
                var groupData = groups[book];
                var hls = groupData.highlights;
                var matchedAbsId = groupData.matched_abs_id;

                var options = _pkBooks.map(function (b) {
                    return {
                        value: b.abs_id,
                        label: b.title,
                        selected: (matchedAbsId && b.abs_id === matchedAbsId)
                    };
                });

                var comboboxHtml = createComboboxHtml(options, 'Match to book\u2026', 'handleHighlightLinkChange', 'data-book-title="' + escapeHtml(book) + '" data-bf-id="' + escapeHtml(groupData.bookfusion_book_id) + '"');

                var hlsHtml = hls.map(function (h) {
                    var metaParts = [];
                    if (h.chapter_heading) metaParts.push(h.chapter_heading.replace(/^#{1,6}\s*/, ''));
                    if (h.date) metaParts.push(h.date);
                    var metaText = metaParts.map(escapeHtml).join(' &middot; ');
                    var isNew = _newHighlightIds.length && h.highlight_id && _newHighlightIds.indexOf(h.highlight_id) !== -1;
                    var newBadge = isNew ? '<span class="bf-new-badge">New</span>' : '';
                    var newAttr = isNew ? ' data-new-highlight="1"' : '';

                    return '<div class="bf-highlight"' + newAttr + '>' +
                        '<div class="bf-highlight-content">' + newBadge + escapeHtml(h.quote || '') + '</div>' +
                        '<div class="bf-highlight-chapter">' + metaText + '</div>' +
                    '</div>';
                }).join('');

                return '<div class="bf-highlight-group">' +
                    '<div class="bf-group-header" tabindex="0" role="button" onclick="toggleGroup(event, this)" onkeydown="if(event.key===\'Enter\'||event.key===\' \') { event.preventDefault(); toggleGroup(event, this); }">' +
                        '<span class="chevron">\u25BC</span>' +
                        '<span class="bf-group-title">' + escapeHtml(book) + '</span>' +
                        '<span class="bf-count">(' + hls.length + ')</span>' +
                        (matchedAbsId ? '<span class="bf-match-badge">\u2714 Linked</span>' : '') +
                        '<span class="bf-journal-controls" onclick="event.stopPropagation()">' +
                            comboboxHtml +
                            '<button class="bf-upload-btn" data-book-title="' + escapeHtml(book) + '" onclick="handleSaveJournalClick(this)">Save to Journal</button>' +
                        '</span>' +
                    '</div>' +
                    '<div class="bf-group-body">' + hlsHtml + '</div>' +
                '</div>';
            }).join('');
            revealFirstMobileResult('bf-highlights-container');

            if (_newHighlightIds.length) {
                var firstNew = container.querySelector('[data-new-highlight]');
                if (firstNew) {
                    var group = firstNew.closest('.bf-group-body');
                    if (group && group.classList.contains('hidden')) {
                        group.classList.remove('hidden');
                        var header = group.previousElementSibling;
                        if (header) header.classList.remove('collapsed');
                    }
                    setTimeout(function () {
                        firstNew.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }, 100);
                }
            }
        })
        .catch(function (err) {
            var container = document.getElementById('bf-highlights-container');
            container.textContent = '';
            var emptyEl = document.createElement('div');
            emptyEl.className = 'bf-empty';
            var icon = document.createElement('div'); icon.className = 'bf-empty-icon'; icon.textContent = '\u26A0\uFE0F';
            var h = document.createElement('div'); h.className = 'bf-empty-heading'; h.textContent = 'Failed to load highlights';
            var d = document.createElement('div'); d.className = 'bf-empty-desc'; d.textContent = 'Please try again';
            emptyEl.appendChild(icon); emptyEl.appendChild(h); emptyEl.appendChild(d);
            container.appendChild(emptyEl);
        });
}

function toggleGroup(e, headerEl) {
    if (e.target.closest('.bf-journal-controls')) return;
    headerEl.classList.toggle('collapsed');
    headerEl.nextElementSibling.classList.toggle('hidden');
}

function handleHighlightLinkChange(comboboxEl) {
    var input = comboboxEl.querySelector('.bf-combobox-input');
    var absId = input.dataset.selectedValue;
    var bookfusionBookId = comboboxEl.dataset.bfId;
    linkHighlight(bookfusionBookId, absId);
}

function handleSaveJournalClick(btn) {
    var bookTitle = btn.dataset.bookTitle;
    var groupData = _currentHighlightGroups[bookTitle];
    if (!groupData) return;
    var comboboxEl = btn.previousElementSibling;
    var input = comboboxEl.querySelector('.bf-combobox-input');
    saveToJournal(btn, input.dataset.selectedValue, groupData.highlights);
}

function saveToJournal(btn, absId, highlights) {
    if (!absId) {
        btn.textContent = 'Select a book first';
        btn.classList.add('error');
        setTimeout(function () {
            btn.textContent = 'Save to Journal';
            btn.classList.remove('error');
        }, 2000);
        return;
    }
    btn.disabled = true;
    btn.innerHTML = getSpinnerHtml() + 'Saving\u2026';

    var payload = highlights.map(function (h) {
        return {
            quote: h.quote || '',
            chapter: (h.chapter_heading || '').replace(/^#{1,6}\s*/, ''),
            highlighted_at: h.date || ''
        };
    });

    fetch('/api/bookfusion/save-journal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ abs_id: absId, highlights: payload })
    })
    .then(function (r) {
        if (!r.ok) throw new Error('Save failed');
        return r.json();
    })
    .then(function (data) {
        if (data.success) {
            btn.textContent = '\u2714 Saved ' + data.saved;
            btn.classList.add('done');
        } else {
            btn.textContent = data.error || 'Error';
            btn.classList.add('error');
            btn.disabled = false;
        }
    })
    .catch(function (err) {
        btn.textContent = err.message || 'Save failed';
        btn.classList.add('error');
        btn.disabled = false;
    });
}

function linkHighlight(bookfusionBookId, absId) {
    var inputs = document.querySelectorAll('.bf-combobox[data-book-title] .bf-combobox-input');
    inputs.forEach(function (s) { s.disabled = true; });
    fetch('/api/bookfusion/link-highlight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bookfusion_book_id: bookfusionBookId, abs_id: absId || null })
    })
    .then(function (r) {
        if (!r.ok) throw new Error('Link failed');
        return r.json();
    })
    .then(function (data) {
        inputs.forEach(function (s) { s.disabled = false; });
        if (data.success) {
            loadHighlights();
        } else {
            console.error('Link failed:', data.error || 'unknown error');
            loadHighlights();
        }
    })
    .catch(function (err) {
        inputs.forEach(function (s) { s.disabled = false; });
        console.error('Link request failed:', err);
        loadHighlights();
    });
}

/* ── Library Tab ── */
var _libraryData = [];
var _dashboardBooks = [];
var _currentRenderedBooks = [];

function loadLibrary() {
    var list = document.getElementById('bf-library-list');
    list.textContent = '';
    var emptyEl = document.createElement('div');
    emptyEl.className = 'bf-empty';
    var icon = document.createElement('div'); icon.className = 'bf-empty-icon'; icon.textContent = '\u23F3';
    var h = document.createElement('div'); h.className = 'bf-empty-heading'; h.textContent = 'Loading\u2026';
    emptyEl.appendChild(icon); emptyEl.appendChild(h);
    list.appendChild(emptyEl);

    fetch('/api/bookfusion/library')
        .then(function (r) {
            if (!r.ok) throw new Error('Failed to load library');
            return r.json();
        })
        .then(function (data) {
            _libraryData = data.books || [];
            _dashboardBooks = data.dashboard_books || [];
            var urlQ = new URLSearchParams(window.location.search).get('q');
            if (urlQ) {
                document.getElementById('bf-library-search').value = urlQ;
                filterLibrary();
            } else {
                renderLibrary(_libraryData);
            }
        })
        .catch(function (err) {
            list.textContent = '';
            var errEl = document.createElement('div');
            errEl.className = 'bf-empty';
            var icon = document.createElement('div'); icon.className = 'bf-empty-icon'; icon.textContent = '\u26A0\uFE0F';
            var h = document.createElement('div'); h.className = 'bf-empty-heading'; h.textContent = 'Failed to load library';
            var d = document.createElement('div'); d.className = 'bf-empty-desc'; d.textContent = 'Please try again';
            errEl.appendChild(icon); errEl.appendChild(h); errEl.appendChild(d);
            list.appendChild(errEl);
        });
}

function filterLibrary() {
    var q = document.getElementById('bf-library-search').value.trim().toLowerCase();
    if (!q) {
        renderLibrary(_libraryData);
        return;
    }
    var filtered = _libraryData.filter(function (b) {
        var searchable = (b.title || '') + ' ' + (b.authors || '') + ' ' + (b.series || '') + ' ' + (b.filenames || []).join(' ');
        return searchable.toLowerCase().indexOf(q) !== -1;
    });
    renderLibrary(filtered);
}

function _extractExt(filename) {
    var dot = filename.lastIndexOf('.');
    if (dot > 0) return filename.substring(dot + 1).toUpperCase();
    return '';
}

function _formatDateNote(data) {
    if (!data.dates_set) return null;
    var parts = [];
    if (data.started_at) parts.push(data.started_at);
    if (data.finished_at) parts.push(data.finished_at);
    if (!parts.length) return null;
    var range = parts.join(' to ');
    if (data.dates_source === 'hardcover') {
        return 'Reading dates set from Hardcover \u2014 ' + range;
    }
    return 'Dates estimated from highlights \u2014 ' + range;
}

/* Library item rendering — all user-facing text is escapeHtml-sanitized */
function _renderBookItem(b, i) {
    var metaHtml = '';
    if (b.authors) metaHtml += escapeHtml(b.authors);
    if (b.series) {
        if (b.authors) metaHtml += ' &middot; ';
        metaHtml += escapeHtml(b.series);
    }
    if (b.highlight_count > 0) {
        metaHtml += ' <span class="bf-hl-count">' + b.highlight_count + ' highlight' + (b.highlight_count !== 1 ? 's' : '') + '</span>';
    }

    var filenames = b.filenames || (b.filename ? [b.filename] : []);
    if (filenames.length) {
        metaHtml += ' ';
        var exts = [];
        filenames.forEach(function (fn) {
            var ext = _extractExt(fn);
            if (ext && ext !== 'MD' && exts.indexOf(ext) === -1) exts.push(ext);
        });
        exts.forEach(function (ext) {
            metaHtml += '<span class="bf-format-tag">' + escapeHtml(ext) + '</span>';
        });
    }

    var actionsHtml = '';
    if (b.on_dashboard) {
        actionsHtml = '<a class="bf-dashboard-badge" href="/reading/book/' + encodeURIComponent(b.abs_id) + '">\u2714 Matched</a>' +
            '<button class="bf-upload-btn bf-unlink-btn" onclick="handleUnlinkClick(this, ' + i + ')">Unlink</button>';
    } else {
        var options = _dashboardBooks.map(function (db) {
            return { value: db.abs_id, label: db.title, selected: false };
        });
        var comboboxHtml = createComboboxHtml(options, 'Match to book\u2026', '');
        actionsHtml = comboboxHtml +
            '<button class="bf-upload-btn bf-link-btn" onclick="handleLinkClick(this, ' + i + ')">Link</button>' +
            '<button class="bf-upload-btn" onclick="handleAddClick(this, ' + i + ', \'not_started\')">+ Library</button>' +
            '<button class="bf-upload-btn bf-start-btn" onclick="handleAddClick(this, ' + i + ', \'active\')">Start Reading</button>';
    }

    var hideBtn = b.hidden
        ? '<button class="bf-upload-btn bf-unhide-btn" onclick="handleUnhideClick(this, ' + i + ')">Unhide</button>'
        : '<button class="bf-upload-btn bf-hide-btn" onclick="handleHideClick(this, ' + i + ')">Hide</button>';

    return '<div class="bf-book-item">' +
        '<div class="bf-book-info">' +
            '<div class="bf-book-title">' + escapeHtml(b.title || b.filename) + '</div>' +
            '<div class="bf-book-meta">' + metaHtml + '</div>' +
        '</div>' +
        '<div class="bf-library-actions" data-index="' + i + '">' +
            actionsHtml +
            hideBtn +
        '</div>' +
    '</div>';
}

function renderLibrary(books) {
    var list = document.getElementById('bf-library-list');
    _currentRenderedBooks = books;

    var visible = books.filter(function (b) { return !b.hidden; });
    var hidden = books.filter(function (b) { return b.hidden; });

    if (!books.length) {
        list.textContent = '';
        var emptyEl = document.createElement('div');
        emptyEl.className = 'bf-empty';
        var heading = _libraryData.length ? 'No matches' : 'No books in catalog';
        var desc = _libraryData.length ? 'Try a different filter' : 'Run a Full Re-sync to populate your library';
        var icon = document.createElement('div'); icon.className = 'bf-empty-icon'; icon.textContent = '\uD83D\uDCDA';
        var h = document.createElement('div'); h.className = 'bf-empty-heading'; h.textContent = heading;
        var d = document.createElement('div'); d.className = 'bf-empty-desc'; d.textContent = desc;
        emptyEl.appendChild(icon); emptyEl.appendChild(h); emptyEl.appendChild(d);
        list.appendChild(emptyEl);
        return;
    }

    /* Library book data is escaped via escapeHtml before HTML insertion */
    var html = '';

    if (visible.length) {
        html += visible.map(function (b) { return _renderBookItem(b, books.indexOf(b)); }).join('');
    } else if (hidden.length) {
        html += '<div class="bf-empty"><div class="bf-empty-icon">\uD83D\uDCDA</div><div class="bf-empty-heading">All books are hidden</div><div class="bf-empty-desc">Expand the hidden section below to manage them</div></div>';
    }

    if (hidden.length) {
        html += '<div class="bf-hidden-section">' +
            '<div class="bf-hidden-header" tabindex="0" role="button" onclick="toggleHiddenSection(this)" onkeydown="if(event.key===\'Enter\'||event.key===\' \') { event.preventDefault(); toggleHiddenSection(this); }">' +
                '<span class="chevron">\u25BC</span>' +
                'Hidden' +
                '<span class="bf-count">(' + hidden.length + ')</span>' +
            '</div>' +
            '<div class="bf-hidden-body hidden">' +
                hidden.map(function (b) { return _renderBookItem(b, books.indexOf(b)); }).join('') +
            '</div>' +
        '</div>';
    }

    list.innerHTML = html;  // eslint-disable-line no-unsanitized/property
    revealFirstMobileResult('bf-library-list');
}

/* toggleHiddenSection — provided by utils.js */

function handleHideClick(btn, index) {
    var book = _currentRenderedBooks[index];
    btn.disabled = true;
    btn.innerHTML = getSpinnerHtml() + 'Hiding\u2026';
    fetch('/api/bookfusion/hide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bookfusion_ids: book.bookfusion_ids || [book.bookfusion_id], hidden: true })
    })
    .then(function (r) {
        if (!r.ok) throw new Error('Hide failed');
        return r.json();
    })
    .then(function (data) {
        if (data.success) {
            book.hidden = true;
            renderLibrary(_currentRenderedBooks);
        } else {
            btn.textContent = 'Hide';
            btn.disabled = false;
        }
    })
    .catch(function (err) {
        btn.textContent = 'Hide';
        btn.disabled = false;
    });
}

function handleUnhideClick(btn, index) {
    var book = _currentRenderedBooks[index];
    btn.disabled = true;
    btn.innerHTML = getSpinnerHtml() + 'Unhiding\u2026';
    fetch('/api/bookfusion/hide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bookfusion_ids: book.bookfusion_ids || [book.bookfusion_id], hidden: false })
    })
    .then(function (r) {
        if (!r.ok) throw new Error('Unhide failed');
        return r.json();
    })
    .then(function (data) {
        if (data.success) {
            book.hidden = false;
            renderLibrary(_currentRenderedBooks);
        } else {
            btn.textContent = 'Unhide';
            btn.disabled = false;
        }
    })
    .catch(function (err) {
        btn.textContent = 'Unhide';
        btn.disabled = false;
    });
}

function handleUnlinkClick(btn, index) {
    var book = _currentRenderedBooks[index];
    btn.disabled = true;
    btn.innerHTML = getSpinnerHtml() + 'Unlinking\u2026';
    fetch('/api/bookfusion/unlink', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ abs_id: book.abs_id })
    })
    .then(function (r) {
        if (!r.ok) throw new Error('Unlink failed');
        return r.json();
    })
    .then(function (data) {
        if (data.success) {
            book.on_dashboard = false;
            book.abs_id = null;
            renderLibrary(_currentRenderedBooks);
        } else {
            btn.textContent = 'Unlink';
            btn.disabled = false;
        }
    })
    .catch(function (err) {
        btn.textContent = 'Unlink';
        btn.disabled = false;
    });
}

function handleLinkClick(btn, index) {
    var book = _currentRenderedBooks[index];
    var comboboxEl = btn.previousElementSibling;
    var input = comboboxEl.querySelector('.bf-combobox-input');
    if (input.dataset.selectedValue) {
        matchToBook(btn, input, book, index);
    } else {
        btn.classList.add('error');
        btn.textContent = 'Select a book first';
        setTimeout(function () {
            btn.classList.remove('error');
            btn.textContent = 'Link';
        }, 2000);
    }
}

function handleAddClick(btn, index, status) {
    var book = _currentRenderedBooks[index];
    addToDashboard(btn, book, index, status);
}

function _showDateNote(actionsEl, data) {
    var note = _formatDateNote(data);
    if (!note) return;
    var noteEl = document.createElement('div');
    noteEl.className = 'bf-date-note';
    noteEl.textContent = note;
    noteEl.style.cssText = 'font-size: 0.75rem; color: var(--color-text-muted); margin-top: 4px; opacity: 0; transition: opacity 0.4s; width: 100%;';
    actionsEl.appendChild(noteEl);
    requestAnimationFrame(function () { noteEl.style.opacity = '1'; });
}

function matchToBook(btn, input, book, index) {
    var absId = input.dataset.selectedValue;
    btn.disabled = true;
    input.disabled = true;
    btn.innerHTML = getSpinnerHtml() + 'Linking\u2026';

    fetch('/api/bookfusion/match-to-book', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bookfusion_ids: book.bookfusion_ids || [book.bookfusion_id], abs_id: absId })
    })
    .then(function (r) {
        if (!r.ok) throw new Error('Match failed');
        return r.json();
    })
    .then(function (data) {
        if (data.success) {
            book.on_dashboard = true;
            book.abs_id = absId;
            renderLibrary(_currentRenderedBooks);
            setTimeout(function () {
                var actionsEls = document.querySelectorAll('.bf-library-actions');
                var actionsEl = Array.from(actionsEls).find(function (el) { return el.dataset.index == index; });
                if (actionsEl) _showDateNote(actionsEl, data);
            }, 50);
        } else {
            btn.textContent = 'Link';
            btn.disabled = false;
            input.disabled = false;
        }
    })
    .catch(function (err) {
        btn.textContent = 'Link';
        btn.disabled = false;
        input.disabled = false;
    });
}

function addToDashboard(btn, book, index, status) {
    btn.disabled = true;
    btn.textContent = 'Adding\u2026';

    var payload = { bookfusion_ids: book.bookfusion_ids || [book.bookfusion_id] };
    if (status) payload.status = status;

    fetch('/api/bookfusion/add-to-dashboard', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(function (r) {
        if (!r.ok) throw new Error('Add failed');
        return r.json();
    })
    .then(function (data) {
        if (data.success) {
            book.on_dashboard = true;
            book.abs_id = data.abs_id;
            renderLibrary(_currentRenderedBooks);
            setTimeout(function () {
                var actionsEls = document.querySelectorAll('.bf-library-actions');
                var actionsEl = Array.from(actionsEls).find(function (el) { return el.dataset.index == index; });
                if (actionsEl) _showDateNote(actionsEl, data);
            }, 50);
        } else {
            btn.textContent = data.error || 'Error';
            btn.classList.add('error');
            btn.disabled = false;
        }
    })
    .catch(function (err) {
        btn.textContent = err.message || 'Error';
        btn.classList.add('error');
        btn.disabled = false;
    });
}

/* ── Init ── */
document.addEventListener('focusin', function (e) {
    if (e.target.matches('#bf-search-input, #bf-library-search, .bf-combobox-input')) {
        keepElementVisible(e.target, 'center');
    }
});

window.addEventListener('resize', function () {
    var active = document.activeElement;
    if (active && active.matches && active.matches('#bf-search-input, #bf-library-search, .bf-combobox-input')) {
        keepElementVisible(active, 'center');
    }
});

var urlTab = new URLSearchParams(window.location.search).get('tab');
if (urlTab && document.getElementById('bf-panel-' + urlTab)) {
    switchBFTab(urlTab);
} else {
    loadLibrary();
}
