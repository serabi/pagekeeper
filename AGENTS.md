# PageKeeper Agent Notes

## Git Flow

PageKeeper uses separate public and private remotes with public-first and private-first workflows.

### Remotes

| Remote | Repo | Visibility | Purpose |
|--------|------|------------|---------|
| `origin` | `serabi/pagekeeper` | Public | Public development and releases |
| `private` | `serabi/pagekeeper-dev` | Private | Private drafts and mirrored public history |

### Branches

| Branch | Remote | Purpose |
|--------|--------|---------|
| `draft` | `private` | Private unpublished work |
| `dev` | `origin` | Public development and integration |
| `main` | `origin` | Public release branch |
| `dev` | `private` | Mirror of `origin/dev` |
| `main` | `private` | Mirror of `origin/main` |

### Workflow

1. **Public-first work**: Work on `dev`, push to `origin/dev`, then sync the mirror with `scripts/git/sync-private-mirrors.sh dev`.
2. **Private-first work**: Work on `draft`, push to `private/draft`, then publish with `scripts/git/promote.sh draft dev "commit message"`.
3. **Release to main**: Use `scripts/git/promote.sh dev main "release message"` and then `scripts/git/sync-private-mirrors.sh main`.
4. **Install hooks**: Run `scripts/git/install-hooks.sh` once per clone.
5. **Never push `draft` or private feature branches to `origin`** — the versioned pre-push hook blocks this.

### Private-Only Files

These paths are treated as private-only by `config/private-paths.txt` and are stripped before content reaches public branches:

- `CLAUDE.md`, `AGENTS.md` — AI instructions
- `.claude/` — hooks and config
- `.github/copilot-instructions.md` — Copilot config
- `dev/` — legacy private planning docs that should eventually move under `private/`
- `private/` — future private-first content
- `DOCKER-PREPARATION.md` — internal setup notes
- `migrations/` — internal architecture docs

### Push Rules

- `origin` only accepts `main` and `dev` (enforced by `.githooks/pre-push`)
- Pushing to `main` on origin prompts for confirmation
- `private/dev` and `private/main` should remain exact mirrors of the public branches

## Styling Reference

When making frontend changes, use the app styling guide as the primary reference:

- [dev/app-styling-guide.md](dev/app-styling-guide.md)

Use it for CSS layer placement, token usage, component vs page CSS decisions, responsive behavior, and avoiding inline-style / JS-presentation drift.

## Testing

Always run tests via `./run-tests.sh` — never bare `pytest`. The test suite requires Docker for `epubcfi` and `ffmpeg` dependencies that aren't available locally.

```bash
./run-tests.sh                              # full suite
./run-tests.sh tests/test_sync_manager.py   # single file
./run-tests.sh -k "test_name" -v            # filtered + verbose
```

## Security Notes

`KOSYNC_KEY` is intentionally revealed to the user via the SHOW button on the settings page (fetch-on-demand, never embedded in HTML). However, it must never appear in log output — always sanitize before logging.
