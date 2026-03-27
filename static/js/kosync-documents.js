/* PageKeeper — KoSync Document Management */
(function () {
    'use strict';

    var data = window.PK_PAGE_DATA;
    var documents = data.documents || [];
    var orphanedBooks = data.orphanedBooks || [];

    var STALE_DAYS = 30;

    // ── Toast ──

    function showToast(message) {
        var existing = document.querySelector('.r-tbr-toast');
        if (existing) existing.remove();
        var toast = document.createElement('div');
        toast.className = 'r-tbr-toast';
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(function () {
            toast.style.transition = 'opacity 0.3s';
            toast.style.opacity = '0';
            setTimeout(function () { toast.remove(); }, 300);
        }, 3000);
    }

    // ── Helpers ──

    function truncHash(hash) {
        return hash ? hash.substring(0, 12) + '\u2026' : '';
    }

    function timeAgo(isoStr) {
        if (!isoStr) return 'never';
        var diff = Date.now() - new Date(isoStr).getTime();
        var mins = Math.floor(diff / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return mins + 'm ago';
        var hours = Math.floor(mins / 60);
        if (hours < 24) return hours + 'h ago';
        var days = Math.floor(hours / 24);
        if (days < 30) return days + 'd ago';
        var months = Math.floor(days / 30);
        return months + 'mo ago';
    }

    function daysSince(isoStr) {
        if (!isoStr) return Infinity;
        return (Date.now() - new Date(isoStr).getTime()) / 86400000;
    }

    function clearEl(el) {
        while (el.firstChild) el.removeChild(el.firstChild);
    }

    function makeEmpty(msg) {
        var el = document.createElement('div');
        el.className = 'kosync-empty';
        el.textContent = msg;
        return el;
    }

    function isRealDevice(doc) {
        var d = (doc.device || '').toLowerCase();
        return d && d !== 'pagekeeper-bot' && d !== 'pagekeeper';
    }

    // ── Categorize documents ──

    function categorize() {
        var needsAttention = [];  // unlinked docs + orphaned books
        var healthy = [];         // linked, updated within STALE_DAYS
        var stale = [];           // linked, not updated within STALE_DAYS

        // Unlinked documents -> needs attention
        documents.forEach(function (doc) {
            if (!doc.linked_book_id) {
                needsAttention.push({ type: 'unlinked', doc: doc });
            } else {
                var days = daysSince(doc.last_updated);
                if (days <= STALE_DAYS) {
                    healthy.push(doc);
                } else {
                    stale.push(doc);
                }
            }
        });

        // Orphaned books -> needs attention
        orphanedBooks.forEach(function (book) {
            needsAttention.push({ type: 'orphaned', book: book });
        });

        return { needsAttention: needsAttention, healthy: healthy, stale: stale };
    }

    // ── Stats ──

    function renderStats(cats) {
        var statsEl = document.getElementById('kosync-stats');
        clearEl(statsEl);

        var items = [
            { label: 'need attention', count: cats.needsAttention.length, cls: cats.needsAttention.length > 0 ? 'kosync-stat--alert' : '' },
            { label: 'healthy', count: cats.healthy.length, cls: '' },
            { label: 'stale (30d+)', count: cats.stale.length, cls: cats.stale.length > 0 ? 'kosync-stat--warn' : '' },
            { label: 'total docs', count: documents.length, cls: '' }
        ];

        items.forEach(function (item) {
            var pill = document.createElement('div');
            pill.className = 'kosync-stat' + (item.cls ? ' ' + item.cls : '');
            var strong = document.createElement('strong');
            strong.textContent = item.count;
            pill.appendChild(strong);
            pill.appendChild(document.createTextNode(' ' + item.label));
            statsEl.appendChild(pill);
        });
    }

    // ── Needs Attention section ──

    function renderNeedsAttention(items) {
        var list = document.getElementById('attention-list');
        clearEl(list);

        if (!items.length) {
            list.appendChild(makeEmpty('Nothing needs attention.'));
            document.getElementById('attention-section').style.display = 'none';
            return;
        }
        document.getElementById('attention-section').style.display = '';

        items.forEach(function (item) {
            if (item.type === 'unlinked') {
                list.appendChild(buildUnlinkedCard(item.doc));
            } else {
                list.appendChild(buildOrphanedCard(item.book));
            }
        });
    }

    function buildUnlinkedCard(doc) {
        var card = document.createElement('div');
        card.className = 'kosync-card kosync-card--attention';

        var info = document.createElement('div');
        info.className = 'kosync-card-info';

        // Tag
        var tag = document.createElement('span');
        tag.className = 'kosync-tag kosync-tag--unlinked';
        tag.textContent = 'Unlinked Hash';
        info.appendChild(tag);

        // Hash
        var hashEl = document.createElement('span');
        hashEl.className = 'kosync-hash';
        hashEl.textContent = truncHash(doc.document_hash);
        hashEl.title = doc.document_hash;
        info.appendChild(hashEl);

        // Meta line
        var meta = document.createElement('div');
        meta.className = 'kosync-card-meta';

        if (doc.device) {
            var devSpan = document.createElement('span');
            devSpan.textContent = isRealDevice(doc) ? doc.device : doc.device + ' (bot)';
            if (isRealDevice(doc)) devSpan.className = 'kosync-meta--highlight';
            meta.appendChild(devSpan);
        }
        if (doc.percentage) {
            var pctSpan = document.createElement('span');
            pctSpan.textContent = (doc.percentage * 100).toFixed(1) + '%';
            meta.appendChild(pctSpan);
        }
        if (doc.first_seen) {
            var seenSpan = document.createElement('span');
            seenSpan.textContent = 'First seen ' + timeAgo(doc.first_seen);
            meta.appendChild(seenSpan);
        }
        info.appendChild(meta);
        card.appendChild(info);

        // Actions
        var actions = document.createElement('div');
        actions.className = 'kosync-card-actions';

        var linkBtn = document.createElement('button');
        linkBtn.className = 'btn btn-primary';
        linkBtn.textContent = 'Link to Book';
        linkBtn.type = 'button';
        linkBtn.addEventListener('click', function () { toggleSearchPanel(card, doc.document_hash); });
        actions.appendChild(linkBtn);

        var createBtn = document.createElement('button');
        createBtn.className = 'btn btn-secondary';
        createBtn.textContent = 'Create Book';
        createBtn.type = 'button';
        createBtn.addEventListener('click', function () { showCreateBookModal(doc.document_hash); });
        actions.appendChild(createBtn);

        var deleteBtn = document.createElement('button');
        deleteBtn.className = 'btn btn-danger';
        deleteBtn.textContent = 'Delete';
        deleteBtn.type = 'button';
        deleteBtn.addEventListener('click', function () {
            PKModal.confirm({
                title: 'Delete Document',
                message: 'Delete KoSync document ' + truncHash(doc.document_hash) + '? This removes all stored progress for this hash.',
                confirmLabel: 'Delete',
                confirmClass: 'btn btn-danger',
                onConfirm: function () { deleteDocument(doc.document_hash); }
            });
        });
        actions.appendChild(deleteBtn);

        card.appendChild(actions);
        return card;
    }

    function buildOrphanedCard(book) {
        var card = document.createElement('div');
        card.className = 'kosync-card kosync-card--attention';

        var info = document.createElement('div');
        info.className = 'kosync-card-info';

        var tag = document.createElement('span');
        tag.className = 'kosync-tag kosync-tag--orphaned';
        tag.textContent = 'Orphaned Hash';
        info.appendChild(tag);

        var title = document.createElement('div');
        title.className = 'kosync-card-title';
        title.textContent = book.title;
        info.appendChild(title);

        var meta = document.createElement('div');
        meta.className = 'kosync-card-meta';

        var hashSpan = document.createElement('span');
        hashSpan.className = 'kosync-hash';
        hashSpan.textContent = truncHash(book.kosync_doc_id);
        hashSpan.title = book.kosync_doc_id;
        meta.appendChild(hashSpan);

        var statusSpan = document.createElement('span');
        statusSpan.textContent = (book.status || '').replace(/_/g, ' ');
        meta.appendChild(statusSpan);

        var helpSpan = document.createElement('span');
        helpSpan.className = 'kosync-meta--warn';
        helpSpan.textContent = 'No device is syncing this hash \u2014 causes 502 errors each sync cycle';
        meta.appendChild(helpSpan);

        info.appendChild(meta);
        card.appendChild(info);

        var actions = document.createElement('div');
        actions.className = 'kosync-card-actions';

        var linkBookBtn = document.createElement('button');
        linkBookBtn.className = 'btn btn-primary';
        linkBookBtn.textContent = 'Link to Book';
        linkBookBtn.type = 'button';
        linkBookBtn.title = 'Search your library and link this hash to a book';
        linkBookBtn.addEventListener('click', function () {
            toggleOrphanSearchPanel(card, book.book_id);
        });
        actions.appendChild(linkBookBtn);

        var resolveBtn = document.createElement('button');
        resolveBtn.className = 'btn btn-secondary';
        resolveBtn.textContent = 'Link to Self';
        resolveBtn.type = 'button';
        resolveBtn.title = 'Create the missing record and link it back to ' + book.title;
        resolveBtn.addEventListener('click', function () {
            PKModal.confirm({
                title: 'Link to "' + book.title + '"',
                message: 'This creates the missing KoSync document record and links it to "' + book.title + '". The sync engine will then be able to track ebook progress for this hash.',
                confirmLabel: 'Link',
                confirmClass: 'btn btn-primary',
                onConfirm: function () { resolveOrphanedHash(book.book_id); }
            });
        });
        actions.appendChild(resolveBtn);

        var clearBtn = document.createElement('button');
        clearBtn.className = 'btn btn-warning';
        clearBtn.textContent = 'Clear Hash';
        clearBtn.type = 'button';
        clearBtn.title = 'Remove the hash from this book entirely';
        clearBtn.addEventListener('click', function () {
            PKModal.confirm({
                title: 'Clear Orphaned Hash',
                message: 'This removes the pre-calculated KoSync hash from "' + book.title + '". The sync engine will stop trying to look up ebook progress for this book, which eliminates the repeated 502 errors. If a KOReader device later syncs this ebook, it will appear as a new unlinked hash you can link manually.',
                confirmLabel: 'Clear Hash',
                confirmClass: 'btn btn-warning',
                onConfirm: function () { clearOrphanedHash(book.book_id); }
            });
        });
        actions.appendChild(clearBtn);
        card.appendChild(actions);
        return card;
    }

    // ── Healthy section ──

    function renderHealthy(items) {
        var list = document.getElementById('healthy-list');
        clearEl(list);

        if (!items.length) {
            list.appendChild(makeEmpty('No active linked documents.'));
            return;
        }

        items.forEach(function (doc) {
            list.appendChild(buildLinkedCard(doc));
        });
    }

    // ── Stale section ──

    function renderStale(items) {
        var section = document.getElementById('stale-section');
        var list = document.getElementById('stale-list');
        clearEl(list);

        if (!items.length) {
            section.style.display = 'none';
            return;
        }
        section.style.display = '';

        items.forEach(function (doc) {
            list.appendChild(buildLinkedCard(doc, true));
        });
    }

    function buildLinkedCard(doc, isStale) {
        var card = document.createElement('div');
        card.className = 'kosync-card' + (isStale ? ' kosync-card--stale' : '');

        var info = document.createElement('div');
        info.className = 'kosync-card-info';

        // Title prominently
        var title = document.createElement('div');
        title.className = 'kosync-card-title';
        title.textContent = doc.linked_book_title || '(unknown book)';
        info.appendChild(title);

        var meta = document.createElement('div');
        meta.className = 'kosync-card-meta';

        // Hash (secondary)
        var hashSpan = document.createElement('span');
        hashSpan.className = 'kosync-hash';
        hashSpan.textContent = truncHash(doc.document_hash);
        hashSpan.title = doc.document_hash;
        meta.appendChild(hashSpan);

        // Percentage
        if (doc.percentage) {
            var pctSpan = document.createElement('span');
            pctSpan.textContent = (doc.percentage * 100).toFixed(1) + '%';
            meta.appendChild(pctSpan);
        }

        // Device with bot indicator
        if (doc.device) {
            var devSpan = document.createElement('span');
            if (isRealDevice(doc)) {
                devSpan.textContent = doc.device;
                devSpan.className = 'kosync-meta--highlight';
            } else {
                devSpan.textContent = doc.device + ' (bot)';
            }
            meta.appendChild(devSpan);
        }

        // Last updated as time ago
        if (doc.last_updated) {
            var updSpan = document.createElement('span');
            updSpan.textContent = timeAgo(doc.last_updated);
            if (isStale) updSpan.className = 'kosync-meta--warn';
            meta.appendChild(updSpan);
        }

        info.appendChild(meta);
        card.appendChild(info);

        // Actions
        var actions = document.createElement('div');
        actions.className = 'kosync-card-actions';

        var unlinkBtn = document.createElement('button');
        unlinkBtn.className = 'btn btn-secondary';
        unlinkBtn.textContent = 'Unlink';
        unlinkBtn.type = 'button';
        unlinkBtn.addEventListener('click', function () {
            PKModal.confirm({
                title: 'Unlink Document',
                message: 'Unlink ' + truncHash(doc.document_hash) + ' from "' + (doc.linked_book_title || 'unknown') + '"?',
                confirmLabel: 'Unlink',
                confirmClass: 'btn btn-warning',
                onConfirm: function () { unlinkDocument(doc.document_hash); }
            });
        });
        actions.appendChild(unlinkBtn);

        var deleteBtn = document.createElement('button');
        deleteBtn.className = 'btn btn-danger';
        deleteBtn.textContent = 'Delete';
        deleteBtn.type = 'button';
        deleteBtn.addEventListener('click', function () {
            PKModal.confirm({
                title: 'Delete Document',
                message: 'Delete KoSync document for "' + (doc.linked_book_title || 'unknown') + '"? This removes stored progress.',
                confirmLabel: 'Delete',
                confirmClass: 'btn btn-danger',
                onConfirm: function () { deleteDocument(doc.document_hash); }
            });
        });
        actions.appendChild(deleteBtn);

        card.appendChild(actions);
        return card;
    }

    // ── Inline book search ──

    function toggleSearchPanel(card, docHash) {
        var existing = card.querySelector('.kosync-search-panel');
        if (existing) {
            existing.remove();
            return;
        }

        var panel = document.createElement('div');
        panel.className = 'kosync-search-panel';

        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'search-box';
        input.placeholder = 'Search library by title...';
        input.autocomplete = 'off';
        panel.appendChild(input);

        var results = document.createElement('div');
        panel.appendChild(results);

        card.querySelector('.kosync-card-info').appendChild(panel);
        input.focus();

        var timer = null;
        input.addEventListener('input', function () {
            clearTimeout(timer);
            var q = input.value.trim();
            if (q.length < 2) { clearEl(results); return; }
            timer = setTimeout(function () { searchBooks(q, results, docHash); }, 350);
        });
    }

    function searchBooks(query, resultsEl, docHash) {
        clearEl(resultsEl);
        resultsEl.appendChild(makeEmpty('Searching...'));

        fetch('/api/reading/library-search?q=' + encodeURIComponent(query))
            .then(function (r) { return r.json(); })
            .then(function (books) {
                clearEl(resultsEl);
                if (!books.length) {
                    resultsEl.appendChild(makeEmpty('No matching books found.'));
                    return;
                }
                books.forEach(function (book) {
                    var row = document.createElement('div');
                    row.className = 'kosync-search-result';

                    var info = document.createElement('div');
                    info.className = 'kosync-search-result-info';
                    var t = document.createElement('div');
                    t.className = 'kosync-search-result-title';
                    t.textContent = book.title || book.abs_id || '(untitled)';
                    info.appendChild(t);
                    var s = document.createElement('div');
                    s.className = 'kosync-search-result-status';
                    s.textContent = (book.status || '').replace(/_/g, ' ');
                    info.appendChild(s);
                    row.appendChild(info);

                    var btn = document.createElement('button');
                    btn.className = 'btn btn-primary';
                    btn.textContent = 'Link';
                    btn.type = 'button';
                    btn.style.cssText = 'flex-shrink: 0; padding: 4px 12px; font-size: 12px;';
                    btn.addEventListener('click', function () { linkDocument(docHash, book); });
                    row.appendChild(btn);
                    resultsEl.appendChild(row);
                });
            })
            .catch(function () {
                clearEl(resultsEl);
                resultsEl.appendChild(makeEmpty('Search failed.'));
            });
    }

    // ── Orphan search panel (link hash to a different book) ──

    function toggleOrphanSearchPanel(card, sourceBookId) {
        var existing = card.querySelector('.kosync-search-panel');
        if (existing) { existing.remove(); return; }

        var panel = document.createElement('div');
        panel.className = 'kosync-search-panel';

        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'search-box';
        input.placeholder = 'Search library by title...';
        input.autocomplete = 'off';
        panel.appendChild(input);

        var results = document.createElement('div');
        panel.appendChild(results);

        card.querySelector('.kosync-card-info').appendChild(panel);
        input.focus();

        var timer = null;
        input.addEventListener('input', function () {
            clearTimeout(timer);
            var q = input.value.trim();
            if (q.length < 2) { clearEl(results); return; }
            timer = setTimeout(function () { searchBooksForOrphan(q, results, sourceBookId); }, 350);
        });
    }

    function searchBooksForOrphan(query, resultsEl, sourceBookId) {
        clearEl(resultsEl);
        resultsEl.appendChild(makeEmpty('Searching...'));

        fetch('/api/reading/library-search?q=' + encodeURIComponent(query))
            .then(function (r) { return r.json(); })
            .then(function (books) {
                clearEl(resultsEl);
                if (!books.length) { resultsEl.appendChild(makeEmpty('No matching books found.')); return; }
                books.forEach(function (book) {
                    var row = document.createElement('div');
                    row.className = 'kosync-search-result';

                    var info = document.createElement('div');
                    info.className = 'kosync-search-result-info';
                    var t = document.createElement('div');
                    t.className = 'kosync-search-result-title';
                    t.textContent = book.title || '(untitled)';
                    info.appendChild(t);
                    var s = document.createElement('div');
                    s.className = 'kosync-search-result-status';
                    s.textContent = (book.status || '').replace(/_/g, ' ');
                    info.appendChild(s);
                    row.appendChild(info);

                    var btn = document.createElement('button');
                    btn.className = 'btn btn-primary';
                    btn.textContent = 'Link';
                    btn.type = 'button';
                    btn.style.cssText = 'flex-shrink: 0; padding: 4px 12px; font-size: 12px;';
                    btn.addEventListener('click', function () {
                        resolveOrphanedHash(sourceBookId, book.id);
                    });
                    row.appendChild(btn);
                    resultsEl.appendChild(row);
                });
            })
            .catch(function () {
                clearEl(resultsEl);
                resultsEl.appendChild(makeEmpty('Search failed.'));
            });
    }

    // ── Create book modal ──

    function showCreateBookModal(docHash) {
        var backdrop = document.createElement('div');
        backdrop.className = 'modal-backdrop';
        backdrop.style.zIndex = '1100';

        var content = document.createElement('div');
        content.className = 'modal-content';
        content.style.maxWidth = '420px';
        content.style.padding = '24px';

        var heading = document.createElement('h3');
        heading.style.cssText = 'margin: 0 0 8px; font-size: 16px; font-weight: 600;';
        heading.textContent = 'Create Ebook-Only Book';
        content.appendChild(heading);

        var desc = document.createElement('p');
        desc.style.cssText = 'margin: 0 0 12px; font-size: 13px; color: var(--color-text-muted); line-height: 1.5;';
        desc.textContent = 'Create a new book in your library linked to this KoSync hash.';
        content.appendChild(desc);

        var titleInput = document.createElement('input');
        titleInput.type = 'text';
        titleInput.className = 'search-box';
        titleInput.placeholder = 'Book title';
        titleInput.style.cssText = 'width: 100%; margin-bottom: 16px; box-sizing: border-box;';
        content.appendChild(titleInput);

        var btns = document.createElement('div');
        btns.style.cssText = 'display: flex; gap: 8px; justify-content: flex-end;';

        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn btn-secondary';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.type = 'button';
        cancelBtn.addEventListener('click', function () { backdrop.remove(); });
        btns.appendChild(cancelBtn);

        var createBtn = document.createElement('button');
        createBtn.className = 'btn btn-primary';
        createBtn.textContent = 'Create';
        createBtn.type = 'button';
        createBtn.addEventListener('click', function () {
            var title = titleInput.value.trim();
            if (!title) { titleInput.focus(); return; }
            backdrop.remove();
            createBookFromHash(docHash, title);
        });
        btns.appendChild(createBtn);

        content.appendChild(btns);
        backdrop.appendChild(content);
        backdrop.addEventListener('click', function (e) {
            if (e.target === backdrop) backdrop.remove();
        });
        document.body.appendChild(backdrop);
        titleInput.focus();
    }

    // ── Toggle sections ──

    function setupToggle(btnId, listId) {
        var btn = document.getElementById(btnId);
        var list = document.getElementById(listId);
        if (btn && list) {
            btn.addEventListener('click', function () {
                var visible = list.style.display !== 'none';
                list.style.display = visible ? 'none' : 'block';
                btn.textContent = visible ? 'show' : 'hide';
            });
        }
    }

    setupToggle('toggle-healthy', 'healthy-list');
    setupToggle('toggle-stale', 'stale-list');

    // ── API actions ──

    function linkDocument(docHash, book) {
        var body = book.abs_id ? { abs_id: book.abs_id } : { book_id: book.id };
        fetch('/api/kosync-documents/' + encodeURIComponent(docHash) + '/link', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
            .then(function (r) { return r.json(); })
            .then(function (d) { showToast(d.success ? (d.message || 'Linked') : (d.error || 'Failed')); if (d.success) refreshAll(); })
            .catch(function () { showToast('Link failed'); });
    }

    function deleteDocument(docHash) {
        fetch('/api/kosync-documents/' + encodeURIComponent(docHash), { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function (d) { showToast(d.success ? (d.message || 'Deleted') : (d.error || 'Failed')); if (d.success) refreshAll(); })
            .catch(function () { showToast('Delete failed'); });
    }

    function unlinkDocument(docHash) {
        fetch('/api/kosync-documents/' + encodeURIComponent(docHash) + '/unlink', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (d) { showToast(d.success ? (d.message || 'Unlinked') : (d.error || 'Failed')); if (d.success) refreshAll(); })
            .catch(function () { showToast('Unlink failed'); });
    }

    function resolveOrphanedHash(bookId, targetBookId) {
        var body = targetBookId ? { target_book_id: targetBookId } : {};
        var opts = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        };
        fetch('/api/kosync-documents/resolve-orphan/' + encodeURIComponent(bookId), opts)
            .then(function (r) { return r.json(); })
            .then(function (d) { showToast(d.success ? (d.message || 'Linked') : (d.error || 'Failed')); if (d.success) refreshAll(); })
            .catch(function () { showToast('Resolve failed'); });
    }

    function clearOrphanedHash(bookId) {
        fetch('/api/kosync-documents/clear-orphan/' + encodeURIComponent(bookId), { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (d) { showToast(d.success ? (d.message || 'Cleared') : (d.error || 'Failed')); if (d.success) refreshAll(); })
            .catch(function () { showToast('Clear failed'); });
    }

    function createBookFromHash(docHash, title) {
        fetch('/api/kosync-documents/' + encodeURIComponent(docHash) + '/create-book', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: title })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) { showToast(d.success ? (d.message || 'Created') : (d.error || 'Failed')); if (d.success) refreshAll(); })
            .catch(function () { showToast('Create failed'); });
    }

    // ── Refresh ──

    function refreshAll() {
        Promise.all([
            fetch('/api/kosync-documents').then(function (r) { return r.json(); }),
            fetch('/api/kosync-documents/orphaned').then(function (r) { return r.json(); })
        ]).then(function (results) {
            documents = results[0].documents || [];
            orphanedBooks = results[1] || [];
            renderAll();
        }).catch(function () { showToast('Failed to refresh'); });
    }

    // ── Render all ──

    function renderAll() {
        var cats = categorize();
        renderStats(cats);
        renderNeedsAttention(cats.needsAttention);
        renderHealthy(cats.healthy);
        renderStale(cats.stale);
    }

    renderAll();
})();
