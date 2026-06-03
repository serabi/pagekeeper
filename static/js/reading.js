/* PageKeeper — Reading Tab */

// Attach cover-fallback logic via event delegation (no inline onerror needed)
document.addEventListener('error', function (e) {
  const img = e.target;
  if (!img || !img.classList.contains('r-cover-img')) return;

  const fallbackId = (img.dataset.fallbackId || '').trim();
  if (fallbackId && !img.dataset.fallbackAttempted) {
    img.dataset.fallbackAttempted = '1';
    img.src = '/covers/' + encodeURIComponent(fallbackId) + '.jpg';
    return;
  }

  img.style.display = 'none';
  var placeholder = img.nextElementSibling;
  if (placeholder) {
    placeholder.classList.remove('hidden');
  }
}, true);

function initReadingPage(currentYear, activeTab) {
  const sectionsRoot = document.getElementById('reading-sections');
  if (!sectionsRoot) return;

  const cards = () => Array.from(sectionsRoot.querySelectorAll('.r-book-card'));
  const searchInput = document.getElementById('reading-search');
  const mobileSearchInput = document.getElementById('reading-search-mobile');
  const sortSelect = document.getElementById('reading-sort');
  const mobileSortSelect = document.getElementById('reading-sort-mobile');
  const filterChips = document.querySelectorAll('.r-filter-chip');
  const viewBtns = document.querySelectorAll('.r-view-btn');
  const resultsInfo = document.getElementById('results-info');
  const resultsText = document.getElementById('results-text');
  const emptyTab = document.getElementById('empty-tab');
  const sections = Array.from(sectionsRoot.querySelectorAll('.r-section'));
  const controlsModal = document.getElementById('reading-controls-modal');
  const controlsOpen = document.getElementById('reading-controls-open');
  const controlsClose = document.getElementById('reading-controls-close');
  const mainTabs = Array.from(document.querySelectorAll('.r-main-tab-btn'));
  const mainPanels = Array.from(document.querySelectorAll('.r-main-panel'));
  const statsShell = document.getElementById('reading-stats-shell');
  const statsYearSelect = document.getElementById('reading-stats-year');

  let activeFilter = 'all';
  let currentView = 'list';
  const desktopMedia = window.matchMedia('(min-width: 961px)');

  function setMainTab(tabName) {
    mainTabs.forEach(tab => {
      const active = tab.dataset.mainTab === tabName;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    mainPanels.forEach(panel => {
      panel.hidden = panel.dataset.mainPanel !== tabName;
    });
  }

  mainTabs.forEach(tab => {
    tab.addEventListener('click', () => setMainTab(tab.dataset.mainTab));
  });
  setMainTab(activeTab || 'log');

  function syncSearchInputs(source) {
    const value = source ? source.value : '';
    if (searchInput && source !== searchInput) searchInput.value = value;
    if (mobileSearchInput && source !== mobileSearchInput) mobileSearchInput.value = value;
  }

  function syncSortInputs(source) {
    const value = source ? source.value : 'activity-desc';
    if (sortSelect && source !== sortSelect) sortSelect.value = value;
    if (mobileSortSelect && source !== mobileSortSelect) mobileSortSelect.value = value;
  }

  function showControlsModal() {
    if (controlsModal) controlsModal.style.display = 'flex';
  }

  function hideControlsModal() {
    if (controlsModal) controlsModal.style.display = 'none';
  }

  filterChips.forEach(chip => {
    chip.addEventListener('click', () => {
      // If the chip has data-switch-tab, switch to that tab instead of filtering
      if (chip.dataset.switchTab) {
        setMainTab(chip.dataset.switchTab);
        return;
      }
      filterChips.forEach(item => {
        item.classList.remove('active');
        item.setAttribute('aria-selected', 'false');
      });
      chip.classList.add('active');
      chip.setAttribute('aria-selected', 'true');
      activeFilter = chip.dataset.filter;
      applyFiltersAndSort();
    });
  });

  var _filterTimer;
  [searchInput, mobileSearchInput].forEach(input => {
    if (!input) return;
    input.addEventListener('input', () => {
      syncSearchInputs(input);
      clearTimeout(_filterTimer);
      _filterTimer = setTimeout(applyFiltersAndSort, 150);
    });
  });

  [sortSelect, mobileSortSelect].forEach(select => {
    if (!select) return;
    select.addEventListener('change', () => {
      syncSortInputs(select);
      applyFiltersAndSort();
    });
  });

  if (controlsOpen) controlsOpen.addEventListener('click', showControlsModal);
  if (controlsClose) controlsClose.addEventListener('click', hideControlsModal);
  if (controlsModal) {
    controlsModal.addEventListener('click', e => {
      if (e.target === controlsModal) hideControlsModal();
    });
  }

  function setView(view, persist) {
    const forcedView = desktopMedia.matches ? view : 'list';
    currentView = forcedView;
    sectionsRoot.classList.toggle('r-grid-view', forcedView === 'grid');
    viewBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.view === forcedView);
      btn.disabled = !desktopMedia.matches;
    });
    if (persist && desktopMedia.matches) {
      try { localStorage.setItem('pk-reading-view', forcedView); } catch (e) {}
    }
  }

  viewBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      setView(btn.dataset.view, true);
    });
  });

  try {
    const savedView = localStorage.getItem('pk-reading-view');
    setView(savedView === 'grid' ? 'grid' : 'list', false);
  } catch (e) {}
  if (!currentView) setView('list', false);

  const onDesktopChange = () => setView(currentView, false);
  if (desktopMedia.addEventListener) {
    desktopMedia.addEventListener('change', onDesktopChange);
  } else if (desktopMedia.addListener) {
    desktopMedia.addListener(onDesktopChange);
  }

  function compareCards(a, b) {
    const sortValue = sortSelect ? sortSelect.value : 'activity-desc';
    switch (sortValue) {
      case 'title-asc':
        return (a.dataset.title || '').localeCompare(b.dataset.title || '');
      case 'progress-desc':
        return (
          (parseFloat(b.dataset.progress) || 0) - (parseFloat(a.dataset.progress) || 0)
          || (a.dataset.title || '').localeCompare(b.dataset.title || '')
        );
      case 'finished-desc':
        return (
          (b.dataset.finished || '').localeCompare(a.dataset.finished || '')
          || (b.dataset.activity || '').localeCompare(a.dataset.activity || '')
          || (a.dataset.title || '').localeCompare(b.dataset.title || '')
        );
      case 'activity-desc':
      default:
        return (
          (b.dataset.activity || '').localeCompare(a.dataset.activity || '')
          || (b.dataset.finished || '').localeCompare(a.dataset.finished || '')
          || (parseFloat(b.dataset.progress) || 0) - (parseFloat(a.dataset.progress) || 0)
          || (a.dataset.title || '').localeCompare(b.dataset.title || '')
        );
    }
  }

  function getMatches(card, term) {
    const haystack = [
      (card.dataset.title || '').toLowerCase(),
      (card.dataset.author || '').toLowerCase(),
    ].join(' ');
    const matchesSearch = !term || haystack.includes(term);
    const matchesFilter = activeFilter === 'all' || card.dataset.status === activeFilter;
    return matchesSearch && matchesFilter;
  }

  function rebuildFinishedSection(section) {
    const allCards = Array.from(section.querySelectorAll('.r-book-card'));
    section.querySelectorAll('.r-year-group').forEach(group => group.remove());
    const existingStack = section.querySelector('.r-book-stack');
    if (existingStack) existingStack.remove();

    if (allCards.length === 0) return;

    const byYear = new Map();
    allCards.forEach(card => {
      const year = card.dataset.year || 'Unknown';
      if (!byYear.has(year)) byYear.set(year, []);
      byYear.get(year).push(card);
    });

    Array.from(byYear.keys()).sort((a, b) => b.localeCompare(a)).forEach(year => {
      const group = document.createElement('div');
      group.className = 'r-year-group';
      group.dataset.yearGroup = year;

      const heading = document.createElement('div');
      heading.className = 'r-year-heading';
      const text = document.createElement('span');
      text.textContent = year;
      heading.appendChild(text);
      group.appendChild(heading);

      const stack = document.createElement('div');
      stack.className = 'r-book-stack';
      byYear.get(year).forEach(card => stack.appendChild(card));
      group.appendChild(stack);
      section.appendChild(group);
    });
  }

  function sortSection(section) {
    const allCards = Array.from(section.querySelectorAll('.r-book-card'));
    allCards.sort(compareCards);

    if (section.dataset.section === 'finished') {
      allCards.forEach(card => section.appendChild(card));
      rebuildFinishedSection(section);
      return;
    }

    let stack = section.querySelector('.r-book-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'r-book-stack';
      section.appendChild(stack);
    }
    allCards.forEach(card => stack.appendChild(card));
  }

  function applyFiltersAndSort() {
    const term = ((searchInput && searchInput.value) || '').toLowerCase().trim();
    let visibleCount = 0;
    let filterTotal = 0;

    cards().forEach(card => {
      if (activeFilter === 'all' || card.dataset.status === activeFilter) {
        filterTotal++;
      }
    });

    sections.forEach(section => {
      sortSection(section);
      const sectionCards = Array.from(section.querySelectorAll('.r-book-card'));
      let sectionVisibleCount = 0;

      sectionCards.forEach(card => {
        const visible = getMatches(card, term);
        card.style.display = visible ? '' : 'none';
        if (visible) {
          visibleCount++;
          sectionVisibleCount++;
        }
      });

      if (section.dataset.section === 'finished') {
        section.querySelectorAll('.r-year-group').forEach(group => {
          const hasVisibleCards = Array.from(group.querySelectorAll('.r-book-card')).some(card => card.style.display !== 'none');
          group.style.display = hasVisibleCards ? '' : 'none';
        });
      }

      const emptyState = section.querySelector('.r-section-empty');
      if (emptyState) emptyState.hidden = sectionVisibleCount !== 0;
      section.style.display = sectionVisibleCount === 0 ? 'none' : '';
    });

    if (resultsInfo && resultsText) {
      if (term || activeFilter !== 'all') {
        resultsInfo.hidden = false;
        resultsText.textContent = `Showing ${visibleCount} of ${filterTotal} books`;
      } else {
        resultsInfo.hidden = true;
      }
    }

    if (emptyTab) emptyTab.hidden = visibleCount !== 0;
    sectionsRoot.style.display = visibleCount === 0 ? 'none' : '';
  }

  syncSortInputs(sortSelect || mobileSortSelect);
  syncSearchInputs(searchInput || mobileSearchInput);
  applyFiltersAndSort();

  function updateGoalCard(stats, year) {
    const goalCount = document.getElementById('stats-goal-count');
    const goalLabel = document.getElementById('stats-goal-label');
    const goalProgress = document.getElementById('stats-goal-progress');
    const goalRing = document.querySelector('.r-goal-widget--stats .r-goal-ring circle:last-child');
    const yearLabel = document.getElementById('stats-year-label');
    const totalTracked = document.getElementById('stats-total-tracked');

    if (goalCount) {
      goalCount.textContent = stats.goal_target ? `${stats.goal_completed}/${stats.goal_target}` : '+';
    }
    if (goalLabel) {
      goalLabel.textContent = stats.goal_target ? `${year} goal` : 'Set goal';
    }
    if (goalProgress) {
      goalProgress.textContent = `${Math.round(stats.goal_percent || 0)}%`;
    }
    if (yearLabel) {
      yearLabel.textContent = year;
    }
    if (totalTracked) {
      totalTracked.textContent = `${stats.total_tracked} tracked books total`;
    }
    if (goalRing) {
      const dash = Math.min((stats.goal_percent || 0) * 1.257, 125.7);
      goalRing.setAttribute('stroke-dasharray', `${dash} 125.7`);
    }
  }

  function renderStatsChart(stats) {
    const chart = document.getElementById('stats-chart');
    if (!chart) return;
    const values = stats.monthly_finished || [];
    const labels = stats.monthly_labels || ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const maxValue = values.reduce((acc, value) => Math.max(acc, value), 0);

    chart.querySelectorAll('.r-stats-bar-group').forEach((group, idx) => {
      const value = values[idx] || 0;
      const fill = group.querySelector('.r-stats-bar-fill');
      const valueEl = group.querySelector('.r-stats-bar-value');
      const labelEl = group.querySelector('.r-stats-bar-label');
      if (fill) {
        fill.style.height = `${maxValue > 0 ? ((value / maxValue) * 100).toFixed(1) : 0}%`;
      }
      if (valueEl) valueEl.textContent = value;
      if (labelEl) labelEl.textContent = labels[idx] || '';
    });
  }

  function renderStats(stats) {
    const finished = document.getElementById('stats-books-finished');
    const current = document.getElementById('stats-currently-reading');
    const average = document.getElementById('stats-average-rating');
    if (finished) finished.textContent = stats.books_finished;
    if (current) current.textContent = stats.currently_reading;
    if (average) average.textContent = stats.average_rating == null ? '\u2014' : Number(stats.average_rating).toFixed(2);
    updateGoalCard(stats, stats.year);
    renderStatsChart(stats);
  }

  function loadStats(year) {
    return fetch(`/api/reading/stats/${year}`)
      .then(r => {
        if (!r.ok) throw new Error('Failed to load stats');
        return r.json();
      })
      .then(data => {
        renderStats(data);
      })
      .catch(err => console.debug('Stats load failed:', err));
  }

  if (statsYearSelect) {
    statsYearSelect.addEventListener('change', () => {
      const year = parseInt(statsYearSelect.value, 10) || currentYear;
      loadStats(year);
    });
  }

  // ── Goal modal ─────────────────────────────────────────────

  const goalCard = document.getElementById('goal-card');
  const goalModal = document.getElementById('goal-modal');
  const goalClose = document.getElementById('goal-modal-close');
  const goalCancel = document.getElementById('goal-cancel');
  const goalSave = document.getElementById('goal-save');
  const goalInput = document.getElementById('goal-input');

  function showModal()  { if (goalModal) goalModal.style.display = 'flex'; }
  function hideModal()  { if (goalModal) goalModal.style.display = 'none'; }

  if (goalCard) goalCard.addEventListener('click', showModal);
  if (goalClose) goalClose.addEventListener('click', hideModal);
  if (goalCancel) goalCancel.addEventListener('click', hideModal);
  if (goalModal) goalModal.addEventListener('click', e => { if (e.target === goalModal) hideModal(); });

  if (goalSave) {
    goalSave.addEventListener('click', () => {
      const activeYear = parseInt(statsYearSelect?.value, 10) || currentYear;
      const target = parseInt(goalInput?.value, 10);
      if (!target || target < 1) return;
      goalSave.disabled = true;
      fetch(`/api/reading/goal/${activeYear}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_books: target }),
      })
        .then(r => {
          if (!r.ok) throw new Error('Failed to save goal');
          return r.json();
        })
        .then(data => {
          if (data.success) {
            return loadStats(activeYear).then(() => {
              hideModal();
              goalSave.disabled = false;
            });
          }
          goalSave.disabled = false;
        })
        .catch(() => { goalSave.disabled = false; });
    });
  }

  if (statsShell && statsYearSelect) {
    loadStats(parseInt(statsYearSelect.value, 10) || currentYear);
  }
}


function initReadingDetail() {
  // ── PageKeeper metadata overrides ──
  const metadataModal = document.getElementById('metadata-override-modal');
  const metadataTitleInput = document.getElementById('metadata-title-override');
  const metadataAuthorInput = document.getElementById('metadata-author-override');
  const metadataStatus = document.getElementById('metadata-override-status');
  const metadataSaveBtn = document.getElementById('metadata-save-btn');
  const metadataClearBtn = document.getElementById('metadata-clear-btn');

  function setMetadataStatus(message, state) {
    if (!metadataStatus) return;
    metadataStatus.hidden = !message;
    metadataStatus.textContent = message || '';
    metadataStatus.className = 'metadata-override-status' + (state ? ` metadata-override-status--${state}` : '');
  }

  function setMetadataSaving(isSaving) {
    if (metadataSaveBtn) metadataSaveBtn.disabled = isSaving;
    if (metadataClearBtn) metadataClearBtn.disabled = isSaving;
  }

  function openMetadataOverrideModal() {
    if (!metadataModal) return;
    setMetadataStatus('', '');
    metadataModal.style.display = 'flex';
    if (metadataTitleInput) metadataTitleInput.focus();
  }

  function closeMetadataOverrideModal() {
    if (!metadataModal) return;
    metadataModal.style.display = 'none';
    setMetadataStatus('', '');
  }

  function saveMetadataOverrides(clear) {
    if (!metadataModal) return;
    const bookId = metadataModal.dataset.bookId;
    const hasExistingOverride = metadataModal.dataset.hasOverride === 'true';
    const titleOverride = clear ? null : ((metadataTitleInput && metadataTitleInput.value.trim()) || null);
    const authorOverride = clear ? null : ((metadataAuthorInput && metadataAuthorInput.value.trim()) || null);

    if (!clear && !hasExistingOverride && !titleOverride && !authorOverride) {
      setMetadataStatus('Enter a title or author override before saving.', 'error');
      return;
    }

    setMetadataSaving(true);
    setMetadataStatus(clear ? 'Clearing overrides...' : 'Saving...', 'muted');
    fetch(`/api/reading/book/${bookId}/metadata-overrides`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title_override: titleOverride,
        author_override: authorOverride,
      }),
    })
      .then(r => r.json().then(data => ({ ok: r.ok, data })))
      .then(result => {
        if (!result.ok || !result.data.success) {
          throw result.data;
        }
        window.location.reload();
      })
      .catch(err => {
        setMetadataSaving(false);
        setMetadataStatus((err && err.error) || 'Metadata was not saved.', 'error');
      });
  }

  window.openMetadataOverrideModal = openMetadataOverrideModal;
  window.closeMetadataOverrideModal = closeMetadataOverrideModal;
  window.saveMetadataOverrides = saveMetadataOverrides;

  // ── Rating stars (5 stars, half-star support) ──
  const rc = document.getElementById('rating-stars');
  if (rc) {
    const bookId = rc.dataset.bookId;
    const hardcoverSyncAvailable = rc.dataset.hardcoverSyncAvailable === 'true';
    const stars = rc.querySelectorAll('.r-star-btn');
    const label = document.getElementById('rating-label');
    const syncStatus = document.getElementById('rating-sync-status');

    function formatRating(value) {
      return Number(value).toFixed(value % 1 === 0 ? 0 : 1) + '/5';
    }

    function applyRatingState(value) {
      const numeric = Number(value) || 0;
      stars.forEach(star => {
        const idx = parseInt(star.dataset.index, 10);
        star.classList.remove('r-star-full', 'r-star-half', 'r-star-empty');
        if (numeric >= idx) {
          star.classList.add('r-star-full');
        } else if (numeric >= idx - 0.5) {
          star.classList.add('r-star-half');
        } else {
          star.classList.add('r-star-empty');
        }
      });
      rc.dataset.rating = String(numeric);
      if (label) label.textContent = numeric > 0 ? formatRating(numeric) : 'Rate';
    }

    function setSyncStatus(state, message) {
      if (!syncStatus) return;
      syncStatus.hidden = false;
      syncStatus.className = `r-rating-sync r-rating-sync--${state}`;
      syncStatus.textContent = message;
    }

    function submitRating(value) {
      fetch(`/api/reading/book/${bookId}/rating`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating: value }),
      })
        .then(r => {
          if (!r.ok) throw new Error('Failed to save rating');
          return r.json();
        })
        .then(data => {
          if (data.success) {
            applyRatingState(data.rating);
            if (!hardcoverSyncAvailable) {
              if (syncStatus) syncStatus.hidden = true;
            } else if (data.hardcover_synced) {
              setSyncStatus('success', 'Synced to Hardcover');
            } else if (data.hardcover_error) {
              setSyncStatus('warning', 'Saved locally, Hardcover sync failed');
            } else {
              if (syncStatus) syncStatus.hidden = true;
            }
          }
        })
        .catch(() => {
          setSyncStatus('error', 'Save failed — rating not saved');
        });
    }

    function getStarValue(star, clientX) {
      const idx = parseInt(star.dataset.index, 10);
      const rect = star.getBoundingClientRect();
      const isLeftThird = (clientX - rect.left) < rect.width / 3;
      return isLeftThird ? idx - 0.5 : idx;
    }

    stars.forEach(star => {
      // Click / tap: left half → half star, right half → full star
      star.addEventListener('click', (e) => {
        submitRating(getStarValue(star, e.clientX));
      });

      // Touch: use touch coordinates for half-star detection
      star.addEventListener('touchend', (e) => {
        e.preventDefault();
        const touch = e.changedTouches[0];
        submitRating(getStarValue(star, touch.clientX));
      });

      // Hover preview (desktop)
      star.addEventListener('mousemove', (e) => {
        const previewValue = getStarValue(star, e.clientX);
        applyRatingState(previewValue);
        if (label) label.textContent = formatRating(previewValue);
      });
    });

    // Restore actual rating when mouse leaves the rating area
    rc.addEventListener('mouseleave', () => {
      applyRatingState(parseFloat(rc.dataset.rating || '0'));
    });

    applyRatingState(parseFloat(rc.dataset.rating || '0'));
  }

  // ── Date fields ──
  function flashDateFeedback(input, ok, msg) {
    const color = ok ? 'var(--color-success, #22c55e)' : 'var(--color-danger, red)';
    input.style.outline = `2px solid ${color}`;
    input.title = msg || '';
    setTimeout(() => { input.style.outline = ''; input.title = ''; }, 2500);
  }

  function bindDate(field, inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    input.addEventListener('change', () => {
      const payload = {};
      payload[field] = input.value || null;
      fetch(`/api/reading/book/${input.dataset.bookId}/dates`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then(r => r.json())
        .then(data => {
          if (!data.success) {
            flashDateFeedback(input, false, data.error || 'Save failed');
          } else {
            flashDateFeedback(input, true, 'Saved');
            // Update the corresponding timeline entry visually
            const eventClass = field === 'started_at' ? 'r-tl-event-started' : 'r-tl-event-finished';
            const timeline = document.getElementById('journal-timeline');
            if (timeline) {
              const eventSpan = timeline.querySelector('.' + eventClass);
              if (eventSpan) {
                const dateSpan = eventSpan.parentElement.querySelector('.r-tl-date');
                if (dateSpan) {
                  if (input.value) {
                    const formatted = new Date(input.value + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
                    dateSpan.textContent = formatted;
                  } else {
                    // Date cleared — remove synthetic timeline items (no journal ID)
                    const tlItem = eventSpan.closest('.r-tl-item');
                    if (tlItem && !tlItem.dataset.journalId) {
                      tlItem.style.transition = 'opacity 0.3s';
                      tlItem.style.opacity = '0';
                      setTimeout(() => tlItem.remove(), 300);
                    }
                  }
                }
              }
            }
            // Mark HC sync button as stale
            const syncBtn = document.getElementById('hc-date-sync');
            if (syncBtn) syncBtn.classList.add('r-hc-date-sync-btn--stale');
          }
        })
        .catch(() => {
          flashDateFeedback(input, false, 'Save failed');
        });
    });
  }
  bindDate('started_at', 'started-at');
  bindDate('finished_at', 'finished-at');

  // ── Sync Dates to Hardcover button ──
  const hcSyncBtn = document.getElementById('hc-date-sync');
  const hcSyncStatus = document.getElementById('hc-date-sync-status');
  if (hcSyncBtn) {
    hcSyncBtn.addEventListener('click', () => {
      hcSyncBtn.disabled = true;
      if (hcSyncStatus) { hcSyncStatus.hidden = true; }
      fetch(`/api/reading/book/${hcSyncBtn.dataset.bookId}/dates/sync-hardcover`, {
        method: 'POST',
      })
        .then(r => {
          if (!r.ok) return r.json().then(data => { throw data; });
          return r.json();
        })
        .then(data => {
          hcSyncBtn.disabled = false;
          if (data.success) hcSyncBtn.classList.remove('r-hc-date-sync-btn--stale');
          if (hcSyncStatus) {
            hcSyncStatus.hidden = false;
            hcSyncStatus.className = `r-hc-date-sync-status ${data.success ? 'success' : 'error'}`;
            hcSyncStatus.textContent = data.success ? 'Dates synced to Hardcover' : (data.error || 'Sync failed');
            setTimeout(() => { hcSyncStatus.hidden = true; }, 4000);
          }
        })
        .catch(err => {
          hcSyncBtn.disabled = false;
          if (hcSyncStatus) {
            hcSyncStatus.hidden = false;
            hcSyncStatus.className = 'r-hc-date-sync-status error';
            hcSyncStatus.textContent = (err && err.error) || 'Sync failed';
            setTimeout(() => { hcSyncStatus.hidden = true; }, 4000);
          }
        });
    });
  }

  // ── Pull Dates from Hardcover button ──
  const hcPullBtn = document.getElementById('hc-date-pull');
  const hcPullStatus = document.getElementById('hc-date-pull-status');
  if (hcPullBtn) {
    hcPullBtn.addEventListener('click', () => {
      hcPullBtn.disabled = true;
      if (hcPullStatus) { hcPullStatus.hidden = true; }
      fetch(`/api/reading/book/${hcPullBtn.dataset.bookId}/dates/pull-hardcover`, {
        method: 'POST',
      })
        .then(r => {
          if (!r.ok) return r.json().then(data => { throw data; });
          return r.json();
        })
        .then(data => {
          hcPullBtn.disabled = false;
          if (data.success && data.dates) {
            const startedInput = document.getElementById('started-at');
            const finishedInput = document.getElementById('finished-at');
            if (startedInput && data.dates.started_at) startedInput.value = data.dates.started_at;
            if (finishedInput && data.dates.finished_at) finishedInput.value = data.dates.finished_at;
          }
          if (hcPullStatus) {
            hcPullStatus.hidden = false;
            hcPullStatus.className = `r-hc-date-sync-status ${data.success ? 'success' : 'error'}`;
            hcPullStatus.textContent = data.success ? 'Dates pulled from Hardcover' : (data.error || 'Pull failed');
            setTimeout(() => { hcPullStatus.hidden = true; }, 4000);
          }
        })
        .catch(err => {
          hcPullBtn.disabled = false;
          if (hcPullStatus) {
            hcPullStatus.hidden = false;
            hcPullStatus.className = 'r-hc-date-sync-status error';
            hcPullStatus.textContent = (err && err.error) || 'Pull failed';
            setTimeout(() => { hcPullStatus.hidden = true; }, 4000);
          }
        });
    });
  }

  // ── About This Book description expand/collapse ──
  const descWrap = document.getElementById('about-book-desc-wrap');
  const descMoreBtn = document.getElementById('about-book-more');
  if (descWrap && descMoreBtn) {
    descMoreBtn.addEventListener('click', () => {
      const collapsed = descWrap.classList.toggle('is-collapsed');
      descMoreBtn.textContent = collapsed ? 'Read More' : 'Show Less';
      descMoreBtn.setAttribute('aria-expanded', String(!collapsed));
    });
  }

  // ── Journal ──
  const form = document.getElementById('journal-form');
  if (form) {
    const bookId = form.dataset.bookId;
    const textarea = document.getElementById('journal-entry');
    const timeline = document.getElementById('journal-timeline');
    const submitBtn = document.getElementById('journal-submit');
    const cancelBtn = document.getElementById('journal-cancel');

    function resetJournalForm() {
      form.dataset.editJournalId = '';
      if (submitBtn) submitBtn.textContent = 'Add Note';
      if (cancelBtn) cancelBtn.classList.add('hidden');
      if (textarea) textarea.value = '';
    }

    form.addEventListener('submit', e => {
      e.preventDefault();
      const entry = textarea.value.trim();
      if (!entry) return;
      const editJournalId = form.dataset.editJournalId;
      const method = editJournalId ? 'PATCH' : 'POST';
      const url = editJournalId
        ? `/api/reading/journal/${editJournalId}`
        : `/api/reading/book/${bookId}/journal`;

      fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry }),
      })
        .then(r => {
          if (!r.ok) throw new Error('Failed to save');
          return r.json();
        })
        .then(data => {
          if (!data.success) return;
          if (timeline) {
            if (editJournalId) {
              const item = timeline.querySelector(`.r-tl-item[data-journal-id="${editJournalId}"]`);
              const text = item?.querySelector('.r-tl-text');
              if (text) renderJournalEntry(text, data.journal);
              if (item) item.dataset.entry = data.journal.entry;
            } else {
              const empty = timeline.querySelector('.r-journal-empty');
              if (empty) empty.remove();
              timeline.prepend(buildJournalNode(data.journal));
            }
          }
          resetJournalForm();
        })
        .catch(err => {
          console.warn('Journal save failed:', err);
        });
    });

    if (cancelBtn) cancelBtn.addEventListener('click', () => {
      resetJournalForm();
    });

    // Delete (event delegation)
    if (timeline) timeline.addEventListener('click', e => {
      const trigger = e.target.closest('.r-tl-menu-trigger');
      if (trigger) {
        const menu = trigger.closest('.r-tl-menu');
        const isOpen = menu.classList.contains('open');
        timeline.querySelectorAll('.r-tl-menu.open').forEach(item => {
          item.classList.remove('open');
          const btn = item.querySelector('.r-tl-menu-trigger');
          if (btn) btn.setAttribute('aria-expanded', 'false');
        });
        if (!isOpen) {
          menu.classList.add('open');
          trigger.setAttribute('aria-expanded', 'true');
        }
        return;
      }

      const actionBtn = e.target.closest('.r-tl-menu-item');
      if (!actionBtn) return;
      const menu = actionBtn.closest('.r-tl-menu');
      if (!menu) return;
      const journalId = menu.dataset.journalId;
      const eventType = menu.dataset.journalEvent;
      const item = menu.closest('.r-tl-item');

      if (actionBtn.dataset.action === 'edit') {
        if (eventType === 'started' || eventType === 'finished') {
          // Focus the corresponding top date input for editing
          const inputId = eventType === 'started' ? 'started-at' : 'finished-at';
          const dateInput = document.getElementById(inputId);
          if (dateInput) {
            dateInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
            setTimeout(() => { dateInput.focus(); dateInput.showPicker?.(); }, 350);
          }
          menu.classList.remove('open');
          const triggerBtn = menu.querySelector('.r-tl-menu-trigger');
          if (triggerBtn) triggerBtn.setAttribute('aria-expanded', 'false');
          return;
        }
        if (eventType !== 'note') return;
        const text = item?.dataset.entry || '';
        form.dataset.editJournalId = journalId;
        textarea.value = text;
        if (submitBtn) submitBtn.textContent = 'Save Note';
        if (cancelBtn) cancelBtn.classList.remove('hidden');
        textarea.focus();
        menu.classList.remove('open');
        const triggerBtn = menu.querySelector('.r-tl-menu-trigger');
        if (triggerBtn) triggerBtn.setAttribute('aria-expanded', 'false');
        form.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
      }

      if (actionBtn.dataset.action !== 'delete') return;
      if (typeof showJournalDeleteConfirm === 'function') {
        showJournalDeleteConfirm(journalId);
      }
      menu.classList.remove('open');
      const triggerBtn2 = menu.querySelector('.r-tl-menu-trigger');
      if (triggerBtn2) triggerBtn2.setAttribute('aria-expanded', 'false');
    });

    document.addEventListener('click', e => {
      if (!e.target.closest('.r-tl-menu')) {
        timeline.querySelectorAll('.r-tl-menu.open').forEach(item => {
          item.classList.remove('open');
          const btn = item.querySelector('.r-tl-menu-trigger');
          if (btn) btn.setAttribute('aria-expanded', 'false');
        });
      }
    });
  }

}


function renderJournalEntry(container, journal) {
  if (!container) return;
  if (journal.entry_html) {
    container.innerHTML = journal.entry_html;
    return;
  }
  container.textContent = journal.entry || '';
}


/** Build a journal timeline node using safe DOM methods. */
function buildJournalNode(j) {
  const item = document.createElement('div');
  item.className = 'r-tl-item';
  item.dataset.journalId = j.id;
  item.dataset.entry = j.entry || '';

  const line = document.createElement('div');
  line.className = 'r-tl-line';
  item.appendChild(line);

  const dot = document.createElement('div');
  dot.className = `r-tl-dot r-tl-dot-${j.event || 'note'}`;
  item.appendChild(dot);

  const body = document.createElement('div');
  body.className = 'r-tl-body';

  const head = document.createElement('div');
  head.className = 'r-tl-head';

  const evtSpan = document.createElement('span');
  const eventName = j.event || 'note';
  evtSpan.className = `r-tl-event r-tl-event-${eventName}`;
  evtSpan.textContent = eventName.charAt(0).toUpperCase() + eventName.slice(1);
  head.appendChild(evtSpan);

  if (j.created_at) {
    const dateSpan = document.createElement('span');
    dateSpan.className = 'r-tl-date';
    dateSpan.textContent = new Date(j.created_at).toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric'
    });
    head.appendChild(dateSpan);
  }

  if (j.percentage != null) {
    const pct = document.createElement('span');
    pct.className = 'r-tl-pct';
    pct.textContent = Math.round(j.percentage * 100) + '%';
    head.appendChild(pct);
  }

  body.appendChild(head);

  if (j.entry) {
    const text = document.createElement('div');
    text.className = 'r-tl-text';
    renderJournalEntry(text, j);
    body.appendChild(text);
  }

  if (eventName === 'note' || eventName === 'highlight') {
    const menu = document.createElement('div');
    menu.className = 'r-tl-menu';
    menu.dataset.journalId = j.id;
    menu.dataset.journalEvent = eventName;

    const trigger = document.createElement('button');
    trigger.className = 'r-tl-menu-trigger';
    trigger.type = 'button';
    trigger.title = 'More actions';
    trigger.setAttribute('aria-label', 'More actions');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.textContent = '\u22ef';
    menu.appendChild(trigger);

    const dropdown = document.createElement('div');
    dropdown.className = 'r-tl-menu-dropdown';

    if (eventName === 'note') {
      const edit = document.createElement('button');
      edit.className = 'r-tl-menu-item';
      edit.type = 'button';
      edit.dataset.action = 'edit';
      edit.textContent = 'Edit';
      dropdown.appendChild(edit);
    }

    const del = document.createElement('button');
    del.className = 'r-tl-menu-item r-tl-menu-item--danger';
    del.type = 'button';
    del.dataset.action = 'delete';
    del.textContent = 'Delete';
    dropdown.appendChild(del);

    menu.appendChild(dropdown);
    body.appendChild(menu);
  }

  item.appendChild(body);
  return item;
}


/* ═══════════════════════════════════════════
   TBR (Want to Read) Tab
   ═══════════════════════════════════════════ */

function initTbrTab(hcConfigured) {
  const grid = document.getElementById('tbr-grid');
  const emptyState = document.getElementById('tbr-empty');
  if (!grid) return;

  let _searchTimer = null;
  let _activeProvider = hcConfigured ? 'hardcover' : 'open_library';
  const _itemsById = {};  // TBR item lookup map

  /** Replace all children of el with a single text message paragraph. */
  function setStatusMessage(el, text, color) {
    while (el.firstChild) el.removeChild(el.firstChild);
    const p = document.createElement('p');
    p.style.cssText = 'font-size: 13px; padding: 8px; color: ' + (color || 'var(--color-text-muted)');
    p.textContent = text;
    el.appendChild(p);
  }

  // ── Load TBR items ──
  function loadTbrItems() {
    fetch('/api/reading/tbr')
      .then(r => r.json())
      .then(items => {
        grid.querySelectorAll('.r-tbr-card, .r-tbr-section-label, .r-tbr-section-divider, .r-tbr-section-hint').forEach(c => c.remove());
        // Rebuild lookup map
        Object.keys(_itemsById).forEach(k => delete _itemsById[k]);
        items.forEach(item => { _itemsById[item.id] = item; });

        if (!items.length) {
          if (emptyState) emptyState.hidden = false;
          updateTabBadge(0);
          return;
        }
        if (emptyState) emptyState.hidden = true;

        // Split into Up Next vs normal
        const upNext = items.filter(i => i.priority > 0);
        const rest = items.filter(i => !i.priority);

        const upNextLabel = document.createElement('div');
        upNextLabel.className = 'r-tbr-section-label';
        upNextLabel.textContent = upNext.length ? 'Up Next (' + upNext.length + ')' : 'Up Next';
        grid.appendChild(upNextLabel);

        if (upNext.length) {
          upNext.forEach(item => grid.appendChild(buildTbrCard(item)));
        } else {
          const hint = document.createElement('div');
          hint.className = 'r-tbr-section-hint';
          hint.textContent = 'Bookmark books to add them here';
          grid.appendChild(hint);
        }

        if (rest.length) {
          const divider = document.createElement('div');
          divider.className = 'r-tbr-section-divider';
          grid.appendChild(divider);

          const restLabel = document.createElement('div');
          restLabel.className = 'r-tbr-section-label';
          restLabel.textContent = 'Everything Else (' + rest.length + ')';
          grid.appendChild(restLabel);
        }
        rest.forEach(item => grid.appendChild(buildTbrCard(item)));
        updateTabBadge(items.length);

        // Kick off background enrichment if any items lack metadata
        const needsEnrich = items.some(i => !i.description && (i.hardcover_book_id || i.ol_work_key));
        if (needsEnrich) runBackfillEnrichment();
      })
      .catch(() => showToast('Failed to load TBR list'));
  }

  function runBackfillEnrichment() {
    fetch('/api/reading/tbr/enrich', { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        if (data.success && data.enriched > 0) {
          // Reload to show enriched data
          loadTbrItems();
        }
      })
      .catch(e => console.warn('TBR enrichment failed:', e));
  }

  function updateTabBadge(count) {
    const tab = document.getElementById('tab-tbr');
    if (!tab) return;
    let badge = tab.querySelector('.r-tab-badge');
    if (count > 0) {
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'r-tab-badge';
        tab.appendChild(badge);
      }
      badge.textContent = count;
    } else if (badge) {
      badge.remove();
    }
  }

  function buildTbrCard(item) {
    const card = document.createElement('div');
    card.className = 'r-tbr-card' + (item.priority ? ' r-tbr-card--upnext' : '');
    card.dataset.tbrId = item.id;

    const cover = document.createElement('div');
    cover.className = 'r-tbr-card-cover';
    if (item.cover_url) {
      const img = document.createElement('img');
      img.src = item.cover_url;
      img.alt = '';
      img.loading = 'lazy';
      img.onerror = function() { this.style.display = 'none'; };
      cover.appendChild(img);
    } else {
      const empty = document.createElement('div');
      empty.className = 'r-tbr-card-cover-empty';
      empty.textContent = '\u{1F4D6}';
      cover.appendChild(empty);
    }

    // Up Next bookmark toggle
    const bookmarkBtn = document.createElement('button');
    bookmarkBtn.className = 'r-tbr-upnext-toggle' + (item.priority ? ' r-tbr-upnext-toggle--active' : '');
    bookmarkBtn.type = 'button';
    bookmarkBtn.title = item.priority ? 'Remove from Up Next' : 'Mark as Up Next';
    const bSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    bSvg.setAttribute('viewBox', '0 0 16 16');
    bSvg.setAttribute('width', '16');
    bSvg.setAttribute('height', '16');
    if (item.priority) {
      bSvg.setAttribute('fill', 'currentColor');
      bSvg.removeAttribute('stroke');
    } else {
      bSvg.setAttribute('fill', 'none');
      bSvg.setAttribute('stroke', 'currentColor');
      bSvg.setAttribute('stroke-width', '1.5');
    }
    const bPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    bPath.setAttribute('d', 'M4 2h8v12l-4-3-4 3V2z');
    bSvg.appendChild(bPath);
    bookmarkBtn.appendChild(bSvg);
    bookmarkBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      const newPriority = item.priority ? 0 : 1;
      fetch('/api/reading/tbr/' + item.id, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ priority: newPriority }),
      })
        .then(r => r.json())
        .then(data => { if (data.success) loadTbrItems(); })
        .catch(() => showToast('Failed to update priority'));
    });
    cover.appendChild(bookmarkBtn);

    card.appendChild(cover);

    const body = document.createElement('div');
    body.className = 'r-tbr-card-body';

    const title = document.createElement('div');
    title.className = 'r-tbr-card-title';
    title.textContent = item.title;
    title.title = item.title;
    body.appendChild(title);

    if (item.author) {
      const author = document.createElement('div');
      author.className = 'r-tbr-card-author';
      author.textContent = item.author;
      body.appendChild(author);
    }

    // Compact info row: rating, pages, year
    const infoParts = [];
    if (item.rating != null) infoParts.push({ star: true, text: Number(item.rating).toFixed(1) });
    if (item.page_count) infoParts.push({ text: item.page_count + 'p' });
    if (item.release_year) infoParts.push({ text: String(item.release_year) });
    if (infoParts.length) {
      const infoRow = document.createElement('div');
      infoRow.className = 'r-tbr-card-info';
      infoParts.forEach(p => {
        const span = document.createElement('span');
        if (p.star) {
          const s = document.createElement('span');
          s.className = 'r-tbr-card-info-star';
          s.textContent = '\u2605';
          span.appendChild(s);
          span.appendChild(document.createTextNode(' ' + p.text));
        } else {
          span.textContent = p.text;
        }
        infoRow.appendChild(span);
      });
      body.appendChild(infoRow);
    }

    // Genre pills (first 2)
    const genres = item.genres || [];
    if (genres.length) {
      const genreRow = document.createElement('div');
      genreRow.className = 'r-tbr-card-genres';
      genres.slice(0, 2).forEach(g => {
        const pill = document.createElement('span');
        pill.className = 'r-tbr-card-genre-pill';
        pill.textContent = g;
        pill.title = g;
        genreRow.appendChild(pill);
      });
      body.appendChild(genreRow);
    }

    const meta = document.createElement('div');
    meta.className = 'r-tbr-card-meta';

    if (item.book_abs_id) {
      const lib = document.createElement('span');
      lib.className = 'r-tbr-badge r-tbr-badge--library';
      lib.textContent = 'In Library';
      meta.appendChild(lib);
    }

    const sourceBadge = document.createElement('span');
    sourceBadge.className = 'r-tbr-badge';
    const sourceLabels = {
      manual: 'Manual',
      open_library: 'Open Library',
      hardcover_search: 'Hardcover',
      hardcover_wtr: 'Want to Read',
      hardcover_list: item.hardcover_list_name || 'HC List',
    };
    sourceBadge.textContent = sourceLabels[item.source] || item.source;
    meta.appendChild(sourceBadge);
    body.appendChild(meta);
    card.appendChild(body);

    card.style.cursor = 'pointer';
    card.addEventListener('click', () => {
      window.location.href = '/reading/tbr/' + item.id;
    });

    return card;
  }

  // ── Add Book Modal ──
  const addModal = document.getElementById('tbr-add-modal');
  const addBtn = document.getElementById('tbr-add-btn');
  const addClose = document.getElementById('tbr-add-close');
  const searchInput = document.getElementById('tbr-search-input');
  const searchResults = document.getElementById('tbr-search-results');
  const providerBtns = document.querySelectorAll('.r-tbr-provider-btn');

  function showAddModal() { if (addModal) { addModal.style.display = 'flex'; searchInput?.focus(); } }
  function hideAddModal() {
    if (addModal) addModal.style.display = 'none';
    if (searchResults) while (searchResults.firstChild) searchResults.removeChild(searchResults.firstChild);
  }

  if (addBtn) addBtn.addEventListener('click', showAddModal);
  if (addClose) addClose.addEventListener('click', hideAddModal);
  if (addModal) addModal.addEventListener('click', e => { if (e.target === addModal) hideAddModal(); });

  providerBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      providerBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _activeProvider = btn.dataset.provider;
      if (searchInput && searchInput.value.trim().length >= 2) {
        doSearch(searchInput.value.trim());
      }
    });
  });

  if (searchInput) {
    searchInput.addEventListener('input', () => {
      clearTimeout(_searchTimer);
      const q = searchInput.value.trim();
      if (q.length < 2) {
        if (searchResults) while (searchResults.firstChild) searchResults.removeChild(searchResults.firstChild);
        return;
      }
      _searchTimer = setTimeout(() => doSearch(q), 350);
    });
  }

  function doSearch(query) {
    if (searchResults) setStatusMessage(searchResults, 'Searching...');

    fetch('/api/reading/tbr/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, provider: _activeProvider }),
    })
      .then(r => r.json())
      .then(data => {
        if (!searchResults) return;
        while (searchResults.firstChild) searchResults.removeChild(searchResults.firstChild);
        if (!data.results || !data.results.length) {
          setStatusMessage(searchResults, 'No results found.', 'var(--color-text-faint)');
          return;
        }
        data.results.forEach(result => {
          searchResults.appendChild(buildSearchResult(result));
        });
      })
      .catch(() => {
        if (searchResults) setStatusMessage(searchResults, 'Search failed.', 'var(--color-error)');
      });
  }

  function buildSearchResult(result) {
    const el = document.createElement('div');
    el.className = 'r-tbr-result';

    const cover = document.createElement('div');
    cover.className = 'r-tbr-result-cover';
    if (result.cover_url) {
      const img = document.createElement('img');
      img.src = result.cover_url;
      img.alt = '';
      img.loading = 'lazy';
      cover.appendChild(img);
    }
    el.appendChild(cover);

    const info = document.createElement('div');
    info.className = 'r-tbr-result-info';

    const title = document.createElement('div');
    title.className = 'r-tbr-result-title';
    title.textContent = result.title;
    info.appendChild(title);

    if (result.author) {
      const author = document.createElement('div');
      author.className = 'r-tbr-result-author';
      author.textContent = result.author;
      info.appendChild(author);
    }

    el.appendChild(info);

    el.addEventListener('click', () => {
      addFromSearchResult(result, el);
    });

    return el;
  }

  function addFromSearchResult(result, el) {
    fetch('/api/reading/tbr/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result),
    })
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          const added = document.createElement('div');
          added.className = 'r-tbr-result-added';
          added.textContent = data.created ? 'Added!' : 'Already on list';
          el.querySelector('.r-tbr-result-info')?.appendChild(added);
          el.style.pointerEvents = 'none';
          el.style.opacity = '0.6';
          loadTbrItems();
        }
      })
      .catch(() => showToast('Failed to add book'));
  }

  // ── Manual entry ──
  const manualToggle = document.getElementById('tbr-manual-toggle');
  const manualForm = document.getElementById('tbr-manual-form');
  const manualAdd = document.getElementById('tbr-manual-add');

  if (manualToggle && manualForm) {
    manualToggle.addEventListener('click', () => {
      manualForm.hidden = !manualForm.hidden;
      manualToggle.textContent = manualForm.hidden ? 'Or add manually' : 'Hide manual entry';
    });
  }

  if (manualAdd) {
    manualAdd.addEventListener('click', () => {
      const title = document.getElementById('tbr-manual-title')?.value.trim();
      if (!title) return;
      const author = document.getElementById('tbr-manual-author')?.value.trim();
      const notes = document.getElementById('tbr-manual-notes')?.value.trim();

      fetch('/api/reading/tbr/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, author, notes }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            hideAddModal();
            loadTbrItems();
            showToast(data.created ? 'Added to list!' : 'Already on list');
          }
        })
        .catch(() => showToast('Failed to add book'));
    });
  }

  // ── Hardcover Import ──
  const importBtn = document.getElementById('tbr-import-btn');
  const importDropdown = document.getElementById('tbr-import-dropdown');
  const importWtr = document.getElementById('tbr-import-wtr');
  const importList = document.getElementById('tbr-import-list');

  if (importBtn && importDropdown) {
    importBtn.addEventListener('click', () => {
      importDropdown.classList.toggle('open');
    });
    document.addEventListener('click', e => {
      if (!e.target.closest('.r-tbr-import-menu')) {
        importDropdown.classList.remove('open');
      }
    });
  }

  if (importWtr) {
    importWtr.addEventListener('click', () => {
      importDropdown?.classList.remove('open');
      importWtr.textContent = 'Importing...';
      importWtr.disabled = true;
      fetch('/api/reading/tbr/import-hardcover', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          importWtr.textContent = 'Import Want to Read';
          importWtr.disabled = false;
          if (data.success) {
            showToast('Imported ' + data.imported + ' books (' + data.skipped + ' already on list)');
            loadTbrItems();
          } else {
            showToast(data.error || 'Import failed');
          }
        })
        .catch(() => {
          importWtr.textContent = 'Import Want to Read';
          importWtr.disabled = false;
          showToast('Import failed');
        });
    });
  }

  if (importList) {
    importList.addEventListener('click', () => {
      importDropdown?.classList.remove('open');
      showListPicker();
    });
  }

  // ── Hardcover List Picker ──
  const listModal = document.getElementById('tbr-list-modal');
  const listClose = document.getElementById('tbr-list-close');
  const listPicker = document.getElementById('tbr-list-picker');

  function showListPicker() {
    if (!listModal || !listPicker) return;
    listModal.style.display = 'flex';
    setStatusMessage(listPicker, 'Loading lists...');

    fetch('/api/reading/tbr/hardcover-lists')
      .then(r => r.json())
      .then(lists => {
        while (listPicker.firstChild) listPicker.removeChild(listPicker.firstChild);
        if (!lists.length) {
          setStatusMessage(listPicker, 'No lists found on Hardcover.', 'var(--color-text-faint)');
          return;
        }
        lists.forEach(lst => {
          const option = document.createElement('div');
          option.className = 'r-tbr-list-option';

          const name = document.createElement('span');
          name.className = 'r-tbr-list-name';
          name.textContent = lst.name;
          option.appendChild(name);

          const count = document.createElement('span');
          count.className = 'r-tbr-list-count';
          count.textContent = lst.books_count + ' books';
          option.appendChild(count);

          option.addEventListener('click', () => importFromList(lst.id, lst.name));
          listPicker.appendChild(option);
        });
      })
      .catch(() => {
        setStatusMessage(listPicker, 'Failed to load lists.', 'var(--color-error)');
      });
  }

  function hideListModal() { if (listModal) listModal.style.display = 'none'; }

  if (listClose) listClose.addEventListener('click', hideListModal);
  if (listModal) listModal.addEventListener('click', e => { if (e.target === listModal) hideListModal(); });

  function importFromList(listId, listName) {
    hideListModal();
    showToast('Importing from "' + listName + '"...');

    fetch('/api/reading/tbr/import-hardcover-list', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ list_id: listId }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          showToast('Imported ' + data.imported + ' books from "' + data.list_name + '" (' + data.skipped + ' already on list)');
          loadTbrItems();
        } else {
          showToast(data.error || 'Import failed');
        }
      })
      .catch(() => showToast('Import failed'));
  }

  // ── Toast helper ──
  function showToast(message) {
    const existing = document.querySelector('.r-tbr-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = 'r-tbr-toast';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
      toast.style.transition = 'opacity 0.3s';
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  // ── TBR Filter Chips (Want to Read / In Library) ──
  const tbrFilterChips = document.querySelectorAll('[data-tbr-filter]');
  let activeTbrFilter = 'all';

  function applyTbrFilter() {
    const tbrGrid = document.getElementById('tbr-grid');
    const librarySection = document.getElementById('library-backlog-section');
    if (activeTbrFilter === 'all') {
      if (tbrGrid) tbrGrid.style.display = '';
      if (librarySection) librarySection.style.display = '';
    } else if (activeTbrFilter === 'want') {
      if (tbrGrid) tbrGrid.style.display = '';
      if (librarySection) librarySection.style.display = 'none';
    } else if (activeTbrFilter === 'library') {
      if (tbrGrid) tbrGrid.style.display = 'none';
      if (librarySection) librarySection.style.display = '';
    }
  }

  tbrFilterChips.forEach(chip => {
    chip.addEventListener('click', () => {
      tbrFilterChips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      activeTbrFilter = chip.dataset.tbrFilter;
      applyTbrFilter();
    });
  });

  // ── Library backlog card actions ──
  document.querySelectorAll('.r-library-start-btn').forEach(btn => {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      const bookId = this.dataset.bookId;
      this.disabled = true;
      this.textContent = 'Starting...';
      fetch('/api/reading/book/' + encodeURIComponent(bookId) + '/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'active' }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            window.location.href = '/reading/book/' + encodeURIComponent(bookId);
          } else {
            showToast(data.error || 'Failed to start reading');
            this.disabled = false;
            this.textContent = 'Start Reading';
          }
        })
        .catch(() => {
          showToast('Failed to start reading');
          this.disabled = false;
          this.textContent = 'Start Reading';
        });
    });
  });

  document.querySelectorAll('.r-library-tbr-btn').forEach(btn => {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      const bookId = this.dataset.bookId;
      this.disabled = true;
      this.textContent = 'Adding...';
      fetch('/api/reading/tbr/from-library', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ abs_id: bookId }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            this.textContent = 'Added';
            showToast(data.created ? 'Added to Want to Read' : 'Already on list');
            loadTbrItems();
          } else {
            showToast(data.error || 'Failed to add');
            this.disabled = false;
            this.textContent = '+ Want to Read';
          }
        })
        .catch(() => {
          showToast('Failed to add');
          this.disabled = false;
          this.textContent = '+ Want to Read';
        });
    });
  });

  // ── Load on tab activation ──
  let _loaded = false;
  const tbrTab = document.getElementById('tab-tbr');
  if (tbrTab) {
    tbrTab.addEventListener('click', () => {
      if (!_loaded) {
        loadTbrItems();
        _loaded = true;
      }
    });
    // If TBR tab is already active on page load (e.g. /reading/tbr), load immediately
    if (tbrTab.classList.contains('active')) {
      loadTbrItems();
      _loaded = true;
    }
  }
}
