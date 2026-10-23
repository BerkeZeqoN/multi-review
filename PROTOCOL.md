# Multi Review Protocol (tool-agnostic)

Any number of agents audit the same project. Each agent writes **only its own
file** in `<project>/multi-review/` and sits in a **review ring**:

```
c.md > a.md > co.md > c.md
claude reviews antigravity, antigravity reviews codex, codex reviews claude
```

- **review** = the next file in the ring: you challenge its findings.
- **answer** = the previous file in the ring: that agent challenges you, you answer it.
- With 2 agents both roles point to the same file (classic A.md <-> c.md).

The script assigns your roles; `join` and every `wait` print them. This file is
the single source of rules; SKILL.md only adds tool-specific how-to.

Write content in **Turkish** (the user's language); keep ids, file paths, code
and command output in English.

## Identity

- Agent name = your tool name, lowercase: `claude`, `antigravity`, `codex`,
  `gemini`, `cursor`, `copilot`, `opencode`, `qwen`, ...
- Run `roster` first. If your tool's name is already active (a second Claude
  session, for example), pick a suffixed name: `claude-2`.
- **You choose your own file name.** Pass `--file <name>.md` (letters,
  digits, `_ . -`, max 60 chars) and optionally `--prefix <ID>` (1-4
  letters/digits, unique) on `join`. Pick something that identifies you,
  e.g. `claude-opus.md` / `CL`. Taken names are rejected; pick another.
- If you pass nothing, `join` assigns defaults:
  `claude`→`c.md`/`C`, `antigravity`→`a.md`/`A`, `codex`→`co.md`/`CO`,
  `gemini`→`g.md`/`G`, others → first free letters of the name.
- Either way, use exactly the file and prefix `join` prints
  (`own_file=`, `prefix=`): `C-001`, `C-002`, ... The choice is fixed for the
  session; a rejoin keeps it.

## Clock script

`scripts/multi_loop.py` next to SKILL.md. Run from the project root:

```bash
python SCRIPT join --agent NAME --interval 5m --duration 2h --order-mode random|negotiated  # creator
python SCRIPT join --agent NAME                      # later agents: join
python SCRIPT wait --agent NAME [--max-wait 9m]      # block until next tick
python SCRIPT propose --agent NAME --order a,b,c     # negotiated mode: vote ring order
python SCRIPT shuffle                                # random mode: reshuffle now (user asks)
python SCRIPT roster | status                        # ring, roles, clock
python SCRIPT leave --agent NAME                     # you quit early
python SCRIPT stop                                   # user ends it for all
```

Durations: `30s`, `5m`, `10dk`, `2h`, `2sa`, `1h30m`; bare number = minutes.
Add `multi-review/.state/` to `.gitignore` in git repos.

`wait` prints one of:
- `TICK <n> review=a.md review_changed=yes answer=co.md answer_changed=no ring_changed=no`
  (+ `previous_review=... previous_answer=...` when the ring changed) → review round
- `CONTINUE_WAIT` → only with `--max-wait`; run `wait` again
- `SESSION_OVER` → final round, then stop

It also prints `RING vN`, `YOUR_ROLE: review=... answer=...`, and in negotiated
mode `order_status=SETTLED | NEGOTIATING proposals=... waiting_for=... deadline_tick=N`.

## Ring order modes (chosen once by the user at session start)

**random** - the script owns the order. Every join reshuffles the whole ring
with a random permutation (with 3+ agents it never repeats the previous cycle).
A leave just closes the gap (the leaver's reviewer takes over its target).
Agents never argue about order; `shuffle` only when the user asks.

**negotiated** - agents decide the order themselves:
1. A newcomer is appended to the end **provisionally** so everyone can work
   at once, and the script opens a negotiation (only with 3+ agents; 2 agents
   have a single possible ring).
2. Each agent (except text-only `drive` agents, who abstain) writes its
   reasoning under `## Sira tartismasi` in its own file - e.g. "codex should
   review claude because claude's findings are backend-heavy and codex ran the
   tests" - reads the others' reasoning, then votes:
   `propose --agent NAME --order claude,codex,antigravity` (list every active
   agent once; rotations are the same ring).
3. All voters propose the same ring → adopted immediately.
4. No agreement by `deadline_tick` (2 ticks after it opened) → the most-voted
   proposal wins; no votes → provisional order stays. Change your vote freely
   before then. Anyone may reopen a settled order with a new `propose`.

## Joining (first or late)

1. `roster` → choose a unique name. `NO_SESSION` → you are the creator: the
   user must have chosen interval, duration and order mode.
2. `join` → note `YOU=`, `prefix=`, `own_file=`, `YOUR_ROLE`.
3. **Round 0:** explore the project (structure, README, configs, main code,
   tests; run safe read-only checks like lint/type-check/tests).
4. **Late joiner extra:** read your `review` and `answer` files fully. Skim the
   remaining files only to avoid duplicating their findings - you do not
   review them.
5. Write findings to `## Eksiklikler`, add `### <review file> incelemesi` under
   `## Incelemeler`, then start `wait`. Negotiated mode with
   `order_status=NEGOTIATING` → also argue and `propose` (see above).

## When the ring changes (`ring_changed=yes`)

- New `review` file → read it fully, open a new `### <file> incelemesi`.
  Keep the old subsection (do not delete it); mark it `(halka degisti, devredildi)`.
- New `answer` file → read it fully; from now on answer that agent.
- `previous_answer` agent: answer any objection it already raised one last
  time this round, then stop following it.
- `previous_review` agent: your open objections to it stay in your file; its
  new reviewer may adopt them.

## Every TICK

1. Read your `review` file and `answer` file fully when `*_changed=yes`
   (no re-read needed for `no` unless you lost them from context).
2. Verify each claim **against the code** (open the cited file, run the
   command). Never accept "done / %100 tamam / tested" without evidence.
3. Update **your own file only**:
   - `## Incelemeler` → `### <review file> incelemesi`, one line per id:
     `G-003: KABUL | RED | KISMEN - <reason> (kanit: path:line or command output)`.
     Also flag wrong file:line refs, invented APIs, missing edge cases,
     misjudged severity, duplicates across agents, things they missed.
   - `## Bana gelen itirazlara cevap` → answer every objection to your ids in
     your `answer` file: accept & fix the finding, or defend with evidence. Withdrawn:
     `~~C-004~~ (geri cekildi: ...)`, never delete.
   - `## Eksiklikler` → new findings.
   - `## Tur gunlugu` → `- Tur <n> (<time>): <1-line summary>`.
4. Nothing changed and nothing pending → still do a fresh pass for new
   deficiencies. Never write "degisiklik yok" and stop.
5. Run `wait` again. Never end your turn between ticks while the session is
   active - the loop is the task.

### When everything is agreed

Open `## Yeni Konu: <topic>` and research the next area (security,
performance, error handling, tests, accessibility, docs, deployment, missing
features, UX). Prefer a topic no other agent has opened yet.

## Finding format

```markdown
### C-007 [Yuksek] Login rate limit yok
- Yer: `src/api/auth.ts:42`
- Sorun: Brute-force korumasi yok, her istek DB'ye gidiyor.
- Kanit: `grep -n rateLimit src/` → sonuc yok
- Oneri: express-rate-limit, IP+kullanici basina 5/dk.
- Durum: ACIK | KABUL (G, CO) | TARTISMALI | GERI CEKILDI
```

Severity: `Kritik`, `Yuksek`, `Orta`, `Dusuk`. Always write references as
`path:line` (relative to project root) so text-only agents get the code.

## Consensus (ring)

A finding is **ortak kabul** when its reviewer (the agent whose `review` is
your file) marked it KABUL and no RED from any agent that ever reviewed it
remains unanswered. One unanswered RED with evidence → TARTISMALI. A ring
change gives a finding a second reviewer - more confidence, not a reset.

## Rules

- **Write only your own file.** Never edit another agent's md or `.state/`.
- **Do not change project code** unless the user explicitly asks.
- Every claim needs evidence (path:line, command + output). No evidence → RED.
- Be critical, not polite. Self-critique: retract your own wrong findings.
- Past ~600 lines, collapse closed items into `## Kapanan maddeler` (id + 1 line).

## SESSION_OVER - final round

1. Read your `review` and `answer` files one last time; answer anything pending.
2. Append:

```markdown
## Son Durum (<time>)
- Ortak kabul: C-001, G-003, ...  (oncelik sirasiyla)
- Tartismali: C-005 (neden), CO-007 (neden)
- Geri cekilen: ...
- Onerilen ilk 5 is: ...
```

3. Tell the user the session ended, with a short summary and your file path.
