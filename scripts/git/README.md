# Git Workflow Scripts

These scripts replace the old local-only `.git/promote.sh` and `.git/hooks/pre-push`
flow with repo-owned tooling.

## Branch Roles

- `dev`: public development branch on `origin`
- `main`: public release branch on `origin`
- review and feature branches: public-safe PR branches on `origin`
- `draft`: private unpublished branch on `private`
- `private/dev` and `private/main`: mirrors of the public branches

## First-Time Setup

```bash
scripts/git/install-hooks.sh
scripts/git/sanitize-public-branch.sh dev "chore: strip private-only files from dev"
scripts/git/sanitize-public-branch.sh main "chore: strip private-only files from main"
scripts/git/create-draft-branch.sh
git branch --track draft private/draft
scripts/git/setup-worktrees.sh
```

## Public-First Workflow

1. Work on `dev`
2. Push to `origin/dev`
3. Sync the private mirror:

```bash
scripts/git/sync-private-mirrors.sh dev
```

## Private-First Workflow

1. Work on `draft`
2. Push to `private/draft`
3. Promote a sanitized snapshot to `dev`

```bash
scripts/git/promote.sh --push draft dev "feat: publish draft snapshot"
scripts/git/sync-private-mirrors.sh dev
```

## Release Workflow

```bash
scripts/git/promote.sh --push dev main "release: vX.Y.Z"
scripts/git/sync-private-mirrors.sh main
```

## Safety Checks

- `config/private-paths.txt` defines what must never land on public branches
- `.githooks/pre-push` verifies pushes to `origin` do not include private-only paths
- `.githooks/pre-push` blocks public branch deletion and mismatched local/remote ref pushes
- `scripts/git/verify-public-tree.sh` validates public branch content
