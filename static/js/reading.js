/* PageKeeper — Reading Tab */

function initReadingPage(currentYear) {
  const searchInput = document.getElementById('reading-search');

  if (searchInput) {
    searchInput.addEventListener('input', () => {
      const term = searchInput.value.toLowerCase();
      document.querySelectorAll('.r-book-card').forEach(card => {
        const title = (card.dataset.title || '').toLowerCase();
        card.style.display = title.includes(term) ? '' : 'none';
      });
      // Hide empty sections
      document.querySelectorAll('.r-section').forEach(section => {
        const visible = section.querySelectorAll('.r-book-card:not([style*="display: none"])');
        section.style.display = visible.length ? '' : 'none';
      });
    });
  }

  // Goal modal
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
      const target = parseInt(goalInput?.value, 10);
      if (!target || target < 1) return;
      fetch(`/api/reading/goal/${currentYear}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_books: target }),
      })
        .then(r => r.json())
        .then(data => { if (data.success) window.location.reload(); });
    });
  }
}


function initReadingDetail() {
  // ── Rating stars ──
  const rc = document.getElementById('rating-stars');
  if (rc) {
    const absId = rc.dataset.absId;
    const stars = rc.querySelectorAll('.r-star-btn');
    const label = document.getElementById('rating-label');

    stars.forEach(star => {
      star.addEventListener('click', () => {
        const value = parseInt(star.dataset.value, 10);
        fetch(`/api/reading/book/${absId}/rating`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rating: value }),
        })
          .then(r => r.json())
          .then(data => {
            if (data.success) {
              stars.forEach((s, i) => {
                s.classList.toggle('filled', i + 1 <= data.rating);
                s.classList.remove('half');
              });
              if (label) label.textContent = data.rating + '/5';
            }
          });
      });
    });
  }

  // ── Date fields ──
  function bindDate(field, inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    input.addEventListener('change', () => {
      const payload = {};
      payload[field] = input.value || null;
      fetch(`/api/reading/book/${input.dataset.absId}/dates`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    });
  }
  bindDate('started_at', 'started-at');
  bindDate('finished_at', 'finished-at');

  // ── Journal ──
  const form = document.getElementById('journal-form');
  if (form) {
    const absId = form.dataset.absId;
    const textarea = document.getElementById('journal-entry');
    const timeline = document.getElementById('journal-timeline');

    form.addEventListener('submit', e => {
      e.preventDefault();
      const entry = textarea.value.trim();
      if (!entry) return;

      fetch(`/api/reading/book/${absId}/journal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry }),
      })
        .then(r => r.json())
        .then(data => {
          if (!data.success) return;
          textarea.value = '';

          const empty = timeline.querySelector('.r-journal-empty');
          if (empty) empty.remove();

          timeline.prepend(buildJournalNode(data.journal));
        });
    });

    // Delete (event delegation)
    timeline.addEventListener('click', e => {
      const btn = e.target.closest('.r-tl-delete');
      if (!btn) return;
      fetch(`/api/reading/journal/${btn.dataset.journalId}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            const item = btn.closest('.r-tl-item');
            if (item) {
              item.style.transition = 'opacity 0.3s';
              item.style.opacity = '0';
              setTimeout(() => item.remove(), 300);
            }
          }
        });
    });
  }
}


/** Build a journal timeline node using safe DOM methods. */
function buildJournalNode(j) {
  const item = document.createElement('div');
  item.className = 'r-tl-item';
  item.dataset.journalId = j.id;

  const line = document.createElement('div');
  line.className = 'r-tl-line';
  item.appendChild(line);

  const dot = document.createElement('div');
  dot.className = 'r-tl-dot r-tl-dot-note';
  item.appendChild(dot);

  const body = document.createElement('div');
  body.className = 'r-tl-body';

  const head = document.createElement('div');
  head.className = 'r-tl-head';

  const evtSpan = document.createElement('span');
  evtSpan.className = 'r-tl-event r-tl-event-note';
  evtSpan.textContent = 'Note';
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
    const text = document.createElement('p');
    text.className = 'r-tl-text';
    text.textContent = j.entry;
    body.appendChild(text);
  }

  const del = document.createElement('button');
  del.className = 'r-tl-delete';
  del.dataset.journalId = j.id;
  del.title = 'Delete';
  del.textContent = '\u00D7';
  body.appendChild(del);

  item.appendChild(body);
  return item;
}
