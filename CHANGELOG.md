# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- README opens with an animated review chat (`docs/agents-chat.gif`) and shows the real
  `wait` output in a collapsible block.
- README explains the verdict words: `KABUL` (accept), `RED` (reject), `KISMEN` (partly right).

## [0.4.1] - 2026-10-24

### Fixed
- `STALE_AGENTS` also lists drive agents whose driver stopped.
- `status` and `roster` print a `NEXT:` line with the selected `--root`/`--session`.
- A sandbox that refuses writes (Codex outside its workspace) is reported at once as
  `ERROR: cannot write ...: permission denied` with the fix, instead of `lock busy`
  after 15 s. Any other refused write ends with the same hint instead of a traceback.

## [0.4.0] - 2026-10-23

### Added
- **Several sessions at once** (`--session NAME`, default `main`): two reviews
  of the same project or of different projects run side by side. Each named
  session lives in `multi-review/<name>/` with its own state and `final.md`.
  `MULTIPLE_SESSIONS` lists name, goal, project and time left. `--session`
  alone finds that name in any project.
- `NEXT:` line after `join` and `wait`: the exact next command, with
  `--root`/`--session`.
- `/multi-review-join` command for Claude Code (installed by `install.py`): a
  thin router to the skill's join path, no questions asked.
- Heartbeat: `seen=<age>_ago`, `[STALE]` and `STALE_AGENTS=` for agents whose
  loop stopped. `now=` in the clock line.

### Changed
- Creation options (`--interval`, `--goal`, `--work-mode`, ...) on an already
  active session are an error (`SESSION_EXISTS`). Before, they were silently
  ignored.
- `done` waits for the `final.md` draft (`WAITING_FOR_FINAL`). Drive-only
  sessions get a final writer and a lead.

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
- `WORK_WARNING` also clears on a logged clean pass (`Taze tarama: ...`), as
  the protocol allows.
- Windows: the lock retries on `PermissionError`, and `install.py` updates in
  place instead of `rmtree`.

## [0.1.0] - 2026-10-23

### Added
- First public version: N-agent review ring, shared clock, random or
  negotiated ring order, `drive` for text-only models.

[Unreleased]: https://github.com/BerkeZeqoN/multi-review/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/BerkeZeqoN/multi-review/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/BerkeZeqoN/multi-review/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/BerkeZeqoN/multi-review/compare/v0.1.0...v0.3.0
[0.1.0]: https://github.com/BerkeZeqoN/multi-review/releases/tag/v0.1.0
