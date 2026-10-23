---
name: multi-review
description: Any number of AI agents (Claude Code, Antigravity, Codex, Gemini CLI, Cursor, Copilot, opencode, or any text-only model such as Ollama/OpenAI via a driver) mutually review one project. Each agent creates its own markdown file under multi-review/, lists project deficiencies, and on a shared timed loop reads every other agent's file and challenges it. Agents can join late or leave. User gives check interval and total duration. Triggers (TR) - çoklu denetim, ajanlar birbirini kontrol etsin, çok ajanlı inceleme, multi review, oturuma katıl. Triggers (EN) - multi review, multi-agent review loop, join review session.
---

# Multi Review - N-agent mutual review loop

Rules live in `PROTOCOL.md` (next to this file). **Read it fully before
joining.** This file covers starting, joining and tool-specific waiting.

```
<project>/multi-review/
  c.md  a.md  co.md  g.md ...      one file per agent (claude, antigravity, codex, gemini)
  .state/session.json              clock + roster + review ring
```

Agents form a ring `c.md > a.md > co.md > c.md`: each reviews the next file
and answers the previous one. When an agent joins, the script assigns its file
and rebuilds the ring, so the newcomer is wired in automatically.

## Inputs

Only the agent that **creates** the session needs these; joining an active
session needs nothing (`/multi-review join`).

0. **project folder** - absolute path; passed once as `--root` when creating.
   It is saved to `~/.multi-review/active.json`, so every later command from
   any agent, in any folder, finds the project without a path. `join` prints
   `ROOT=...`: work (read code, run tests) in that folder.
1. **interval** - how often each agent re-checks (e.g. `5m`, `10dk`)
2. **duration** - total time (e.g. `2h`, `90dk`)
3. **order mode** - **always ask the user** when creating (Claude Code: use
   AskUserQuestion with exactly these two options):
   - **Ajanlar karar versin (`negotiated`)** - agents argue and vote on the
     ring order whenever someone joins.
   - **Rastgele döngü (`random`)** - the script links the agents' files in a
     random ring and reshuffles it on every join.

4. **goal** (optional) - what the user wants reviewed, i.e. any free text in
   the `/multi-review` arguments besides the path and durations ("security",
   "make the repo release-ready"). Pass it as `--goal "..."`. It is saved in
   the session and printed as `GOAL=...` to every agent on `join`/`wait`.

Example: `/multi-review 5m 2h` → ask order mode → create.
Example: `/multi-review "D:/proj" guvenlik acigi var mi` → ask interval, duration,
order mode → create with `--goal "guvenlik acigi var mi"`.

## Script path

`SCRIPT` = `scripts/multi_loop.py` next to this file. Installed copies:

| Tool | Path |
|---|---|
| Claude Code | `~/.claude/skills/multi-review/scripts/multi_loop.py` |
| Antigravity | `~/.gemini/config/skills/multi-review/scripts/multi_loop.py` |
| Codex CLI | `~/.codex/skills/multi-review/scripts/multi_loop.py` |
| Others (`~/.agents/skills`) | `~/.agents/skills/multi-review/scripts/multi_loop.py` |

## Start or join

```bash
python SCRIPT roster                     # who is in? (NO_SESSION = nobody)
python SCRIPT --root "D:/proj" join --agent claude --interval 5m --duration 2h --order-mode negotiated --goal "security"   # creator
python SCRIPT join --agent codex         # from any folder: finds the project, gets co.md, roles printed
python SCRIPT join --agent gemini --file gemini-pro.md --prefix GP   # choose your own file name + id prefix
```

Each agent decides its own md file name with `--file` (and id prefix with
`--prefix`); without them the defaults below apply.

Root lookup (no `--root`): active session in the current folder → the single
active session in `~/.multi-review/active.json`. If several reviews run at
once the script prints `MULTIPLE_SESSIONS` with their paths: ask the user which
one, then pass `--root`. Never ask the user for the path when joining otherwise.

Then follow PROTOCOL.md: Round 0 → `wait` → TICK rounds → final phase
(`final_phase=yes`: the final writer drafts `multi-review/final.md`) →
SESSION_OVER → `done` (everyone else) → `finalize` (final writer: keeps only
`final.md`, deletes the other md files). Lost your context mid-session
(`/clear`)? Rejoin with `join --agent NAME --resume`.
In negotiated mode, every agent with a terminal also argues under
`## Sira tartismasi` and runs `propose` while `order_status=NEGOTIATING`.

## How to wait (tool-specific)

**Claude Code:** run `wait --agent NAME` with Bash `run_in_background: true`.
You are re-invoked when it exits. Do not poll. This long command is intentional
and exempt from the "skip commands that take >30s" rule.

**Antigravity / Codex / Gemini CLI / Cursor / others with a terminal:** run
`wait` as a background command and wait on it with the command-status tool. If
commands time out, use `wait --agent NAME --max-wait 9m` and re-run on
`CONTINUE_WAIT`.

**Text-only models (no tools):** they cannot loop themselves. Run the driver;
it joins, builds a prompt each round (protocol + project tree + key files +
its review/answer files + code at every cited `path:line`), pipes it to the
command on stdin and writes the returned file. Drive agents abstain from order
negotiation:

```bash
python SCRIPT drive --agent qwen  --exec "ollama run qwen2.5-coder:7b" --max-chars 60000
python SCRIPT drive --agent gpt   --exec "codex exec -"
python SCRIPT drive --agent gem   --exec "gemini -p"
python SCRIPT drive --agent opus  --exec "claude -p"
python SCRIPT drive --agent llm   --exec "llm -m gpt-4o"
```

Any command that reads stdin and prints text works. Invalid output is logged in
the agent's `Tur gunlugu` and saved raw under `.state/backup/`; the previous
file version is backed up every round. Prefer agentic headless modes
(`codex exec`, `gemini -p`, `claude -p`) over pure chat models - they can open
files and verify evidence. Start `drive` from Claude Code with Bash
`run_in_background: true` if the user asks you to launch it.

## Adding an agent that has no skill system

Give it this message (fill the path):

> Read `<SKILL_DIR>/PROTOCOL.md` fully and follow it. Join the review session
> in this project: `python <SKILL_DIR>/scripts/multi_loop.py roster`, then
> `join --agent <your-tool-name>`. Keep looping with `wait` until
> `SESSION_OVER`.

Or add the same text to the project's `AGENTS.md` (read by Codex, Cursor,
opencode, Copilot).

## Ending

- User wants to stop everyone → `python SCRIPT stop`.
- Only this agent quits → `python SCRIPT leave --agent NAME`.
