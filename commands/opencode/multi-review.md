---
description: Multi-agent review loop - create, join or manage a multi-review session
---

Use the `multi-review` skill (load it with your skill tool; if that is not
available, read `~/.agents/skills/multi-review/SKILL.md`). Its `SKILL.md` and
`PROTOCOL.md` are the only rules; this command only routes to them. The
scripts are in the same folder: `~/.agents/skills/multi-review/scripts/multi_loop.py`.

Arguments: `$ARGUMENTS`

- Empty, `join` or a session name: join the running session, as SKILL.md
  says for joining (ask nothing about interval, duration, order or work mode).
- A project path and/or durations (e.g. `C:\proj 5m 2h security`): create a
  session, asking the user what SKILL.md "Inputs" says to ask.
- `chat`, `stop`, `status`, `rename ...`, `open-ide antigravity`: run that
  subcommand of `multi_loop.py`.

Your agent name is `opencode`. Keep looping with the printed `NEXT:` command
until `SESSION_OVER`.
