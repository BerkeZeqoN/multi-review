# final.md - multi-review duzeltilecek bulgular (2026-10-23)
> Yazan: claude. Katilan: claude, antigravity, codex. Oturum: 15:40 - 17:40.

## Duzeltilecekler (oncelik sirasiyla)

### F-001 [Kritik] Review file name can collide with an instruction file
- Kaynak: C-007
- Yer: `scripts/multi_loop.py:45`
- Sorun: `claude.md` == `CLAUDE.md` on Windows/macOS; tools load it as instructions.
- Oneri: reject reserved names case-insensitively (`RESERVED_FILES`).
- Kabul: claude, antigravity, codex

### F-002 [Yuksek] Late joiners only comment, nobody makes them research
- Kaynak: C-010
- Yer: `PROTOCOL.md` ("Every TICK"), `scripts/multi_loop.py` (`wait_for_tick`)
- Sorun: new findings are optional in the protocol and never measured.
- Oneri: `--work-mode`, `own_findings/new`, `WORK_WARNING` after 2 idle ticks.
- Kabul: claude, antigravity, codex

### F-003 [Orta] Installer can leave a half-deleted skill on Windows
- Kaynak: A-004
- Yer: `install.py:61`
- Sorun: `rmtree` fails halfway when a terminal sits in the folder.
- Oneri: overwrite in place, then remove leftovers.
- Kabul: claude, antigravity, codex

### F-004 [Dusuk] README shows `roster | status` as a shell pipe
- Kaynak: CO-001, A-001
- Yer: `README.md:75`
- Sorun: `status` runs as a shell command, not a subcommand.
- Oneri: two separate commands.
- Kabul: claude, antigravity, codex

## Tartismali (karar kullanicida)
- A-002: Windows lock `PermissionError`. antigravity: crash risk / claude: only "delete pending", Dusuk. Both agree to retry.

## Kapsam disi
- A-003 (geri cekildi: CI already existed in `75344b2`).
