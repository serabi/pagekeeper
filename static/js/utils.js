/* ═══════════════════════════════════════════
   PageKeeper — shared utilities
   ═══════════════════════════════════════════ */

/**
 * Escape HTML special characters to prevent XSS.
 * @param {*} text — value to escape (coerced to string)
 * @returns {string}
 */
function escapeHtml(text) {
    var s = String(text == null ? '' : text);
    return s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Returns a debounced version of `func` that delays invocation
 * until `wait` ms have elapsed since the last call.
 * @param {Function} func
 * @param {number}   wait — milliseconds
 * @returns {Function}
 */
function debounce(func, wait) {
    var timeout;
    return function () {
        var ctx = this, args = arguments;
        clearTimeout(timeout);
        timeout = setTimeout(function () { func.apply(ctx, args); }, wait);
    };
}

/**
 * Toggle a collapsible "hidden" section.
 * Expects `headerEl` followed by a sibling whose visibility is toggled.
 * @param {HTMLElement} headerEl — the clickable header element
 */
function toggleHiddenSection(headerEl) {
    headerEl.classList.toggle('collapsed');
    var sibling = headerEl.nextElementSibling;
    if (sibling) sibling.classList.toggle('hidden');
}
