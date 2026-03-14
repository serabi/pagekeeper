# Contributing to PageKeeper

Thanks for your interest in contributing to PageKeeper!

## Ways to Contribute

- **Report bugs**: open an issue with steps to reproduce
- **Suggest features**: open an issue so we can chat about it
- **Improve documentation**: typos, clarity, missing info
- **Submit code**: bug fixes, new features, refactors

For larger changes, please open an issue first so we can discuss the approach before you invest time in a PR.

## Development Setup

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (required for both running and testing)
- [Git](https://git-scm.com/)

### Getting Started

```bash
# Fork the repo on GitHub first, then:
git clone https://github.com/<your-username>/pagekeeper.git
cd pagekeeper
cp docker-compose.example.yml docker-compose.yml
docker compose up --build
```

The dashboard is available at `http://localhost:4477`. All settings — integrations, API keys, sync configuration — are managed through the web dashboard. No environment variables are required beyond `TZ`.

## Running Tests

Tests run inside Docker. From the project root:

```bash
# Run the full test suite
./run-tests.sh

# Run a specific test file
./run-tests.sh tests/test_sync_manager.py

# Run tests matching a keyword
./run-tests.sh -k "test_progress"
```

The test container handles all dependencies (epubcfi, ffmpeg, etc.), so there's nothing extra to install locally.

## Branching & Pull Requests

1. **Fork** the repo and clone your fork
2. **Branch from `dev`** — `main` is for releases only
3. **Keep PRs focused** — one feature or fix per PR
4. **Open your PR against `dev`**, not `main`

```bash
git checkout dev
git pull origin dev
git checkout -b my-feature
# ... make changes ...
git push origin my-feature
# Open PR → base: dev
```

## Code Style

- **Python** — formatted and linted with [ruff](https://docs.astral.sh/ruff/). Run `ruff check` and `ruff format` before submitting.
- **Frontend** — vanilla JS, HTML, and CSS

## AI Usage

I use AI to build PageKeeper and I'm fine with others also using AI. With that said, **please make sure you understand what your code does before submitting a PR.** AI is great as a tool but lousy as a decision maker.

## Reporting Bugs

When opening a bug report, please include:

- What you expected vs. what happened
- Steps to reproduce
- Relevant logs (check the Logs page in the dashboard)
- Your deployment method and any relevant integration versions

## License

PageKeeper is [MIT licensed](LICENSE). By contributing, you agree that your contributions will be licensed under the same terms.
