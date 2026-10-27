# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.7.0] - 2026-10-27

### Added
- `open-ide antigravity` (and `join --open-ide antigravity` when creating) opens Antigravity on the
  session's project folder, so its agent works inside its workspace and the IDE stops asking for
  permission on every write of the loop. Antigravity joining from another folder gets
  `WORKSPACE_OUTSIDE_ROOT` with that command.

### Fixed
- A session created by a sandboxed agent (Antigravity, Codex) no longer stays invisible: the next
  agent that joins and may write the machine-wide files registers it and opens the chat panel if no
  agent could. Sandboxed joiners still write nothing outside the project.
- A sandbox that refuses `~/.multi-review` (Antigravity: `cannot write ...: permission denied`) no
  longer unregisters the session: the registry, `recent.json`, `hub.json` and the panel's Edge
  profile fall back to `%TEMP%/multi-review`, readers merge both. Nothing writable prints
  `REGISTRY_UNAVAILABLE` with the exact `--root ... join` command for the other agents.
- The panel did not open by itself when a session was created (the spawned process exited at
  once); `join` now starts it and reports `CHAT_PANEL opening` only once it answers.

## [0.6.1] - 2026-10-24

### Added
- An agent without a `wait` for 30 minutes is dropped (out of tokens or disconnected): the ring
  closes, it cannot be the final writer, `finalize` no longer waits for it; `join` brings it back.
  `wait`/`roster`/`finalize` print `DROPPED=...`, the chat shows why it left.
- One chat panel for every session: the list on the left switches between running sessions
  (live, message box) and recently finished ones (replay). `join` opens it when it creates a
  session; an open panel selects a new session by itself. `MULTI_REVIEW_NO_CHAT=1` turns it off.
- Finished sessions from before the event log get one built from what is left when opened.
- Rename a session from the panel (✎ next to the title) or with `rename --title "..."`. It is a
  display name only: the session's name, folder and `NEXT:` commands stay the same.

### Fixed
- Unanswered user messages are no longer lost: every TICK lists them as `PENDING_USER=...`,
  `join --resume` shows them again.
- Agent replies written as `- U-001 (note): ...` or `- **U-001:** ...` reach the chat.
- One broken line in `.state/user.jsonl` no longer hides later messages or reuses an id.
- The panel rejects requests whose Host is not `127.0.0.1`/`localhost` (DNS rebinding) and
  accepts `localhost` for the message box too.
- Windows: a second panel no longer binds the same port (`SO_EXCLUSIVEADDRUSE`); a taken port
  moves to the next free one. A rejected message no longer resets the connection.
- `stop`/`finalize` record when a session ended, so the panel shows the real end time.

## [0.6.0] - 2026-10-24

### Added
- Message box in the `chat` page: the user writes to all agents (or `@name` to one). `wait`
  returns early with `USER_MESSAGE`; agents answer under `## Kullaniciya cevap` and the answer
  appears as their reply. Messages live in `.state/user.jsonl`; a late joiner sees earlier ones.
- `POST /api/say` accepts only the page itself: same Host and Origin, JSON body, per-run token.

## [0.5.0] - 2026-10-24

### Added
- `chat`: a live chat view of the session on `http://127.0.0.1:8777` (standard library web
  server, localhost only, no external resources) in its own Edge app window that stops the server
  when closed (default browser without Edge). `chat --replay` plays a finished session back.
- Event log `.state/events.jsonl`: every `wait` tick, join, leave, ring change and `finalize`
  appends new findings, verdicts, answers and withdrawals parsed from the agent files.
  `finalize` keeps it; a new session archives the old one. A broken log never stops a review.
- `install.py` also copies `scripts/chat_events.py` and `scripts/chat_view.py`.

### Changed
- README opens with `docs/agents-chat.gif`: the chat view replaying the real three-agent session
  (claude, antigravity, codex) that built this repo. The real `wait` output stays in a
  collapsible block.
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

[Unreleased]: https://github.com/BerkeZeqoN/multi-review/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/BerkeZeqoN/multi-review/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/BerkeZeqoN/multi-review/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/BerkeZeqoN/multi-review/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/BerkeZeqoN/multi-review/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/BerkeZeqoN/multi-review/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/BerkeZeqoN/multi-review/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/BerkeZeqoN/multi-review/compare/v0.1.0...v0.3.0
[0.1.0]: https://github.com/BerkeZeqoN/multi-review/releases/tag/v0.1.0
