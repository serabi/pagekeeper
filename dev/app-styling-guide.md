# App Styling Guide

This document is the working CSS guide for PageKeeper contributors. It is based on the current frontend stack and should be treated as the default reference when adding or revising UI on the dashboard, reading log, header, and future screens.

The goal is consistency, not novelty. New UI should look like it belongs in the app, use the existing styling layers, and avoid adding more drift through inline styles or JS-driven presentation.

## Design Principles

- Build from the existing design system before inventing page-specific styling.
- Keep the visual language consistent: dark layered surfaces, soft borders, high-contrast text, rounded cards, and restrained motion.
- Preserve the app's current strong patterns:
  - tokenized colors, type, radii, and transitions
  - shared layout and component layers loaded before page CSS
  - sticky, glassy navigation and toolbar treatments
  - consistent card and surface treatment across pages
  - mobile-aware breakpoints already used in layout and reading views
- Prefer maintainable styling over fast one-off fixes. If a style will be reused, name it and place it correctly.

## CSS Architecture

PageKeeper already has a clear layer order. Keep using it in this order:

1. `static/css/variables.css`
2. `static/css/base.css`
3. `static/css/layout.css`
4. `static/css/components.css`
5. page-specific CSS such as `static/css/dashboard.css` or `static/css/reading.css`

Use these layers for different concerns:

- `variables.css`: design tokens only
- `base.css`: reset, element defaults, and small utilities
- `layout.css`: page structure, navigation, containers, headers, shared layout primitives
- `components.css`: reusable UI pieces such as buttons, forms, cards, badges, modals
- page CSS: screen-specific behavior and styling that is not yet shared

### Promotion Rule

Use this rule when deciding where a new pattern belongs:

- single page only: keep it in that page's CSS
- reused on 2+ screens: move it into `components.css` or `layout.css`
- global design value: add a token to `variables.css`

If a page-specific pattern starts spreading, promote it instead of copying it.

## Tokens and Theming

The current token layer is the foundation of the app. Reuse it before introducing literals.

### What To Keep

- Color tokens for primary, semantic, surface, text, border, and service accents
- Typography tokens for headings and body copy
- Radius tokens for small, default, large, and extra-large corners
- Transition tokens for standard, bounce, and smooth timing

### Rules

- Prefer CSS variables over hardcoded colors, shadows, and gradients.
- Add a new token when a value is:
  - reused
  - semantic
  - visually important enough that it may need global tuning later
- Do not hardcode repeated accent rgba values in page CSS or JS if they can be promoted to tokens.
- Keep service-specific colors semantic. If a color represents ABS, BookFusion, Hardcover, or another integration, define or reuse a named token rather than embedding hex values inline.

### When To Add a Token

Add a token if the value is any of the following:

- a recurring shadow
- a recurring gradient
- a recurring surface tint
- a recurring focus ring
- a recurring status or integration accent

Do not add a token for a truly one-off, page-local visual detail that is unlikely to repeat.

## Layout Rules

`layout.css` should continue to own the large structural patterns of the app.

### Header

The header establishes the app's layout tone:

- sticky positioning
- glassy blurred background
- soft border and shadow separation
- compact rounded link shapes
- responsive collapse to a mobile toggle

Future top-level navigation work should extend `.top-nav`, `.nav-menu`, `.nav-link`, `.nav-actions`, and related classes instead of recreating navigation patterns inside page templates.

### Containers and Headers

- Use `.container` for page width and outer spacing unless there is a strong reason not to.
- Use shared page header conventions from `layout.css` where possible before creating a new header pattern.
- Keep spacing rhythms consistent with existing container and section header spacing.

### Sticky Interface Elements

Sticky treatment is already part of the app language in the header and reading toolbar. Preserve these traits:

- translucent or layered background
- blur used sparingly to separate from content
- stable z-index choices
- clear border/shadow edge so sticky UI does not visually disappear into the page

If another sticky bar is added, it should feel related to these existing patterns.

## Shared Component Rules

`components.css` should be the home for reusable UI, not just generic UI.

### Buttons

- Reuse existing button variants before creating new ones.
- New button styles should be semantic and named by role, not by page.
- Keep hover and focus behavior consistent with current button motion and contrast levels.

### Forms and Controls

- Reuse the existing form field styling for text inputs, selects, textareas, and search boxes.
- Keep focus states visible and token-driven.
- When multiple controls form one tool area, prefer a shared wrapper pattern like the existing control bars over ad hoc spacing in templates.

### Cards and Surfaces

The dashboard book cards and reading log cards share a clear surface language:

- dark layered backgrounds
- subtle borders
- rounded corners
- hover lift and shadow
- readable hierarchy for title, metadata, and actions

New cards should inherit from this visual family rather than inventing alternate surface treatments without reason.

### Modals

There is already modal styling in shared CSS, but dashboard-specific modal styles also exist. Future work should reduce this split.

Rules:

- prefer shared modal patterns first
- only keep page-specific modal rules when the content structure is truly unique
- if a modal shell pattern repeats, promote it into shared component CSS

## Page-Specific Guidance

### Header

The header is part of the layout system, not page CSS. Keep it that way.

- Nav structure belongs in shared layout
- service icon links should continue to use shared nav classes
- page-specific nav tweaks should be rare

### Dashboard

The dashboard uses shared components plus dashboard-only behavior:

- controls bar for search, filter, and sort
- grid-based book sections
- integration-specific modals and action panel behavior

Guidance:

- dashboard-only selectors may stay local if they are tied to dashboard workflows
- if a control pattern is needed on another page, promote it out of `dashboard.css`
- keep dashboard selectors scoped to dashboard-specific names or IDs to avoid leakage

### Reading Log

The reading log is a good example of a page that has its own strong namespace and still fits the app:

- `r-` prefixed selectors keep concerns local
- custom section, card, toolbar, and detail styles remain readable because they are consistently scoped
- responsive behavior is defined in CSS, not improvised in templates

Guidance:

- continue using clear page prefixes for reading-specific patterns
- keep reading page interaction states class-based where possible
- when a reading pattern becomes useful elsewhere, promote it rather than copying `r-` selectors to another page

## Accessibility and Responsive Rules

The app already does several things correctly. Keep these standards in place.

### Accessibility

- Maintain visible focus styles for links, buttons, inputs, and interactive chips.
- Preserve semantic controls in templates: buttons for actions, links for navigation.
- Keep text contrast high against dark surfaces.
- Do not rely on color alone to communicate state when text or icons can help.
- When using icon-only actions, include an accessible label or title.

### Responsive Design

- Follow the existing breakpoint approach already used in `layout.css` and `reading.css`.
- Put responsive layout behavior in CSS, not in JS or inline template styles.
- Design mobile behavior intentionally:
  - stacked layouts
  - reduced padding where needed
  - scrollable control rows when appropriate
  - explicit mobile toggles instead of overcrowding desktop controls

Before adding a new page, decide:

- how the page header compresses
- how toolbars collapse
- whether cards stay list-based or switch to grid
- what must remain sticky on small screens, if anything

## CSS Best Practices For This Repo

These rules should guide all future frontend work.

- Prefer CSS variables over literals.
- Prefer semantic, reusable classes over per-template one-offs when a pattern repeats.
- Prefer class or data-state toggles over JS `style.*` mutations for visibility and interaction states.
- Prefer component classes over inline `style=""` attributes.
- Keep page CSS scoped with a page prefix or page-local selector strategy.
- Define spacing, hover, focus, and responsive behavior in CSS alongside the component.
- Keep templates structural. Keep presentation in CSS.
- Keep JS responsible for state and behavior, not visual authorship.

### Acceptable Inline Style Exceptions

Inline styles are acceptable only when the value is truly data-driven at render time:

- progress width percentages
- server-rendered image URLs or background images
- temporary one-off experiments that must be promoted or removed before the feature is considered finished

If the style is not data-driven, it should almost always be a class.

## Do This, Not That

### 1. Visibility and open/close state

Do this:

```html
<div class="confirm-modal hidden" id="confirm-modal"></div>
```

```js
modal.classList.remove('hidden');
modal.classList.add('is-open');
```

Not that:

```js
modal.style.display = 'flex';
modal.style.display = 'none';
```

Reason: state classes are easier to reuse, animate, and audit than imperative display styles scattered across JS.

### 2. Reused styling details

Do this:

```css
:root {
  --shadow-elevated: 0 8px 28px rgba(0, 0, 0, 0.35);
}

.book-card:hover {
  box-shadow: var(--shadow-elevated);
}
```

Not that:

```css
.book-card:hover {
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.35);
}
```

And not this in JS:

```js
item.style.opacity = '0';
item.style.transition = 'opacity 0.3s';
```

Reason: repeated visual values should be tokenized or class-based, not recreated ad hoc.

### 3. Template presentation

Do this:

```html
<img src="/static/Storyteller_Logo.png" alt="ST" class="r-service-row-icon r-service-row-icon--storyteller">
```

```css
.r-service-row-icon--storyteller {
  background: white;
  border-radius: 3px;
}
```

Not that:

```html
<img src="/static/Storyteller_Logo.png" alt="ST" class="r-service-row-icon" style="background: white; border-radius: 3px;">
```

Reason: presentational details belong in CSS so they remain reusable and searchable.

### 4. Repeated page patterns

Do this:

- keep a unique pattern in `dashboard.css` or `reading.css` while it is page-local
- move it to shared CSS once a second screen needs it

Not that:

- copy a dashboard pattern into a second page with slightly different class names

Reason: promotion is cheaper than divergence.

## Known Debt and Cleanup Targets

This repo already has styling debt. Document it and reduce it over time.

### Current Debt

- Inline style usage in dashboard and reading templates
- JS directly setting `display`, `outline`, `opacity`, `border`, `font`, and positioning styles
- Repeated purple and teal rgba values that should become tokens
- Modal styling duplicated between shared components and dashboard-specific CSS
- Mixed use of shared classes and page-specific one-offs without clear promotion

### What Future Work Should Do

- Replace inline presentational styles with classes
- Replace JS-applied visual styling with state classes
- Promote recurring rgba, glow, gradient, and shadow values into tokens
- Consolidate modal shells and shared action patterns
- Move repeated per-page control patterns into shared CSS when reuse is proven

This guide does not require cleaning up all of that now. It sets the standard so future work does not add more.

## New UI Checklist

Before shipping a new UI change, check:

- Does the style belong in the right layer?
- Did I reuse tokens before adding literals?
- If I added a new recurring visual value, did I promote it to `variables.css`?
- If I added a reusable pattern, did I place it in shared CSS instead of page CSS?
- Are focus states visible?
- Does it work at mobile breakpoints?
- Is presentation mostly in CSS rather than inline styles or JS `style.*` writes?
- Are selectors scoped clearly enough to avoid leaking into other screens?
- If I used an inline style, is it truly data-driven?

When in doubt, bias toward consistency with the dashboard, reading log, and header rather than creating a new visual language.
