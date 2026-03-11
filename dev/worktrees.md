# Git Worktrees

Git worktrees let you check out multiple branches in parallel, each in its own directory, without cloning the repo again. They share the same `.git` object store, so commits, stashes, and refs are visible everywhere.

## Why worktrees suit PageKeeper

PageKeeper often has several streams of work in flight — a UI feature, Docker/infra tweaks, a sync integration — each on its own feature branch off `dev-private`. Worktrees let you switch between them instantly without stashing or context-switching a single checkout.

## Naming convention

Place worktrees as sibling directories using the pattern:

```
/Volumes/externalSSD/development/
  pagekeeper/              ← main worktree (dev-private)
  pagekeeper-tbr/          ← feature/tbr-page
  pagekeeper-docker/       ← feature/docker-updates
```

Name format: `../pagekeeper-<purpose>`

## Commands

### Create a worktree on a new feature branch

```bash
# From the main worktree (/Volumes/externalSSD/development/pagekeeper)
git worktree add ../pagekeeper-<purpose> -b feature/<name> dev-private
```

### List active worktrees

```bash
git worktree list
```

### Remove a worktree

Always use the git command — don't just delete the directory:

```bash
git worktree remove ../pagekeeper-<purpose>
```

If the directory was already deleted manually, clean up stale references:

```bash
git worktree prune
```

### Delete the feature branch after merging

```bash
git branch -d feature/<name>
```

## Key constraints

- **One branch per worktree.** A branch can only be checked out in one worktree at a time. Attempting to check it out elsewhere will error.
- **Run promote.sh from the main worktree only.** The promote script expects to be run from the primary checkout (`pagekeeper/`), not from a linked worktree.
- **Shared state.** Commits, branches, and stashes are shared across all worktrees. A `git fetch` in any worktree updates refs for all of them.
- **Hooks are shared.** Pre-push, pre-commit, and other hooks live in the main `.git/` directory and apply to all worktrees.

## Typical lifecycle

```
1. git worktree add ../pagekeeper-foo -b feature/foo dev-private
2. cd ../pagekeeper-foo && <do work, commit>
3. cd ../pagekeeper && git merge feature/foo
4. git worktree remove ../pagekeeper-foo
5. git branch -d feature/foo
```
