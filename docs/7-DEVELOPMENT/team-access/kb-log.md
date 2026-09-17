# Team Access + Analytics — KB Log

Every challenge + solution from the implementation run is recorded in Obsidian at `KB/open-notebook/` (kb-create format: challenge / cause / solution / result / related files / tags, frontmatter `knowledgebase` + `llm-reference`). This file is the repo-side index into the vault.

## Recorded

| Note | One-liner |
|---|---|
| `KB/open-notebook/ask-matt skill recovery.md` | Deleted wrong skill; restored from `~/.agents/.skill-lock.json` source URL |
| `KB/open-notebook/Local SurrealDB must be v2.md` | v3 binary breaks v2-syntax migrations; use `~/.local/bin/surreal-2.6.5` via `SURREAL_BIN`; never reuse a data dir across major versions |
| `KB/open-notebook/SurrealDB env-var pitfalls and reload auto-migrations.md` | Silent version-0 masks connection failures; uvicorn `--reload` auto-applies pending migrations when you edit migration files |
| `KB/open-notebook/SurrealDB record fields reject string record ids.md` | `record<...>` fields reject plain strings on the `repo_create`/`repo_update` path; write them via raw CREATE/UPDATE CONTENT with `ensure_record_id` (`create_user`/`update_user` pattern) |

## Pending capture

(None yet.)
