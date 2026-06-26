var migrationBooks = [];
var migrationManualMatches = {};
var migrationActiveMatchAbsId = null;

function migrationOptions() {
  return {
    carry_listening_sessions: document.getElementById("mig-sessions").checked,
    carry_bookmarks: document.getElementById("mig-bookmarks").checked,
    mark_ebook_as_read: document.getElementById("mig-ebook-read").checked,
    manual_matches: migrationManualMatches,
  };
}

function migrationEscape(s) {
  var d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function migrationJsArg(value) {
  return migrationEscape(JSON.stringify(String(value == null ? "" : value)));
}

function migrationText(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback || "";
  return String(value);
}

function migrationMatchLabel(matchedBy) {
  var labels = {
    isbn: "ISBN",
    asin: "ASIN",
    title_author: "Title + author",
    title: "Title",
    manual: "Manual match",
  };
  return labels[matchedBy] || migrationText(matchedBy, "Unknown");
}

function migrationTypeLabel(type) {
  var value = migrationText(type, "Unknown").toUpperCase();
  if (value === "AUDIOBOOK") return "Audiobook";
  if (value === "EPUB") return "EPUB";
  if (value === "PDF") return "PDF";
  if (value === "CBX") return "CBX";
  return value;
}

function migrationUpdateCount() {
  var checked = document.querySelectorAll(".mig-select:checked").length;
  var btn = document.getElementById("mig-migrate-btn");
  btn.textContent = "Migrate selected (" + checked + ")";
  btn.disabled = checked === 0;
}

function migrationSelectAll(state) {
  var boxes = document.querySelectorAll(".mig-select");
  for (var i = 0; i < boxes.length; i++) boxes[i].checked = state;
  migrationUpdateCount();
}

function migrationSearchText(b) {
  return [
    b.title,
    b.author,
    b.grimmory_title,
    b.grimmory_authors,
    b.grimmory_file_name,
    b.grimmory_ebook_title,
    b.matched_by,
    b.grimmory_book_type,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function migrationNoteHtml(b) {
  var notes = [];
  if (b.replay_note) {
    notes.push(
      "Sessions/bookmarks skipped: Grimmory match is " +
        migrationTypeLabel(b.grimmory_book_type) +
        ", not audiobook.",
    );
  }
  if (b.grimmory_ebook_source === "matched_record") {
    notes.push("Ebook read uses the matched " + migrationTypeLabel(b.grimmory_book_type) + " record.");
  } else if (b.grimmory_ebook_title) {
    notes.push("Ebook also: " + b.grimmory_ebook_title);
  } else if (b.grimmory_ebook_id === null || b.ebook_note) {
    notes.push(b.ebook_note || "No separate ebook record found.");
  }
  if (b.manual_match_error) notes.push(b.manual_match_error);
  if (!notes.length) return "";
  return (
    '<div class="mig-note-list">' +
    notes
      .map(function (note) {
        return "<span>" + migrationEscape(note) + "</span>";
      })
      .join("") +
    "</div>"
  );
}

function migrationSourceChips(b) {
  var chips = ['<span class="mig-chip mig-chip--quiet">Audiobookshelf</span>'];
  if (b.finished_at) {
    chips.push('<span class="mig-chip mig-chip--quiet">Finished ' + migrationEscape(b.finished_at) + "</span>");
  }
  return '<div class="mig-chip-row mig-source-chips">' + chips.join("") + "</div>";
}

function migrationMatchPane(b) {
  if (!b.grimmory_book_id) {
    return (
      '<div class="mig-match-summary">' +
      '<span class="mig-match-label">Grimmory</span>' +
      '<div class="mig-match-content">' +
      '<span class="mig-match-title">No match selected</span>' +
      '<span class="mig-match-sub">Search Grimmory to choose a target.</span>' +
      "</div>" +
      "</div>"
    );
  }

  var type = migrationTypeLabel(b.grimmory_book_type);
  var chips = [
    '<span class="mig-chip">' + migrationEscape(type) + "</span>",
    '<span class="mig-chip">Matched by ' + migrationEscape(migrationMatchLabel(b.matched_by)) + "</span>",
  ];
  if (b.manual_match) chips.unshift('<span class="mig-chip mig-chip--manual">Manual</span>');
  var sub = [b.grimmory_authors, b.grimmory_file_name].filter(Boolean).join(" · ");

  return (
    '<div class="mig-match-summary">' +
    '<span class="mig-match-label">Grimmory</span>' +
    '<div class="mig-match-content">' +
    '<div class="mig-match-line"><span class="mig-match-title">' +
    migrationEscape(migrationText(b.grimmory_title, "Untitled")) +
    '</span><span class="mig-chip-row">' +
    chips.join("") +
    "</span></div>" +
    '<div class="mig-match-sub">' +
    migrationEscape(sub || migrationText(b.grimmory_instance_id, "default")) +
    "</div>" +
    "</div>" +
    "</div>"
  );
}

function migrationRowActions(b, bucket) {
  var label = b.grimmory_book_id ? "Change match" : "Find match";
  var html =
    '<div class="mig-row-actions">' +
    '<button type="button" class="btn btn-secondary btn-sm" onclick="migrationOpenMatchModal(' +
    migrationJsArg(b.abs_id) +
    ')">' +
    label +
    "</button>";
  if (b.manual_match || migrationManualMatches[String(b.abs_id)]) {
    html +=
      '<button type="button" class="btn btn-secondary btn-sm" onclick="migrationClearManualMatch(' +
      migrationJsArg(b.abs_id) +
      ')">Use auto match</button>';
  }
  if (bucket === "already_migrated" && b.migrated_outcome) {
    html +=
      '<span class="mig-chip mig-chip--quiet">' +
      migrationEscape(b.migrated_outcome) +
      (b.migrated_at ? " " + migrationEscape(b.migrated_at.slice(0, 10)) : "") +
      "</span>";
  }
  html += "</div>";
  return html;
}

function migrationRenderCard(b, bucket) {
  var selectable = bucket === "will_migrate";
  var checkbox = selectable
    ? '<input type="checkbox" class="mig-select" checked data-abs-id="' +
      migrationEscape(b.abs_id) +
      '">'
    : "";
  return (
    '<article class="mig-card mig-row" data-search="' +
    migrationEscape(migrationSearchText(b)) +
    '">' +
    '<div class="mig-card-check">' +
    checkbox +
    "</div>" +
    '<div class="mig-card-main">' +
    '<div class="mig-card-head"><div class="mig-card-title-block">' +
    '<div class="mig-book-line"><span class="mig-title">' +
    migrationEscape(migrationText(b.title, "Untitled")) +
    '</span><span class="mig-author">' +
    migrationEscape(migrationText(b.author, "Unknown author")) +
    "</span></div>" +
    migrationSourceChips(b) +
    "</div>" +
    migrationRowActions(b, bucket) +
    "</div>" +
    '<div class="mig-meta-grid">' +
    migrationMatchPane(b) +
    "</div>" +
    migrationNoteHtml(b) +
    "</div>" +
    "</article>"
  );
}

function migrationRenderSection(title, note, rows, bucket) {
  if (!rows.length) return "";
  return (
    '<section class="mig-section" data-bucket="' +
    migrationEscape(bucket) +
    '">' +
    '<div class="mig-section-header"><h3 class="mig-section-title">' +
    migrationEscape(title) +
    " (" +
    rows.length +
    ')</h3><span class="mig-section-note">' +
    migrationEscape(note || "") +
    "</span></div>" +
    '<div class="mig-card-list">' +
    rows
      .map(function (b) {
        return migrationRenderCard(b, bucket);
      })
      .join("") +
    "</div>" +
    "</section>"
  );
}

function migrationRenderBuckets(books) {
  migrationBooks = books || [];
  var groups = { will_migrate: [], already_migrated: [], already_read: [], unmatched: [] };
  migrationBooks.forEach(function (b) {
    if (groups[b.bucket]) groups[b.bucket].push(b);
  });

  var html = "";
  html += migrationRenderSection(
    "Will migrate",
    "Selected rows will be included in the next run.",
    groups.will_migrate,
    "will_migrate",
  );
  html += migrationRenderSection(
    "Needs a match",
    "Choose a Grimmory record before migrating these.",
    groups.unmatched,
    "unmatched",
  );
  html += migrationRenderSection(
    "Already read",
    "Skipped to avoid duplicating sessions or bookmarks.",
    groups.already_read,
    "already_read",
  );
  html += migrationRenderSection(
    "Already migrated",
    "Audit rows already exist for these matches.",
    groups.already_migrated,
    "already_migrated",
  );

  document.getElementById("mig-buckets").innerHTML =
    html || '<div class="mig-empty">No finished Audiobookshelf books found.</div>';

  var boxes = document.querySelectorAll(".mig-select");
  for (var i = 0; i < boxes.length; i++)
    boxes[i].addEventListener("change", migrationUpdateCount);
  migrationApplyFilter();
}

function migrationApplyFilter() {
  var search = document.getElementById("mig-search");
  if (!search) return;
  var q = search.value.toLowerCase();
  var rows = document.querySelectorAll("#mig-buckets .mig-row");
  for (var i = 0; i < rows.length; i++) {
    rows[i].classList.toggle("is-hidden", rows[i].getAttribute("data-search").indexOf(q) === -1);
  }
}

function migrationPreview(btn) {
  var button = btn || document.getElementById("mig-preview-btn");
  if (button) {
    button.disabled = true;
    button.textContent = "Previewing...";
  }
  document.getElementById("mig-results").textContent = "";
  document.getElementById("mig-buckets").innerHTML = "";
  document.getElementById("mig-stats").hidden = true;
  document.getElementById("mig-action-bar").hidden = true;
  fetch("/api/abs-grimmory-migration/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(migrationOptions()),
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      if (!data.success) {
        PKModal.alert({
          title: "Error",
          message: (data && data.error) || "Preview failed",
        });
        return;
      }
      var c = data.counts || {};
      document.getElementById("mig-will").textContent = Number(c.will_migrate || 0);
      document.getElementById("mig-done").textContent = Number(c.already_migrated || 0);
      document.getElementById("mig-unmatched").textContent = Number(c.unmatched || 0);
      document.getElementById("mig-stats").hidden = false;
      migrationRenderBuckets(data.books || []);
      document.getElementById("mig-action-bar").hidden = Number(c.will_migrate || 0) === 0;
      migrationUpdateCount();
    })
    .catch(function (err) {
      console.error("migration preview error:", err);
      PKModal.alert({
        title: "Error",
        message: "An error occurred. Check console.",
      });
    })
    .finally(function () {
      if (button) {
        button.disabled = false;
        button.textContent = "Preview";
      }
    });
}

function migrationRun(btn) {
  var dryRun = document.getElementById("mig-dry-run").checked;
  var selected = [];
  var boxes = document.querySelectorAll(".mig-select:checked");
  for (var i = 0; i < boxes.length; i++)
    selected.push(boxes[i].getAttribute("data-abs-id"));

  var run = function () {
    btn.disabled = true;
    btn.textContent = dryRun ? "Dry running..." : "Migrating...";
    var payload = migrationOptions();
    payload.dry_run = dryRun;
    payload.selected_abs_ids = selected;
    fetch("/api/abs-grimmory-migration/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.success) {
          PKModal.alert({
            title: "Error",
            message: (data && data.error) || "Migration failed",
          });
          return;
        }
        var oc = data.outcome_counts || {};
        var parts = Object.keys(oc).map(function (k) {
          return oc[k] + " " + k.replace(/_/g, " ");
        });
        document.getElementById("mig-results").textContent =
          (dryRun ? "Dry run: " : "Done: ") + parts.join(" · ");
        if (!dryRun) migrationPreview(document.getElementById("mig-preview-btn"));
      })
      .catch(function (err) {
        console.error("migration run error:", err);
        PKModal.alert({
          title: "Error",
          message: "An error occurred. Check console.",
        });
      })
      .finally(function () {
        btn.disabled = false;
        migrationUpdateCount();
      });
  };

  if (dryRun) {
    run();
    return;
  }
  PKModal.confirm({
    title: "Migrate " + selected.length + " book(s)?",
    message:
      "This writes READ status, dates, sessions, and bookmarks into Grimmory for the selected matches.",
    onConfirm: run,
  });
}

function migrationBookByAbsId(absId) {
  for (var i = 0; i < migrationBooks.length; i++) {
    if (String(migrationBooks[i].abs_id) === String(absId)) return migrationBooks[i];
  }
  return null;
}

function migrationOpenMatchModal(absId) {
  migrationActiveMatchAbsId = String(absId);
  var book = migrationBookByAbsId(absId);
  var title = book ? migrationText(book.title, "this book") : "this book";
  document.getElementById("mig-match-title").textContent = "Choose match for " + title;
  var input = document.getElementById("mig-match-search");
  input.value = title;
  document.getElementById("mig-match-results").innerHTML = "";
  document.getElementById("mig-match-modal").hidden = false;
  input.focus();
  migrationSearchMatches();
}

function migrationCloseMatchModal() {
  document.getElementById("mig-match-modal").hidden = true;
  migrationActiveMatchAbsId = null;
}

function migrationSearchMatches() {
  var input = document.getElementById("mig-match-search");
  var q = input.value.trim();
  var results = document.getElementById("mig-match-results");
  if (!q) {
    results.innerHTML = '<div class="mig-empty">Enter a title, author, or filename.</div>';
    return;
  }
  results.innerHTML = '<div class="mig-empty">Searching Grimmory...</div>';
  fetch("/api/grimmory/search?q=" + encodeURIComponent(q))
    .then(function (r) {
      return r.json();
    })
    .then(function (books) {
      if (!books || !books.length) {
        results.innerHTML = '<div class="mig-empty">No Grimmory matches found.</div>';
        return;
      }
      results.innerHTML = books
        .map(function (book) {
          var id = migrationText(book.id, "");
          var instance = migrationText(book.instanceId, "default");
          var meta = [
            migrationText(book.authors, ""),
            migrationTypeLabel(book.bookType),
            migrationText(book.source, ""),
            migrationText(book.fileName, ""),
          ]
            .filter(Boolean)
            .join(" · ");
          return (
            '<button type="button" class="mig-match-result" onclick="migrationChooseManualMatch(' +
            migrationJsArg(id) +
            ", " +
            migrationJsArg(instance) +
            ')"><span class="mig-pane-title">' +
            migrationEscape(migrationText(book.title, "Untitled")) +
            '</span><span class="mig-pane-sub">' +
            migrationEscape(meta) +
            "</span></button>"
          );
        })
        .join("");
    })
    .catch(function (err) {
      console.error("migration match search error:", err);
      results.innerHTML = '<div class="mig-empty">Search failed. Check console.</div>';
    });
}

function migrationChooseManualMatch(bookId, instanceId) {
  if (!migrationActiveMatchAbsId) return;
  migrationManualMatches[migrationActiveMatchAbsId] = {
    grimmory_book_id: String(bookId),
    grimmory_instance_id: instanceId || "default",
  };
  migrationCloseMatchModal();
  migrationPreview();
}

function migrationClearManualMatch(absId) {
  delete migrationManualMatches[String(absId)];
  migrationPreview();
}

document.addEventListener("DOMContentLoaded", function () {
  var search = document.getElementById("mig-search");
  if (search) {
    search.addEventListener("input", migrationApplyFilter);
  }

  var matchSearch = document.getElementById("mig-match-search");
  if (matchSearch) {
    matchSearch.addEventListener("keydown", function (e) {
      if (e.key === "Enter") migrationSearchMatches();
      if (e.key === "Escape") migrationCloseMatchModal();
    });
  }
});
