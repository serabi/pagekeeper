/* PageKeeper — TBR Detail Page */

(function () {
  var itemId = window.TBR_ITEM_ID;
  var hcConfigured = window.HC_CONFIGURED;

  // ── Toast ──
  function showToast(msg) {
    var existing = document.querySelector('.r-tbr-toast');
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.className = 'r-tbr-toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 3000);
  }

  function clearEl(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  function makeStatusText(container, text, color) {
    clearEl(container);
    var d = document.createElement('div');
    d.style.cssText = 'font-size: 12px; color: ' + (color || 'var(--color-text-muted)') + '; padding: 8px 0;';
    d.textContent = text;
    container.appendChild(d);
  }

  // ── Up Next Toggle ──
  var upNextBtn = document.getElementById('up-next-btn');
  if (upNextBtn) {
    upNextBtn.addEventListener('click', function () {
      var current = parseInt(upNextBtn.dataset.priority) || 0;
      var newPriority = current ? 0 : 1;
      patchItem({ priority: newPriority }).then(function (ok) {
        if (ok) {
          upNextBtn.dataset.priority = newPriority;
          upNextBtn.textContent = newPriority ? '\u2605 Up Next' : '\u2606 Up Next';
          if (newPriority) {
            upNextBtn.classList.add('r-hero-action-btn--upnext-active');
            showToast('Marked as Up Next');
          } else {
            upNextBtn.classList.remove('r-hero-action-btn--upnext-active');
            showToast('Removed from Up Next');
          }
        }
      });
    });
  }

  // ── Edit Mode Toggle ──
  var editSection = document.getElementById('edit-section');
  var editToggle = document.getElementById('edit-toggle-btn');
  if (editToggle) {
    editToggle.addEventListener('click', function () {
      var visible = editSection.style.display !== 'none';
      editSection.style.display = visible ? 'none' : '';
      editToggle.textContent = visible ? 'Edit' : 'Cancel Edit';
    });
  }

  // ── Save Notes ──
  var saveNotesBtn = document.getElementById('save-notes-btn');
  if (saveNotesBtn) {
    saveNotesBtn.addEventListener('click', function () {
      var notes = document.getElementById('tbr-notes').value;
      patchItem({ notes: notes }).then(function (ok) {
        if (ok) showToast('Notes saved');
      });
    });
  }

  // ── Save Edits ──
  var saveEditsBtn = document.getElementById('save-edits-btn');
  if (saveEditsBtn) {
    saveEditsBtn.addEventListener('click', function () {
      var title = document.getElementById('edit-title').value.trim();
      if (!title) { showToast('Title is required'); return; }
      var fields = {
        title: title,
        author: document.getElementById('edit-author').value.trim(),
        subtitle: document.getElementById('edit-subtitle').value.trim(),
        description: document.getElementById('edit-description').value.trim(),
        notes: document.getElementById('edit-notes').value.trim(),
        page_count: parseInt(document.getElementById('edit-pages').value) || null,
        release_year: parseInt(document.getElementById('edit-year').value) || null,
      };
      patchItem(fields).then(function (ok) {
        if (ok) {
          showToast('Book updated');
          location.reload();
        }
      });
    });
  }

  // ── Start Reading ──
  var startBtn = document.getElementById('start-reading-btn');
  if (startBtn) {
    startBtn.addEventListener('click', function () {
      var absId = startBtn.dataset.absId;
      startBtn.disabled = true;
      startBtn.textContent = 'Starting...';
      fetch('/api/reading/tbr/' + itemId + '/start', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.success) {
            window.location.href = '/reading/book/' + absId;
          } else {
            showToast(data.error || 'Could not start reading');
            startBtn.disabled = false;
            startBtn.textContent = 'Start Reading';
          }
        })
        .catch(function () {
          showToast('Failed to start reading');
          startBtn.disabled = false;
          startBtn.textContent = 'Start Reading';
        });
    });
  }

  // ── Remove ──
  var removeBtn = document.getElementById('remove-btn');
  if (removeBtn) {
    removeBtn.addEventListener('click', function () {
      var isHc = removeBtn.dataset.isHc === 'true';
      if (isHc) {
        showRemoveConfirm();
      } else {
        if (confirm('Remove this book from your TBR list?')) {
          doRemove(false);
        }
      }
    });
  }

  function showRemoveConfirm() {
    var backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.style.zIndex = '1100';

    var content = document.createElement('div');
    content.className = 'modal-content';
    content.style.maxWidth = '380px';
    content.style.padding = '24px';

    var title = document.createElement('h3');
    title.style.cssText = 'margin: 0 0 8px; font-size: 16px; font-weight: 600;';
    title.textContent = 'Remove from TBR';
    content.appendChild(title);

    var desc = document.createElement('p');
    desc.style.cssText = 'margin: 0 0 20px; font-size: 13px; color: var(--color-text-muted); line-height: 1.5;';
    desc.textContent = 'This book was imported from Hardcover. Would you also like to remove it from your Hardcover shelf?';
    content.appendChild(desc);

    var btns = document.createElement('div');
    btns.style.cssText = 'display: flex; flex-direction: column; gap: 8px;';

    var bothBtn = document.createElement('button');
    bothBtn.className = 'btn btn-primary';
    bothBtn.style.cssText = 'width: 100%; background: var(--color-error); border-color: var(--color-error);';
    bothBtn.textContent = 'Remove from both';
    bothBtn.addEventListener('click', function () { backdrop.remove(); doRemove(true); });
    btns.appendChild(bothBtn);

    var localBtn = document.createElement('button');
    localBtn.className = 'btn btn-secondary';
    localBtn.style.width = '100%';
    localBtn.textContent = 'Remove from PageKeeper only';
    localBtn.addEventListener('click', function () { backdrop.remove(); doRemove(false); });
    btns.appendChild(localBtn);

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.style.cssText = 'width: 100%; opacity: 0.7;';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', function () { backdrop.remove(); });
    btns.appendChild(cancelBtn);

    content.appendChild(btns);
    backdrop.appendChild(content);
    backdrop.addEventListener('click', function (e) { if (e.target === backdrop) backdrop.remove(); });
    document.body.appendChild(backdrop);
  }

  function doRemove(removeFromHc) {
    var qs = removeFromHc ? '?remove_from_hc=true' : '';
    fetch('/api/reading/tbr/' + itemId + qs, { method: 'DELETE' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) {
          window.location.href = '/reading/tbr';
        } else {
          showToast(data.error || 'Failed to remove');
        }
      })
      .catch(function () { showToast('Failed to remove'); });
  }

  // ── Cover Picker ──
  var coverBtn = document.getElementById('change-cover-btn');
  var coverEditBtn = document.getElementById('change-cover-edit-btn');
  if (coverBtn) coverBtn.addEventListener('click', showCoverPicker);
  if (coverEditBtn) coverEditBtn.addEventListener('click', showCoverPicker);

  function showCoverPicker() {
    var backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.style.zIndex = '1100';

    var content = document.createElement('div');
    content.className = 'modal-content';
    content.style.cssText = 'max-width: 480px; padding: 24px; max-height: 80vh; overflow-y: auto;';

    var heading = document.createElement('h3');
    heading.style.cssText = 'margin: 0 0 12px; font-size: 16px; font-weight: 600;';
    heading.textContent = 'Change Cover';
    content.appendChild(heading);

    var searchRow = document.createElement('div');
    searchRow.style.cssText = 'display: flex; gap: 8px; margin-bottom: 12px;';
    var searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'search-box';
    searchInput.placeholder = 'Search for covers...';
    searchInput.value = document.getElementById('edit-title') ? document.getElementById('edit-title').value : '';
    searchInput.style.flex = '1';
    var searchBtn = document.createElement('button');
    searchBtn.className = 'btn btn-primary';
    searchBtn.type = 'button';
    searchBtn.textContent = 'Search';
    searchRow.appendChild(searchInput);
    searchRow.appendChild(searchBtn);
    content.appendChild(searchRow);

    var urlRow = document.createElement('div');
    urlRow.style.cssText = 'display: flex; gap: 8px; margin-bottom: 16px;';
    var urlInput = document.createElement('input');
    urlInput.type = 'text';
    urlInput.className = 'search-box';
    urlInput.placeholder = 'Or paste a cover image URL...';
    urlInput.style.flex = '1';
    var urlBtn = document.createElement('button');
    urlBtn.className = 'btn btn-secondary';
    urlBtn.type = 'button';
    urlBtn.textContent = 'Use URL';
    urlRow.appendChild(urlInput);
    urlRow.appendChild(urlBtn);
    content.appendChild(urlRow);

    var resultsGrid = document.createElement('div');
    resultsGrid.style.cssText = 'display: grid; grid-template-columns: repeat(auto-fill, minmax(80px, 1fr)); gap: 10px;';
    content.appendChild(resultsGrid);

    var status = document.createElement('div');
    status.style.cssText = 'font-size: 12px; color: var(--color-text-muted); margin-top: 8px;';
    content.appendChild(status);

    function doSearch() {
      var q = searchInput.value.trim();
      if (!q) return;
      clearEl(resultsGrid);
      status.textContent = 'Searching...';
      fetch('/api/hardcover/cover-search?query=' + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          status.textContent = '';
          var results = (data && data.results) || [];
          if (!results.length) { status.textContent = 'No results found.'; return; }
          results.forEach(function (book) {
            if (!book.cached_image) return;
            var card = document.createElement('div');
            card.style.cssText = 'cursor: pointer; border-radius: 6px; overflow: hidden; border: 2px solid transparent; transition: border-color 0.2s;';
            card.addEventListener('mouseenter', function () { card.style.borderColor = 'var(--color-primary)'; });
            card.addEventListener('mouseleave', function () { card.style.borderColor = 'transparent'; });
            var img = document.createElement('img');
            img.src = book.cached_image;
            img.alt = book.title || '';
            img.title = (book.title || '') + (book.author ? ' \u2014 ' + book.author : '');
            img.style.cssText = 'width: 100%; display: block;';
            img.onerror = function () { card.remove(); };
            card.appendChild(img);
            card.addEventListener('click', function () { selectCover(book.cached_image); });
            resultsGrid.appendChild(card);
          });
        })
        .catch(function () { status.textContent = 'Search failed.'; });
    }

    function selectCover(url) {
      patchItem({ cover_url: url }).then(function (ok) {
        if (ok) {
          backdrop.remove();
          showToast('Cover updated');
          location.reload();
        }
      });
    }

    searchBtn.addEventListener('click', doSearch);
    searchInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') doSearch(); });
    urlBtn.addEventListener('click', function () {
      var url = urlInput.value.trim();
      if (url) selectCover(url);
    });

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.style.cssText = 'width: 100%; margin-top: 12px;';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', function () { backdrop.remove(); });
    content.appendChild(cancelBtn);

    backdrop.appendChild(content);
    backdrop.addEventListener('click', function (e) { if (e.target === backdrop) backdrop.remove(); });
    document.body.appendChild(backdrop);

    if (searchInput.value) doSearch();
  }

  // ── HC Linker ──
  var linkHcBtn = document.getElementById('link-hc-btn');
  if (linkHcBtn) linkHcBtn.addEventListener('click', showHcLinker);

  function showHcLinker() {
    var backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.style.zIndex = '1100';

    var content = document.createElement('div');
    content.className = 'modal-content';
    content.style.cssText = 'max-width: 480px; padding: 24px; max-height: 80vh; overflow-y: auto;';

    var heading = document.createElement('h3');
    heading.style.cssText = 'margin: 0 0 4px; font-size: 16px; font-weight: 600;';
    heading.textContent = 'Link to Hardcover';
    content.appendChild(heading);

    var desc = document.createElement('p');
    desc.style.cssText = 'margin: 0 0 12px; font-size: 12px; color: var(--color-text-muted);';
    desc.textContent = 'Search Hardcover to link this book. Enables enrichment and status sync.';
    content.appendChild(desc);

    var searchRow = document.createElement('div');
    searchRow.style.cssText = 'display: flex; gap: 8px; margin-bottom: 12px;';
    var searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'search-box';
    searchInput.placeholder = 'Search Hardcover...';
    searchInput.value = document.getElementById('edit-title') ? document.getElementById('edit-title').value : '';
    searchInput.style.flex = '1';
    var searchBtn = document.createElement('button');
    searchBtn.className = 'btn btn-primary';
    searchBtn.type = 'button';
    searchBtn.textContent = 'Search';
    searchRow.appendChild(searchInput);
    searchRow.appendChild(searchBtn);
    content.appendChild(searchRow);

    var resultsList = document.createElement('div');
    resultsList.style.cssText = 'display: flex; flex-direction: column; gap: 8px;';
    content.appendChild(resultsList);

    var status = document.createElement('div');
    status.style.cssText = 'font-size: 12px; color: var(--color-text-muted); margin-top: 8px;';
    content.appendChild(status);

    function doSearch() {
      var q = searchInput.value.trim();
      if (!q) return;
      clearEl(resultsList);
      status.textContent = 'Searching...';
      fetch('/api/reading/tbr/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, provider: 'hardcover' }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          status.textContent = '';
          var results = (data && data.results) || [];
          if (!results.length) { status.textContent = 'No results found.'; return; }
          results.forEach(function (r) {
            var row = document.createElement('div');
            row.style.cssText = 'display: flex; gap: 10px; align-items: center; padding: 8px; border-radius: 8px; cursor: pointer; background: rgba(255,255,255,0.03);';
            row.addEventListener('mouseenter', function () { row.style.background = 'rgba(255,255,255,0.07)'; });
            row.addEventListener('mouseleave', function () { row.style.background = 'rgba(255,255,255,0.03)'; });
            if (r.cover_url) {
              var img = document.createElement('img');
              img.src = r.cover_url;
              img.style.cssText = 'width: 40px; height: 60px; object-fit: cover; border-radius: 4px;';
              img.onerror = function () { this.style.display = 'none'; };
              row.appendChild(img);
            }
            var info = document.createElement('div');
            info.style.cssText = 'flex: 1; min-width: 0;';
            var t = document.createElement('div');
            t.style.cssText = 'font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;';
            t.textContent = r.title;
            info.appendChild(t);
            if (r.author) {
              var a = document.createElement('div');
              a.style.cssText = 'font-size: 11px; color: var(--color-text-muted);';
              a.textContent = r.author;
              info.appendChild(a);
            }
            row.appendChild(info);

            var linkBtn = document.createElement('button');
            linkBtn.className = 'btn btn-primary';
            linkBtn.type = 'button';
            linkBtn.textContent = 'Link';
            linkBtn.style.cssText = 'flex-shrink: 0; padding: 4px 12px; font-size: 12px;';
            linkBtn.addEventListener('click', function (e) {
              e.stopPropagation();
              doLink(r);
            });
            row.appendChild(linkBtn);
            resultsList.appendChild(row);
          });
        })
        .catch(function () { status.textContent = 'Search failed.'; });
    }

    function doLink(result) {
      var fields = {
        hardcover_book_id: result.hardcover_book_id,
        hardcover_slug: result.hardcover_slug,
      };
      if (result.cover_url) fields.cover_url = result.cover_url;
      if (result.rating) fields.rating = result.rating;
      if (result.page_count) fields.page_count = result.page_count;
      if (result.release_year) fields.release_year = result.release_year;

      patchItem(fields).then(function (ok) {
        if (ok) {
          showToast('Linked to Hardcover');
          fetch('/api/reading/tbr/enrich', { method: 'POST' }).catch(function (err) { console.warn('Background enrichment failed:', err); });
          backdrop.remove();
          location.reload();
        }
      });
    }

    searchBtn.addEventListener('click', doSearch);
    searchInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') doSearch(); });

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.style.cssText = 'width: 100%; margin-top: 12px;';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', function () { backdrop.remove(); });
    content.appendChild(cancelBtn);

    backdrop.appendChild(content);
    backdrop.addEventListener('click', function (e) { if (e.target === backdrop) backdrop.remove(); });
    document.body.appendChild(backdrop);

    if (searchInput.value) doSearch();
  }

  // ── Library Book Search & Link ──
  var libSearchInput = document.getElementById('library-search-input');
  var libSearchResults = document.getElementById('library-search-results');
  var libSearchTimer = null;

  if (libSearchInput) {
    libSearchInput.addEventListener('input', function () {
      clearTimeout(libSearchTimer);
      var q = libSearchInput.value.trim();
      if (q.length < 2) {
        if (libSearchResults) clearEl(libSearchResults);
        return;
      }
      libSearchTimer = setTimeout(function () { searchLibraryBooks(q); }, 350);
    });
  }

  function searchLibraryBooks(query) {
    if (!libSearchResults) return;
    makeStatusText(libSearchResults, 'Searching...');
    fetch('/api/reading/library-search?q=' + encodeURIComponent(query))
      .then(function (r) { return r.json(); })
      .then(function (books) {
        clearEl(libSearchResults);
        if (!books.length) {
          makeStatusText(libSearchResults, 'No matching books found.', 'var(--color-text-faint)');
          return;
        }
        books.forEach(function (book) {
          var row = document.createElement('div');
          row.className = 'tbr-library-result';

          var info = document.createElement('div');
          info.style.cssText = 'flex: 1; min-width: 0;';
          var t = document.createElement('div');
          t.style.cssText = 'font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;';
          t.textContent = book.title || book.abs_id;
          info.appendChild(t);
          var statusEl = document.createElement('span');
          statusEl.style.cssText = 'font-size: 11px; color: var(--color-text-muted);';
          statusEl.textContent = (book.status || '').replace('_', ' ');
          info.appendChild(statusEl);
          row.appendChild(info);

          var linkBtn = document.createElement('button');
          linkBtn.className = 'btn btn-primary';
          linkBtn.type = 'button';
          linkBtn.textContent = 'Link';
          linkBtn.style.cssText = 'flex-shrink: 0; padding: 4px 12px; font-size: 12px;';
          linkBtn.addEventListener('click', function () { linkToLibrary(book.abs_id); });
          row.appendChild(linkBtn);

          libSearchResults.appendChild(row);
        });
      })
      .catch(function () {
        makeStatusText(libSearchResults, 'Search failed.', 'var(--color-error)');
      });
  }

  function linkToLibrary(absId) {
    fetch('/api/reading/tbr/' + itemId + '/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ abs_id: absId }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) {
          showToast('Linked to library book');
          location.reload();
        } else {
          showToast(data.error || 'Link failed');
        }
      })
      .catch(function () { showToast('Failed to link'); });
  }

  // ── Unlink Library ──
  var unlinkBtn = document.getElementById('unlink-library-btn');
  if (unlinkBtn) {
    unlinkBtn.addEventListener('click', function () {
      fetch('/api/reading/tbr/' + itemId + '/link', { method: 'DELETE' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.success) {
            showToast('Unlinked from library');
            location.reload();
          } else {
            showToast(data.error || 'Unlink failed');
          }
        })
        .catch(function () { showToast('Failed to unlink'); });
    });
  }

  // ── Patch helper ──
  function patchItem(fields) {
    return fetch('/api/reading/tbr/' + itemId, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) return true;
        showToast(data.error || 'Update failed');
        return false;
      })
      .catch(function () { showToast('Update failed'); return false; });
  }
})();
