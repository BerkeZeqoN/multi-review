---
description: Join the running multi-review session (no questions asked)
argument-hint: "[session-name] [--resume]"
---

Use the `multi-review` skill in **join** mode. Its `SKILL.md` and `PROTOCOL.md`
are the only rules; this command just picks the join path. Do not ask the user
about interval, duration, order or work mode: those belong to the creator.

1. Read `PROTOCOL.md` of the multi-review skill fully.
2. `python <skill>/scripts/multi_loop.py roster`. If it prints
   `MULTIPLE_SESSIONS`, use the session named in `$ARGUMENTS`; if none is named,
   show the user the listed sessions (name, goal, project) and ask which one.
3. Join under your tool's name and a file name you choose (never `claude.md`,
   `agents.md`, `gemini.md`, `final.md`):
   `python <skill>/scripts/multi_loop.py --session <name> join --agent <you> --file <you>-review.md`
   If it says your name is already active and that is you (you lost context,
   e.g. after `/clear`), run the same command with `--resume`.
4. Work in the printed `ROOT=` folder. Do Round 0 as the protocol says, then
   run the command from the `NEXT:` line in the background and keep looping
   until `SESSION_OVER`.
