# Agent PR Guidelines

These rules apply to PRs opened by AI coding agents such as Codex, Claude, Cursor, or other automated assistants.

## Branch and scope

- Target `dev` unless Sarah explicitly says otherwise.
- Keep PRs small, focused, and independently reviewable.
- Prefer one bug fix, one workflow improvement, or one documentation/tooling change per PR.
- Do not mix unrelated cleanup with feature work.

## PR body

- Use `.github/pull_request_template.md`.
- Keep prose concise and concrete.
- Fill every applicable section.
- For UI changes, include before/after screenshots or explain why screenshots are not available.
- For database, sync, external API, or migration changes, explicitly describe data/migration risk and compatibility notes.
- Include exact validation commands and results. If a command cannot run, state the blocker honestly.

## Commits and attribution

- Do not add AI signatures, marketing footers, or generated-by boilerplate.
- Do not add `Generated with Claude`, `Generated with Codex`, `Co-Authored-By: Claude`, `Co-Authored-By: Codex`, or similar attribution footers.
- Do not sign commits unless the repository/user explicitly requires signing.

## Review loop

- Before opening a PR, run relevant lint/tests for the touched area.
- When using a second agent for review, fix valid findings before opening the PR.
- If a PR remains risky or incomplete, leave it as draft and call out the blocker in the PR body.
