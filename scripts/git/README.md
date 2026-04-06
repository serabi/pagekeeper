# Git Workflow Scripts

These scripts replace the old local-only `.git/promote.sh` and `.git/hooks/pre-push`
flow with repo-owned tooling.

## Branch Roles

- `dev`: integration branch on `origin`
- `main`: release branch on `origin`
- feature branches: short-lived topic branches on `origin`, typically merged into `dev`

## First-Time Setup

```bash
scripts/git/install-hooks.sh
scripts/git/sanitize-public-branch.sh dev "chore: strip private-only files from dev"
scripts/git/sanitize-public-branch.sh main "chore: strip private-only files from main"
scripts/git/setup-worktrees.sh
```

## Development Workflow

1. Work on `dev`
2. Push to `origin/dev`
3. Open a PR from `dev` to `main` when ready to release

## Feature Branch Workflow

1. Create a branch from `dev`
2. Push the branch to `origin`
3. Open a PR into `dev`

```bash
git switch dev
git pull --ff-only origin dev
git switch -c my-feature
git push -u origin my-feature
```

## Release Workflow

1. Merge feature branches into `dev`
2. Push `dev`
3. Open a PR from `dev` to `main`

```bash
git push origin dev
gh pr create --base main --head dev
```

## Safety Checks

- `config/private-paths.txt` defines what must never land on public branches
- `.githooks/pre-push` verifies any branch pushed to `origin` and prompts before direct pushes to `main`
- `scripts/git/verify-public-tree.sh` validates public branch content
