# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-10-23

### Added
- **Work modes** (`--work-mode research|split|review`, `--areas`). By default
  every agent researches the code every tick. Before this, late joiners only
  commented on other agents' findings. `wait` reports `own_findings=N new=K`
  and adds a `WORK_WARNING` after two idle ticks.
- **Session goal** (`--goal`): the user's review focus, shown to every agent
  and passed to `drive` prompts.
- **`final.md`**: on the last tick the final writer drafts the shared list of
  findings to fix. After `SESSION_OVER` the other agents run `done`, then
  `finalize` keeps `final.md` and deletes (or `--archive`s) every other
  review file.
- `--resume` for an agent that lost its context (for example after `/clear`).
- `install.py`: installs, updates or removes the skill for Claude Code,
  Antigravity, Codex and `~/.agents`, with `--dry-run`, `--target`,
  `--uninstall` and `--list`.
- `--version`, test suite (`tests/`), CI on Linux, Windows and macOS with
  Python 3.8-3.13, ruff lint, `.gitattributes` (LF).

### Changed
- Joining with a name that is already active is an error unless `--resume`
  is given.
- A new session archives the previous session's review files instead of
  silently reusing them.
- The `drive` prompt is fitted section by section: identity, own file and
  task are never cut, and other agents' files keep their newest part.
- `path:line` references resolve by unique suffix and support extensionless
  files (`Dockerfile:12`). The project tree includes untracked files.
- A tie at the negotiation deadline keeps the provisional ring. Before, the
  first voter won.

### Fixed
- Review file names that tools load as instructions (`CLAUDE.md`,
  `AGENTS.md`, `GEMINI.md`, ...) are rejected. On case-insensitive file
  systems `claude.md` was loaded as Claude Code instructions.
- `wait --max-wait` now notices `stop` within 10 s.
- A left agent can no longer `wait`. `leave` closes a negotiation that can
  no longer happen.
- Writes to the machine-wide session registry are locked.

## [0.1.0] - 2026-10-23

### Added
- First public version: N-agent review ring, shared clock, random or
  negotiated ring order, `drive` for text-only models.

[0.3.0]: https://github.com/BerkeZeqoN/multi-review/compare/v0.1.0...v0.3.0
[0.1.0]: https://github.com/BerkeZeqoN/multi-review/releases/tag/v0.1.0
