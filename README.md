# multi-review

Several AI coding agents review the same project and check each other's work.
Supported: Claude Code, Antigravity, Codex CLI, Gemini CLI, Cursor, Copilot,
opencode, and text-only models (Ollama, OpenAI, ...) through a driver.

Each agent writes its findings into its **own markdown file** and picks the
file name itself. The agents sit in a **review ring**. Every agent challenges
the next agent's file and answers the objections raised in the previous one.
A shared clock advances the rounds until the session time runs out.

```
<project>/multi-review/
  c.md  a.md  co.md  gemini-pro.md ...   one file per agent (name chosen by the agent)
  .state/session.json                    clock + roster + review ring
```

```
c.md > a.md > co.md > c.md
claude reviews antigravity, antigravity reviews codex, codex reviews claude
```

## Features

- **N agents, late join / early leave.** The ring rebuilds itself whenever
  someone joins or leaves.
- **Two ring-order modes.** In `random` the script reshuffles the ring. In
  `negotiated` the agents argue and vote with `propose`. If they have not
  agreed after 2 ticks, the most-voted ring wins.
- **Evidence-first protocol.** Every claim needs a `path:line` or a command
  output. A claim without evidence is rejected.
- **Text-only models.** `drive` builds each round's prompt (protocol, project
  tree, key files, cited code) and pipes it into any stdin command.
- **One result file.** On the last tick the final writer drafts
  `multi-review/final.md`: only the agreed findings that must be fixed. The
  others object to the draft, and `finalize` then deletes every other md file.
- **No dependencies.** Python 3.8+ standard library only.

## Install

Copy the folder into your tool's skill directory:

| Tool | Path |
|---|---|
| Claude Code | `~/.claude/skills/multi-review/` |
| Antigravity | `~/.gemini/config/skills/multi-review/` |
| Codex CLI | `~/.codex/skills/multi-review/` |
| Others | `~/.agents/skills/multi-review/` |

```bash
git clone <this-repo> ~/.claude/skills/multi-review
```

For a tool without a skill system, give it this message:

> Read `<SKILL_DIR>/PROTOCOL.md` fully and follow it. Join the review session
> in this project: `python <SKILL_DIR>/scripts/multi_loop.py roster`, then
> `join --agent <your-tool-name>`. Keep looping with `wait` until
> `SESSION_OVER`.

## Usage

```bash
S=~/.claude/skills/multi-review/scripts/multi_loop.py

# creator: project root, interval, duration, order mode
python $S --root "D:/proj" join --agent claude --interval 5m --duration 2h --order-mode negotiated

# others join from any folder; each picks its own file name + id prefix
python $S join --agent codex
python $S join --agent gemini --file gemini-pro.md --prefix GP

python $S wait --agent codex                       # block until next tick
python $S propose --agent codex --order claude,codex,gemini
python $S roster | status
python $S leave --agent codex                      # quit early
python $S stop                                     # end for everyone
python $S done --agent codex                       # after SESSION_OVER
python $S finalize --agent claude [--archive]      # final writer: keep only final.md

# text-only model
python $S drive --agent qwen --exec "ollama run qwen2.5-coder:7b" --max-chars 60000
```

In Claude Code, run `/multi-review 5m 2h`, or `/multi-review join` to join a
running session.

## Files

- `SKILL.md`: skill entry point (start, join, tool-specific waiting)
- `PROTOCOL.md`: tool-agnostic rules (roles, ticks, finding format, consensus)
- `scripts/multi_loop.py`: clock, roster, ring and driver
- `tests/`: `python -m unittest discover -s tests`

## License

MIT
