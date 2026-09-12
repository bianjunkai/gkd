# Repository Guidelines

归刻（GKD）is a Markdown-driven personal GTD workspace. Version 0.1.0 is a working **local MVP**: capture, clarify, confirm, write to Markdown, complete, undo and search all function end to end. It is not a completed V1 or a production release.

Authoritative documents, in reading order:

| Document | Role |
| --- | --- |
| `产品需求文档.md` | What to build, the rules, and acceptance criteria (V1 baseline) |
| `开发计划.md` | Delivery order, technical plan, estimates, release gates |
| `README.md` | Actual run commands, configuration, and current limitations |
| `docs/MVP验收记录.md` | What has been verified, with evidence, and what has not |
| `docs/实现决策.md` | Tradeoffs actually taken in the code vs. the PRD blueprint |

When code and documents disagree, **the code is the fact and the documents are the bug** — update the document in the same change. See "Documentation Currency" below.

## Project Structure & Module Organization

```text
apps/miniprogram/src/     原生微信小程序：11 个页面（TS + WXML + WXSS）
apps/web/src/             React 本地工作台（Vite）
packages/client/src/      两端共用类型、传输与幂等客户端
services/api/app/         FastAPI 应用：领域、存储、变更、提案、队列、备份
services/api/tests/       后端单元、API、恢复与安全边界测试
tests/e2e/                浏览器与小程序控制器端到端测试
scripts/                  运行入口与离线 npm 缓存工具
docs/                     验收记录、实现决策、原始需求归档
```

The WeChat Mini Program is the **primary client**; the React Web workspace exists for local development, integration and browser regression. Do not treat Web as a replacement for the Mini Program.

Runtime data (`apps/*/dist/`, `.data/`, `.cache/`, `node_modules/`, `.venv/`) is generated and git-ignored. Never commit `.data/`, `.env`, or backups — they contain raw user text and credentials.

## Build, Test, and Development Commands

Dependencies are already installed in this workspace. Run from the repository root:

| Command | Purpose |
| --- | --- |
| `npm.cmd run build` | Build Web, build Mini Program, check 11 pages' bindings |
| `npm.cmd start` | Start API and serve the built Web workspace on :8000 |
| `npm.cmd run api:dev` | API with source reload (only one API per data dir) |
| `npm.cmd run dev` | Web dev server (:5173, proxies to :8000) |
| `npm.cmd run typecheck` | TypeScript check for Web, Mini Program, shared client |
| `npm.cmd test` | Shared-client Vitest unit tests |
| `npm.cmd run test:api` | Backend pytest (temp data in `.cache/pytest-*`) |
| `npm.cmd run test:e2e` | Browser + Mini Program controller e2e; **build first** |
| `npm.cmd run check` | Typecheck + unit tests + build (no backend, no e2e) |
| `npm.cmd run verify` | Full regression: typecheck, unit, build, backend, e2e |
| `npm.cmd run backup -- create\|restore` | Offline SQLite backup / restore |

Use `npm.cmd` on Windows PowerShell. Backend tests can also run directly:

```powershell
.\services\api\.venv\Scripts\python.exe -m pytest services\api\tests -q -p no:cacheprovider
```

`-p no:cacheprovider` avoids permission warnings from pre-existing `.cache/pytest-*` directories. Passing `-p no:cacheprovider` does not disable tests.

E2E uses local Chrome by default (override with `GKD_BROWSER`) and port 18640 (override with `GKD_E2E_PORT`). It never touches the real `.data/`.

Ruff is configured but **not installed** here, so lint and coverage are not enforced. ESLint/Prettier are not configured.

## Coding Style & Naming Conventions

- Python: 4 spaces, type hints on public functions, `snake_case`. Line length follows the Ruff config in `services/api/pyproject.toml`.
- TypeScript/JSON/YAML: 2 spaces, `snake_case` for wire/schema fields, `camelCase` for local variables.
- Markdown: UTF-8, preserved Chinese prose, descriptive headings, blank lines between blocks, language-tagged code fences.
- Preserve domain terms verbatim: `Capture`, `Task`, `Task Group`, `Proposal`, `Change Set`, `Job`, `Folder`.
- Errors are raised as `AppError` with a stable code, an HTTP status, and a retryable flag (`services/api/app/errors.py`); do not raise bare `HTTPException` in service code.
- Every state-changing API requires an `Idempotency-Key` header plus a request fingerprint. New write paths must keep this contract.

## Testing Guidelines

Frameworks: pytest (`services/api/tests/`), Vitest (`packages/client/src/*.test.ts` and `apps/miniprogram/validate.test.mjs`), and `node --test` (`tests/e2e/`).

Name tests by observable behavior, not implementation, e.g. `duplicate_submission_does_not_duplicate_tasks`, `test_undo_after_new_edit_uses_version_preview`.

New or changed write logic must cover, at minimum:

- idempotent retry of the same request (duplicate submission produces no duplicate tasks),
- version/content-hash conflict rejection instead of silent overwrite,
- recovery after an interrupted multi-file Change Set,
- "no business file is written before confirmation".

Prefer isolation: tests must use temporary data roots and must not read or mutate the real `.data/`. Never inject faults into real user text.

Acceptance-criteria coverage lives in `产品需求文档.md` section 11.3; map new tests to `FR-###` IDs when practical.

## Documentation Currency

Several documents describe the same system at different points in time, so **stale claims are the main documentation risk here.** Before finishing any change that alters behavior, commands, test counts, or a frozen decision:

1. Update `README.md` if commands, configuration, layout, or limitations changed.
2. Append evidence to `docs/MVP验收记录.md` (date, exact command, actual result). Record failures honestly; do not upgrade a partial pass to a full pass.
3. Update `docs/实现决策.md` when a tradeoff is taken that departs from the PRD.
4. If a **frozen decision** in `产品需求文档.md` section 15 changed, update that row and note the change — do not leave the PRD contradicting the code.
5. Keep `开发计划.md` section 0 status and verification numbers current.

Known historical drift, already corrected on 2026-09-10 but worth not repeating: schema version 2 made body `gtd:task:*` blocks authoritative while PRD decision **D-03** still said Front Matter `tasks` was authoritative, and section 0 verification counts lagged the real suite by dozens of tests; both were then synced with the code. Verify counts by running the suite, not by copying an older number.

## Architecture & Data Handling

Markdown is the source of truth for Task/Group content; the database holds only non-rebuildable metadata (identity, folders, proposals, idempotency/change records, job queue, audit) plus rebuildable indexes.

- **Format 2 is current.** Front Matter carries group attributes only; tasks live in `<!-- gtd:task:start -->` / `<!-- gtd:task:end -->` body blocks under a `<!-- gtd:tasks:start -->` region. Format 2 must not also carry a `tasks` key in Front Matter. Format 1 (Front Matter `tasks`) is still read for compatibility; opening, starting, or reindexing must never bulk-rewrite files.
- **Raw Captures are preserved.** Text is written to an open JSON file before the database row is registered. `client_capture_id` deduplicates; startup isolates corrupt records instead of discarding them.
- **Writes are gated.** Capture → Proposal → per-item user confirmation → Change Set. No business file is created before confirmation. Undo produces a new, larger revision rather than rewinding history.
- **Conflicts refuse to overwrite.** Version, content hash, or target path mismatch aborts the write.
- Persona/limit rules: one local API process per DATA_ROOT (OS file lock); same-workspace writes are serialized; external model calls never hold the workspace write lock. Do not add Uvicorn workers to scale writes.
- YAML is parsed safely with duplicate-key, alias, tag, and nesting checks. Unknown legitimate fields and unmanaged body prose are preserved; comment/whitespace/quote style is not guaranteed to round-trip.
- External AI sends only the current raw text, reference time, timezone, and explicitly selected group titles/tags. The default engine is a deterministic, clearly labeled local rule parser — **not** generative AI. Do not describe the local parser as an AI model in UI copy or docs.

Use anonymized examples in docs and tests. Never commit credentials, real raw text, or the data directory.

## Commit & Pull Request Guidelines

Git was initialized on 2026-09-10 with the working local MVP as its baseline. Commits before that do not exist; do not cite prior history. `.gitattributes` fixes all text files to LF (matching `.editorconfig`); do not reintroduce CRLF.

- Subjects: concise and imperative, optionally prefixed (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), e.g. `docs: sync D-03 with storage format 2`.
- Keep commits focused on one intent; separate behavior changes from documentation sweeps.
- Reference affected `FR-###` IDs and any related issue in the body.
- Report the exact validation performed (`npm.cmd run verify` or the narrower command plus its real result).
- Include screenshots for interface changes; `.cache/qa/*.png` is regenerated by e2e and is not versioned.
- State explicitly what remains unverified. The Mini Program has never been validated in the official developer tools or on a real iOS/Android device, and no real AI provider call has been made — do not imply otherwise.
