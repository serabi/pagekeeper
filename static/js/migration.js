function migrationOptions() {
  return {
    carry_listening_sessions: document.getElementById("mig-sessions").checked,
    carry_bookmarks: document.getElementById("mig-bookmarks").checked,
  };
}

function migrationEscape(s) {
  var d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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

function migrationRenderBuckets(books) {
  var groups = { will_migrate: [], already_migrated: [], unmatched: [] };
  books.forEach(function (b) {
    if (groups[b.bucket]) groups[b.bucket].push(b);
  });

  var html = "";

  if (groups.will_migrate.length) {
    html +=
      "<h3>Will migrate (" +
      groups.will_migrate.length +
      ')</h3><ul class="mig-list">';
    groups.will_migrate.forEach(function (b) {
      html +=
        '<li class="mig-row"><label class="checkbox-label">' +
        '<input type="checkbox" class="mig-select" checked data-abs-id="' +
        migrationEscape(b.abs_id) +
        '"> ' +
        "<strong>" +
        migrationEscape(b.title) +
        "</strong> &middot; " +
        migrationEscape(b.author) +
        ' <span class="help-note">matched by ' +
        migrationEscape(b.matched_by) +
        " &rarr; " +
        migrationEscape(b.grimmory_title) +
        (b.finished_at
          ? " &middot; finished " + migrationEscape(b.finished_at)
          : "") +
        "</span></label></li>";
    });
    html += "</ul>";
  }

  if (groups.already_migrated.length) {
    html +=
      "<details><summary>Already migrated (" +
      groups.already_migrated.length +
      ')</summary><ul class="mig-list">';
    groups.already_migrated.forEach(function (b) {
      html +=
        '<li class="mig-row"><strong>' +
        migrationEscape(b.title) +
        "</strong> &middot; " +
        migrationEscape(b.author) +
        ' <span class="help-note">' +
        migrationEscape(b.migrated_outcome) +
        (b.migrated_at
          ? " on " + migrationEscape(b.migrated_at.slice(0, 10))
          : "") +
        "</span></li>";
    });
    html += "</ul></details>";
  }

  if (groups.unmatched.length) {
    html +=
      "<details><summary>Unmatched (" +
      groups.unmatched.length +
      ')</summary><ul class="mig-list">';
    groups.unmatched.forEach(function (b) {
      html +=
        '<li class="mig-row"><strong>' +
        migrationEscape(b.title) +
        "</strong> &middot; " +
        migrationEscape(b.author) +
        "</li>";
    });
    html += "</ul></details>";
  }

  document.getElementById("mig-buckets").innerHTML = html;

  var boxes = document.querySelectorAll(".mig-select");
  for (var i = 0; i < boxes.length; i++)
    boxes[i].addEventListener("change", migrationUpdateCount);
}

function migrationPreview(btn) {
  btn.disabled = true;
  btn.textContent = "Previewing…";
  document.getElementById("mig-results").textContent = "";
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
      document.getElementById("mig-will").textContent = Number(
        c.will_migrate || 0,
      );
      document.getElementById("mig-done").textContent = Number(
        c.already_migrated || 0,
      );
      document.getElementById("mig-unmatched").textContent = Number(
        c.unmatched || 0,
      );
      document.getElementById("mig-stats").hidden = false;
      migrationRenderBuckets(data.books || []);
      document.getElementById("mig-action-bar").hidden =
        Number(c.will_migrate || 0) === 0;
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
      btn.disabled = false;
      btn.textContent = "Preview";
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
    btn.textContent = dryRun ? "Dry running…" : "Migrating…";
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
          return oc[k] + " " + k;
        });
        document.getElementById("mig-results").textContent =
          (dryRun ? "Dry run: " : "Done: ") + parts.join(" · ");
        if (!dryRun)
          migrationPreview(document.getElementById("mig-preview-btn"));
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
      "This writes READ status, dates, sessions, and bookmarks into Grimmory.",
    onConfirm: run,
  });
}

document.addEventListener("DOMContentLoaded", function () {
  var search = document.getElementById("mig-search");
  if (search) {
    search.addEventListener("input", function () {
      var q = search.value.toLowerCase();
      var rows = document.querySelectorAll("#mig-buckets .mig-row");
      for (var i = 0; i < rows.length; i++) {
        rows[i].style.display =
          rows[i].textContent.toLowerCase().indexOf(q) === -1 ? "none" : "";
      }
    });
  }
});
