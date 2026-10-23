# Contributing

Thanks for helping. This project is small on purpose: one stdlib-only script,
two markdown files, no dependencies.

## Setup

```bash
git clone https://github.com/BerkeZeqoN/multi-review.git
cd multi-review
python -m unittest discover -s tests -v   # must pass
pip install ruff && ruff check .          # must pass
python install.py --dry-run               # see where the skill would go
```

## Ground rules

- **Python 3.8+, standard library only.** CI runs Linux, Windows and macOS.
- **Code, identifiers and comments are in English.** The agents' review
  content (section names like `## Eksiklikler`) is Turkish by design. Those
  headings are parsed by the script, so do not rename them casually.
- **Every behaviour change needs a test** in `tests/`. Bug fixes start with a
  failing test.
- **Keep `SKILL.md`, `PROTOCOL.md` and `scripts/multi_loop.py` in sync.** A
  new flag is documented in all three (and in `README.md` if users type it).
- Add an entry under `## [Unreleased]` in `CHANGELOG.md`.

## Adding a tool

1. Add its skill directory to `TOOLS` in `install.py` and to the table in
   `SKILL.md`.
2. If the tool auto-loads an instruction file, add that file name to
   `RESERVED_FILES` in `scripts/multi_loop.py`.
3. Describe in `SKILL.md` ("How to wait") how it waits in the background.

## Pull requests

Small, focused PRs. Describe what changed, why, and how you verified it
(test name or command output). Evidence over claims; that is the whole point
of this tool.
