# PageKeeper Agent Notes

## Git Flow

PageKeeper uses a three-tier branch strategy with separate public and private remotes.

### Remotes

| Remote | Repo | Visibility | Purpose |
|--------|------|------------|---------|
| `origin` | `serabi/pagekeeper` | Public | Releases and contributions |
| `private` | `serabi/pagekeeper-dev` | Private | Full working history |

### Branches

| Branch | Remote | Purpose |
|--------|--------|---------|
| `dev-private` | `private` | Working branch — all development happens here |
| `dev` | `origin` | Public integration branch — squash-merged from dev-private |
| `main` | `origin` | Stable releases — squash-merged from dev |

### Workflow

1. **Feature work**: Create feature branches from `dev-private`, merge back into `dev-private`, push to `private` remote.
2. **Promote to public dev**: Use `.git/promote.sh dev-private dev "commit message"` — this squash-merges and automatically strips private-only files.
3. **Release to main**: Use `.git/promote.sh dev main "release message"` — same process.
4. **Never push `dev-private` or feature branches to `origin`** — the pre-push hook blocks this.

### Private-Only Files

These files exist on `dev-private` but are stripped by the promote script before reaching public branches:

- `CLAUDE.md`, `AGENTS.md` — AI instructions
- `.claude/` — hooks and config
- `.github/copilot-instructions.md` — Copilot config
- `dev/` — internal planning docs
- `DOCKER-PREPARATION.md` — internal setup notes
- `migrations/` — internal architecture docs

### Push Rules

- `origin` only accepts `main` and `dev` (enforced by pre-push hook and git config)
- Pushing to `main` on origin prompts for confirmation
- `private` remote has no restrictions

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