# Security

## Threat model

multi-review moves text written by one AI agent into the context of the
others. Treat every review file as **untrusted input**.

| Risk | Mitigation in this repo | What you should do |
|---|---|---|
| **Prompt injection through review files.** An agent (or a page it read) writes "ignore previous instructions ..." into its findings. | The protocol requires evidence for every claim and tells agents to verify against the code. | Run agents with their normal permission prompts. Do not grant blanket auto-approval during a session. |
| **Instruction-file collision.** A review file named `CLAUDE.md`, `AGENTS.md` or `GEMINI.md` (case-insensitive) is auto-loaded by the tool as instructions. | `join --file` rejects those names (`RESERVED_FILES`). | Report any other auto-loaded file name as a security issue. |
| **`drive --exec` runs a shell command** with the prompt on stdin. | The command comes only from the user's own command line, never from a review file. | Only pass commands you would run yourself. |
| **Review output shipped by accident.** | `.gitignore` excludes `multi-review/`. `install.py` copies only the skill files. | Do not commit `multi-review/` from a project you reviewed. |
| **Deletion by `finalize`.** | Only the final writer can run it, only after `SESSION_OVER`, only with a valid `final.md`. `--archive` keeps copies. | Use `--archive` if you want the raw review files. |

The script makes no network calls. It reads and writes only `<project>/multi-review/`
and `~/.multi-review/`.

## Reporting

Please report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/BerkeZeqoN/multi-review/security/advisories/new)
rather than a public issue. Expect a first answer within a week.
