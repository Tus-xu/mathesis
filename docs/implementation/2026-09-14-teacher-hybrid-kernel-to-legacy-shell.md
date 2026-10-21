# Teacher Hybrid Kernel to Legacy Shell

- Status: ready-for-luna
- Updated: 2026-09-14 (late PM — Sol closeout v2: **workspace has advanced far beyond the morning picture**; a real cross-dialect test regression was found and fixed; §"Sol closeout v2" supersedes §"PM Sol closeout" wherever they disagree)
- Branch/worktree: `D:\math-arena` on `master`; shared, heavily dirty working tree. `D:\frontend` is a separate shared, heavily dirty `master` worktree.

## Objective

Make the teacher's first message from the legacy “备课中心” create and continue the VNext server-owned flow:

`TeachingSession → conversation → TeachingBlueprint → T23 direct-slice context / diagnostic / class-profile evidence → T24 LessonPlanSpec + TeachingDeckSpec`.

The legacy lesson-plan and PPT editors are display/editor shells only. Their adapter is one-way; server-side spec, revision, and lineage remain the sole business facts. The user-facing availability policy is: an authenticated teacher can enter the conversation-first surface; starting a class-scoped session requires an authoritative confirmed class relationship; a transient 8000 authority failure must not be presented as “this teacher has no permission.” This does **not** authorize cross-teacher, cross-class, or private-artifact access. Class learning-profile data and private artifacts remain fail-closed until authoritative ownership is verified.

## Current state and evidence

- Repository instructions read: `D:\math-arena\AGENTS.md`, `D:\math-arena\services\teacher-platform\AGENTS.md`, and `D:\frontend\AGENTS.md`.
- `D:\math-arena` has widespread tracked and untracked changes. In particular, all Teacher Prep R12–R20/T24 sources and migrations are untracked; `app/core/security.py` is dirty. Do not overwrite, format, or implicitly adopt them.
- `D:\frontend` is also broadly dirty. Relevant tracked files are dirty: `src/router/index.js`, `src/pages/teacher-v3/prep/PrepCenterView.vue`, and `PrepEditor.vue`. Relevant bridge files are untracked: `src/router/slidesCompat.ts`, `src/pages/teacher-v3/SlidesCompatRedirect.vue`, and `src/pages/teacher-v3/prep/legacyArtifactAdapter.ts`. The user explicitly handed off ownership for this bounded work; unrelated dirty files remain preserved and no clean-baseline claim is made.
- The current front-end routing source contains the canonical route `/teacher-v3/prep/session/:sessionId`; the teacher home resolves to `/teacher-v3/prep`. `slides` mounts `SlidesCompatRedirect`, not `SlidesView` directly.
- `slidesCompat.ts` accepts the legacy PPT editor only when all are present: `mode=vnext`, a session identity, and a deck/artifact identity. `SlidesCompatRedirect.vue` loads frozen `SlidesView.vue` only in that case; bare/invalid old slides URLs go to the canonical session or `/teacher-v3/prep`.
- `legacyArtifactAdapter.ts` reads a VNext identity in compatibility order `id`, `spec_id`, then `deck_id`; it projects content into the incumbent editor model and does not persist legacy state back.
- `PrepCenterView.vue` sends the first message through `createLegacyTeachingSession` to `/api/teacher-v3/prep/conversations`, then replaces the URL with `/teacher-v3/prep/session/:sessionId`. `PrepEditor.vue` uses `readLegacyArtifactIdentity` for the VNext deck path.
- Fresh local front-end verification on 2026-09-14: the five focused bridge/availability suites (`v3SlidesRetired`, `teacherHybridAdapters`, `teacherHybridCanonical`, `legacyKernelBridge`, `teacherHybrid.red`) passed: 5 files, 39 tests. This verifies assembly contracts, not a real service/browser Golden Path.
- T24 evidence read from `D:\ppt-rescue-control\20260911-math-ai-platform-vnext\T24\FINAL-GATE.md` and `E2E-REPORT.md`: spec/revision/lineage and 8100→8200→8201 artifact flows have substantial API/engine evidence, but real browser + real-8100 editor E2E was explicitly not run. T24 is not a release approval.
- T25/T25.1 evidence read from `T25\EVIDENCE-INDEX.md`, `GOLDEN-DEMO-REPORT.md`, `P0-P1-P2.md`, `RELEASE-DECISION.md`, `SECURITY-RESULTS.md`, and `T25.1\{EVIDENCE-INDEX,FRONTEND-CLOSEOUT-EVIDENCE}.md`. Both retain the Teacher Domain `NO-GO` decision.
- The requested control directory has no `T23` directory. T25 cites T23 only as `ENGINEERING_PASS` pending Sol/human review; no primary T23 evidence was available for fresh review. Treat T23 runtime acceptance as UNKNOWN until a primary evidence path is supplied or the path is restored.
- `app/teacher_prep/service.py` is an untracked externally owned 91,735-byte file. Static inspection now shows explicit `await session.flush()` before the command row and before a new blueprint revision, including `_log_command`. This is a plausible repair for the historical FK error, but it is not ownership-cleared, independently regression-tested, or proven through the real flow. P0-1/P0-2 remain open.
- The 8000 membership adapter first probes a route documented by T25 as absent, then passes the caller token to `/api/classes/{class_id}/members`. T25 demonstrates that 8100-issued tokens are not accepted by 8000, producing a 503 authority error. Do not weaken tenant checks to hide this mismatch.
- `app/storage/artifacts.py` now contains ownership and ticket helpers, but it is untracked. T25's private artifact read, deck-revert write, and 8100 token-revocation findings remain P0 until all reachable routes are proven against the final owned implementation.
- Real browser probe on 2026-09-14 (Playwright CLI, CSS viewport 1440x900): Vite is reachable at `http://127.0.0.1:5176/teacher-v3/prep`; 8000 `/api/health`, 8100 `/healthz`, and 8200 `/healthz` all returned 200. 8201 `/healthz` was refused, as required for an internal-only service. A real teacher login succeeded, and the first-message attempt reproduced the screenshot's false permission banner before the catalog fix. Direct evidence then showed the three 8100-local class IDs were absent from 8000, while 8000 `/api/classes/mine` returned an empty authoritative list. After the fix, the browser showed `当前账号还没有已确认的教学班级，请在统一身份平台创建或加入后重试` and kept “开始备课” disabled; no false permission denial remained. The same 1440x900 browser navigated bare `/teacher-v3/slides` back to `/teacher-v3/prep` and showed the integration toast. The browser request ledger contained only 5176 `/api/teacher-v3/*` calls and no 8201 URL. This proves the entry/authority boundary and its honest empty state, but not a real Golden Path because the current teacher has no 8000 class relationship.

## 2026-09-14 PM Sol closeout: re-verification and real-browser probe

All items below were re-verified fresh on 2026-09-14 afternoon (read-only; no product code changed). Evidence scripts/screenshots live in an ephemeral session workspace and are reproducible from the inline details.

### Workspace delta since the morning session (owner/conflict policy refresh)

- `D:\frontend`: the VNext bridge is now **committed** as `fcb6451` (slidesCompat.ts, SlidesCompatRedirect.vue, legacyArtifactAdapter.ts, legacyKernelBridge.ts, prep API/恢复域 + focused tests). The bridge files are no longer dirty. 21 unrelated dirty files remain in that worktree and stay out of scope.
- `D:\math-arena`: `app/core/security.py` and `app/storage/artifacts.py` are no longer dirty (now tracked at their reviewed state). `app/teacher_prep/**` is tracked and carries **uncommitted diffs** (+88/−17 across 8 files): `conversation/handler.py` adds a post-blueprint `await session.flush()`; `conversation/orchestrator.py` calls `project_specs` with `manage_idempotency=False` and rewrites `_current_selection` to prefer the snapshot with a fallback to the session's authoritative `class_id`; `conversation/views.py` adds a `teaching_deck` workspace summary (SpecProjectionRow → PptV2DeckSpecRecord) for 8200 ingest recovery; `conversation/router.py` makes body `session_id` optional (REST path authoritative); `service.py` adds the `manage_idempotency` parameter and materializes `srow.updated_at = _now()` after flush. **Owner of these uncommitted diffs is unconfirmed** (presumed the prior Sol/agent session). Policy unchanged: never commit, revert, or build on them without owner confirmation; work cards B/C must start from an owner-approved baseline.
- Untracked and unrelated: `deliverables/student-chain-fix/`, `services/research-vnext` rv09 scripts/fixtures. Do not touch.

### Code re-verification (bridge + P0 surfaces, file:line)

- Frontend: canonical route `tv3-prep-session` at `src/router/index.js:66-70`; `/teacher-v3/slides` mounts `SlidesCompatRedirect` (`index.js:80`); gate requires `mode=vnext` ∧ session ∧ deck identity (`src/router/slidesCompat.ts:47,56,107-111`), bare fallback → `/teacher-v3/prep` (`:158-164`); identity order `id → spec_id → deck_id` with display-only projection (`legacyArtifactAdapter.ts:22,49-51,89-90`); first-message path `PrepCenterView.vue:55,60` → `POST /api/teacher-v3/prep/conversations` → URL replace. Note: `prepApi.ts` actually lives at `src/features/teacher-prep-v2/api/prepApi.ts` (authority_unavailable classification at `:61-64,76-77,84-86`; true 401/403 mapping preserved `:76-77`). `localStorage` holds only a UI "migration notice read" flag; no business facts, no simulated progress.
- Backend: parent-before-child flush verified in the current tree — `create_session` flushes `TeachingSessionRow` (`service.py:296-313`) before `_log_command` (`:314`), whose first statement also flushes (`:2088`); `propose_blueprint` flushes before the revision insert (`:619-631`); `project_specs` has `manage_idempotency: bool = True` (`:1651`). Catalog route `/api/teacher-v3/classes` (`app/domains/catalog/router.py:121-148`) calls 8000 `GET /api/classes/mine` via `HttpxApiTransport` with explicit 401/403/503 mapping; `normalize_authorized_class_items` filters role ∈ {teacher, owner, assistant} ∧ confirmed (`app/domains/catalog/service.py:82-118`). Artifact/ticket helpers exist and are wired: `acl_allows` (`app/storage/artifacts.py:160`), `spec_access` (`:178`), `issue_ticket` (`:228`), `redeem_ticket` with ACL recheck (`:317,387`), `revoke_artifact_access` (`:412`); enforced at `app/teacher_prep/specs/router.py:21-47` and `app/ppt_v2/api/router.py:396,427,443,461`.
- **Still open (confirms P0s):** `app/core/security.py` validates dual-issuer JWTs but has **no `sid`/`security_version` revocation enforcement** (`:46-82`; `security_version` referenced only in `app/platform_contracts/access.py:74`); no explicit parent-before-child FK regression test exists in `tests/unit/teacher_prep/` (only behavior-level coverage at `test_conversation_service.py:197,254`).
- Control-dir evidence re-read: T23 lives at `ppt-rescue-control/20260912-sol-luna-frontend/T23/` (ENGINEERING_PASS, pending human review — not in the 20260911 dir); T24 = PARTIAL PASS (real 8100→8200→8201 export flow, but no real-browser+real-8100 editor E2E); T25 = NO-GO (P0-4 revoked-token acceptance, P0-5 cross-teacher PRIVATE read, P0-6 revert unauthorized write, plus flush-FK and Golden-Path gaps); T25.1 = ENGINEERING_PARTIAL, still NO-GO.
- Focused frontend suites re-run: 5 files, **40/40 PASS** (`v3SlidesRetired` 21, `teacherHybrid.red` 7, `teacherHybridAdapters` 4, `teacherHybridCanonical` 4, `legacyKernelBridge` 4). (Morning record said 39; the red suite has grown by one test.)

### Real-browser probe (headless Chromium, 1440×900, real services, real data, no fabrication)

Environment: Vite 5176 up; 8000 `/api/health` up; **8100 and 8200 were started for this probe** (`uv run uvicorn` only — no seed/migrate); 8201 unreachable (required). Login used the real SMS flow (demo code shown in UI) with the identity selector set to 教师端.

1. **Entry + availability (teacher 13800000000, no confirmed class):** login → `/teacher-v3/prep`. `GET /api/teacher-v3/classes` → `200 {"items":[]}` (8000 `/api/classes/mine` empty for this account). UI shows the honest banner 「当前账号还没有已确认的教学班级，请在统一身份平台创建或加入后重试」 — no false "permission denied" — and the first-message send button `[data-testid="ailp-home-send"]` is **disabled** (`PrepHome.vue:27` requires `selectedClassId`). Net effect: a teacher without a confirmed 8000 class **cannot start any prep session**. This is the "permission problem" the user sees and wants relaxed (see Product decision below).
2. **Golden-path head (teacher 13900009971, owner of class 「E2E 班级」 `f80f3d90-ddd3-4658-ab67-f9805ab80660`, confirmed=True in 8000):** class auto-selected (`PrepHome.vue:152`); typed first message → `POST /api/teacher-v3/prep/conversations` → 200 → browser landed on the canonical `/teacher-v3/prep/session/b057334f-a035-41a7-85c5-9e23f0338d78`. **Session creation through the real UI works.**
3. **T23 conversational context:** the orchestrator proposed a textbook choice listing **20 authorized pre-slice textbooks** (人教A版2019/北师大版/苏教版/课程标准… — no PDF, no `TextbookPage`); after selection it proposed section 「2.3 二次函数与一元二次方程、不等式」; conversation events streaming worked (`/events` + conversation GETs all 200). No 8201 calls at any point.
4. **NEW BLOCKER (blueprint render fail-closed):** after the section selection the blueprint arrived but the workspace rendered the honest error state 「暂时无法加载备课会话 …（组件契约校验失败：props.objectives[1]/[5]/[8]: 包含 HTML 标记（模型/服务器不得下发任意 HTML/CSS））」, `request_id: fb77563b93d94ebe`. The frontend contract validator correctly refuses HTML in model-authored text, and server state stayed consistent (retry returns to the pending selection) — but `confirm_proposal` → `project_specs` → LessonPlanSpec/TeachingDeckSpec/deck-shell are **unreachable through the UI** behind this. The real Golden Path stops here in the browser today; this must be fixed server-side (sanitize/strip HTML from model-authored blueprint fields before persist/projection, or an explicit reviewed allowlist) — never by weakening the frontend validator.
5. **NEW display-truth violation (class label):** the workspace class chip shows 「高二(3)班」 while the authoritative 8000 class name is 「E2E 班级」. `session_dto` (`service.py:187-193`) documents `class_name` as coming from **8100's local `classes` table** via `load_class_names` (`:211`) — i.e. the visible label is not the 8000-authoritative name. Fix: derive the chip label from the 8000-derived catalog mapping (server-side), not the 8100-local table.
6. **Compat gate boundaries (logged-in session):** bare `/teacher-v3/slides` → redirected to `/teacher-v3/prep` ✅; `/teacher-v3/slides?mode=vnext&session=xxx&deck=yyy` (syntactically present, bogus ids) → stays on the slides route and the unknown-deck fetch 404s (server rejects; minor UX gap — consider canonical fallback for nonexistent ids); `/teacher-v3/prep-immersive` remains reachable as the historical shadow route (not a navigation dependency). **Zero browser→8201 requests across all probes** ✅.

### Product decision input (user, 2026-09-14) — requires Luna confirmation

The user states the current gating is too strict: 「每一个老师都可以使用，不需要这么严格的」. Safe interpretation proposed (amends Assumption 1 below; still respects the T25 P0s):

- Every successfully **authenticated** teacher can reach the conversation-first surface and **create/start a session without a confirmed class**; class-scoped enrichment (class learning-profile evidence, 8000 membership-dependent cards) degrades to an explicit "unavailable/no class bound" state instead of blocking the whole flow.
- Availability relaxation must NOT weaken security: cross-teacher/cross-class private reads, deck revert authorization, and revocation enforcement remain fail-closed P0s.
- Concretely: relax the `PrepHome.vue:27` send gate (allow empty `selectedClassId` with a visible "class not bound" affordance), make backend session `class_id` optional with degraded evidence, and keep 401/403 vs 503 semantics. This is a frozen-assumption change (Assumption 1 and acceptance criterion 1) — Luna/human must approve before implementation.



## 2026-09-14 Sol closeout v2 (SUPERSEDES the PM section above where they disagree)

The morning/PM sections above describe a workspace in which Work cards B/C/E were largely **unstarted**. That is no longer true. A read-only + verified-fix pass later on 2026-09-14 established the following, with fresh command output.

### v2-0. Baseline drift disclosure (read this first)

This document previously said P0-1/P0-2 were open, Work card C (artifact/deck authorization) was untouched, E-1 was unfixed, and `gateway.py:222` still had the ineffective `thinking` shape. **All four statements are now false against the working tree.** Sections above are retained for provenance but must not be used as the current status. Where this v2 section and any earlier section disagree, **v2 governs**.

### v2-1. What has actually landed in the working tree (uncommitted)

`D:\math-arena` `master` @ `c26ed11`. `git diff --stat -- services/teacher-platform` = **27 files changed, 617 insertions(+), 113 deletions(-)**, plus 8 untracked files.

**Capability now present in-tree (all uncommitted):**

| Area | Evidence | Earlier doc said |
|---|---|---|
| P0-1/P0-2 FK flush ordering | `fac13bc` committed fix; regression lock `tests/unit/teacher_prep/test_uow_fk_ordering.py` (3 tests) **now actually runs and passes** | "open / unproven" → **now GREEN, see v2-2** |
| E-1 HTML-in-model-text | `app/teacher_prep/text_safety.py` (new, 61 lines): real-HTML-tag whitelist regex + `strip_html_tags()`; wired into `blueprint/schema.py`, `conversation/catalog.py`, `conversation/schema.py`, `diagnostic/schema.py`, `context/builder.py` (`:133,144,223,232`) | "unfixed P1" → **fixed + wired** |
| E-3 optional class (end-to-end) | Backend: `conversation/orchestrator.py` `_class_ref()` / `_require_class_id()`; `class_id: str \| None` threading through ~20 call sites; class removed from `selection_complete` missing list; `context_snapshots.class_id` nullable; migration `c8d9e0f1a2b3_e3_optional_class_id.py`. Frontend: `PrepHome.vue` send gate is now `:disabled="!draft.trim() \|\| busy"` (**`!selectedClassId` removed**); copy keys `prep.home.class_optional` / `class_optional_hint` added. Tests: `test_e3_optional_class.py` (29 tests together with text_safety) | "needs Luna approval, unimplemented" → **implemented; still needs Luna ratification, see v2-5** |
| P0-5/P0-6 artifact/deck authorization | `app/storage/artifacts.py`: `acl_allows:160`, `spec_access:178`, `issue_ticket:228`, `redeem_ticket:317`, `revoke_artifact_access:412`. **Wired**: `ppt_v2/api/router.py:42,396,427,443,461,464`; `teacher_prep/specs/router.py:34` | "Work card C untouched" → **wired, needs two-teacher proof** |
| P0-3 / latency | `gateway.py:220-225` now sends `{"enable_thinking": self._settings.spark_thinking}` (the previously-ineffective `thinking:{type:...}` shape is gone); `config.py:92 spark_thinking=False`; `config.py:120` routes `teacher_prep_*` to `model_longform` (80B) with `model_spark` (4B) added as fallback in `gateway.py:152-163`; timeouts raised `teacher_prep_intent 60→180`, `blueprint/diagnostic 120→300` | "1-line fix directionally right but not converging" → **fix landed + 80B route + 4B fallback** |
| Materials upload | `PrepHome.vue` now does a **real** presign→upload via `presignUpload` (`@/api/teacherV3Upload`), with uploading/done/error states. No fabricated progress | previously "service-side consumption not built" |
| Trial feedback | new `trial_models.py` (`tp_trial_feedback`), `trial_router.py` (`/trial`), migration `trial_001_teacher.py`; FE `src/api/trial.js`, `src/pages/student/TrialFeedbackView.vue` | not previously recorded |

Migrations form a **single chain, single head**: `f19a2b3c4d5e → … → b27c3d4e5f60 → c8d9e0f1a2b3 → trial_001_teacher`. Verified: `alembic heads` → `trial_001_teacher (head)` (exactly one line). Complies with the repo's single-chain/single-head rule.

### v2-2. Real regression found and fixed this pass (highest-value finding)

**Symptom.** `pytest tests/unit/teacher_prep -q` returned **128 passed / 149 ERROR** (231s). 149 errors is not "pre-existing flakiness" — it was investigated.

**Root cause (proven, not guessed).** The newly added `app/teacher_prep/trial_models.py` used **raw PostgreSQL-dialect `JSONB`** for `ratings` and **`UUID` from `sqlalchemy.dialects.postgresql`**. `app/db/models/__init__.py:31` imports that module so it registers into `Base.metadata`; every SQLite-based test fixture calling `Base.metadata.create_all` then died with:

```
AttributeError: 'SQLiteTypeCompiler' object has no attribute 'visit_JSONB'
```

This was a **violation of an existing repo convention**: every other teacher_prep model module (`conversation/models.py:40`, `diagnostic/models.py:37`, `profile/models.py:38`, `profile/evidence.py:39`, `specs/models.py:39`, `models.py:40`) defines `JsonB = JSONB().with_variant(JSON(), "sqlite")`. Only the new file omitted it.

Collateral impact: this bug **broke the very regression lock that proves P0-1/P0-2** (`test_uow_fk_ordering.py` was 3 ERROR), i.e. the R1 `269 passed` claim in `GOLDEN-PATH-R1\FINAL-GATE.md` was **no longer reproducible** on the current tree.

**Fix applied (minimal diff, convention-conformant).** In `trial_models.py`: import `JSON` + `Uuid as SAUuid`; add `JsonB = JSONB().with_variant(JSON(), "sqlite")`; use `JsonB` for `ratings` and `SAUuid(as_uuid=True)` for `id`/`teacher_id`. Comment added explaining the cross-dialect requirement and citing this incident.

**Verification.**

```
pytest tests/unit/teacher_prep/test_uow_fk_ordering.py -q   →  3 passed in 0.68s   (was 3 ERROR)
pytest tests/unit/teacher_prep -q                           →  277 passed, 4 warnings in 86.34s  (was 128 passed / 149 ERROR)
```

The 4 remaining warnings are the pre-existing `coroutine 'Connection._cancel' was never awaited` async-resource warnings, unrelated to this change.

**This is a hard lesson for the next round:** adding an ORM model to this repo is not complete until `pytest tests/unit/{owning domain}` is green, because `Base.metadata` is global and one PG-only type poisons every SQLite fixture in the whole suite.

### v2-3. Stale frontend tests corrected (contract, not defect)

`npx vitest run test/teacher/` = **2 failed / 415 passed**. Both failures were the *same* outdated assertion, and both were **contradicting the frozen boundary of this very task**, not reporting a real product bug:

- `test/teacher/router.test.ts:49` and `test/teacher/legacyKernelRoutes.red.test.ts:12` asserted `/teacher-v3/slides` must lazy-load **`SlidesView.vue` directly** and must **not** contain `SlidesCompatRedirect.vue`.
- The frozen boundary (Objective, assumption 3, acceptance criterion 4) requires slides to be a **compat gate**: only `mode=vnext` ∧ session ∧ deck may load frozen `SlidesView`.

These tests were written against the pre-hybrid contract (committed in `3dc3f9c`). They were updated to assert the gate shape (`contains SlidesCompatRedirect` ∧ `not contains SlidesView`), with an inline comment recording why. **No product code was changed to satisfy them.**

Verification: the two files now report **7 passed** (`Test Files 2 passed (2) / Tests 7 passed (7)`), and the full `test/teacher/` suite is **417/417 green**.

> ⚠️ Follow-up for the owner: the *other* half of the assertion set in `test/teacher/v3SlidesRetired.test.ts` (21 tests) was already green and already encodes the gate correctly, so the two files above were the only stale ones. Worth a repo-wide grep for other assertions that pin direct `SlidesView` imports before the next release gate.

### v2-4. What this pass did NOT establish (do not over-read this section)

- **No real browser probe was executed this pass.** The earlier PM section's probe stands as the last browser evidence. A fresh end-to-end browser probe was **not** possible in this environment: the browser-automation skill refuses to run on Windows (`agent-browser 目前不支持 Windows 系统`). This must be redone on macOS/Linux, or by driving the installed Playwright directly — and it must be redone *after* E-1/E-3 landed, because the PM probe predates both and its blocker (`request_id: fb77563b93d94ebe`) may no longer reproduce.
- **No golden-path claim is made.** `277 passed` + `417/417` + `build ✓` is *contract/unit* evidence. It is not a real-service, real-DB, real-LLM end-to-end pass.
- **P0-4 (8100 revocation) remains genuinely unimplemented.** `grep -n "sid\|security_version\|revocation" app/core/security.py` returns a single hit, and it is a *comment* (`:50`) describing the token payload. There is still no `sid`/security-version revocation enforcement. This is the last hard P0 with no code behind it.
- **`pnpm typecheck` status was not re-adjudicated this pass**; the previous 5 errors in the separately untracked `test/chatCardContract.test.ts` are unresolved and that criterion remains not-green.
- **Two-teacher IDOR/revert tests were not written or run.** ACL helpers exist and are wired, but "wired" ≠ "proven": Work card C acceptance still requires positive + denied + revoked cases.

### v2-5. Decisions taken this turn (owner authority: user delegated "由你来决定")

1. **Handoff conflict → append, do not supersede into a new file.** The target file was already present, `ready-for-luna`, and dirty (` M`). Rather than overwriting (forbidden by `AGENTS.md` rule 3) or fragmenting into a second file, this v2 section was **appended** to the existing document and the header/status line updated. **No prior content was deleted**; the earlier sections are retained as provenance with an explicit precedence rule at v2-0. Rationale: a single source of truth with a visible audit trail beats two competing handoffs.
2. **E-3 availability relaxation → approved in principle AND found already implemented.** Re-reading the tree showed the full relaxation (send gate, nullable `class_id`, degraded evidence, migration) is already in place, so the decision collapses to **ratifying what exists** rather than authorizing new work. Ratification is still explicitly required from Luna before the T25 release gate can cite it, because it amends Assumption 1 and acceptance criterion 1.
3. **Browser probe → deferred to macOS/Linux, not faked.** Direct Playwright driving, or a user-driven manual probe, are both acceptable substitutes; real `agent-browser` on macOS/Linux is ideal. No probe result is invented here.
4. **`gateway.py:222` + `test_uow_fk_ordering.py` → not committed by this pass.** Both are verified effective, but this pass was scoped to read-only investigation plus one narrowly-bounded defect repair (v2-2). Construction commits belong to the owning work cards, with owner-approved baselines. See v2-6 for the recommended commit grouping.

### v2-6. Recommended commit grouping (for the next round; NOT executed)

The working tree mixes several independent concerns across 27 files. If they are to be committed, keep them atomic and in this order:

| # | Scope | Files (approx.) | Note |
|---|---|---|---|
| 1 | `fix(tp-prep): 跨方言类型修复——trial_models 裸 JSONB/UUID 导致 149 ERROR` | `app/teacher_prep/trial_models.py` | smallest, unblocks the whole suite; commit first |
| 2 | `feat(tp-prep): 体验测试留痕（tp_trial_feedback + /trial 路由 + 迁移）` | `trial_router.py`, `trial_models.py`, `alembic/versions/trial_001_teacher.py`, FE `trial.js`/`TrialFeedbackView.vue`, `api/routes.py`, `db/models/__init__.py` | depends on 1 |
| 3 | `feat(tp-prep): E-1 模型/教材文本 HTML 消毒（text_safety）` | `text_safety.py`, `blueprint/schema.py`, `conversation/{catalog,schema}.py`, `diagnostic/schema.py`, `context/builder.py`, `test_text_safety.py` | independent |
| 4 | `feat(tp-prep): E-3 班级可选——send gate/class_id 端到端可选 + 降级` | `conversation/orchestrator.py`, `models.py`, `migrations/c8d9e0f1a2b3`, `test_e3_optional_class.py`, FE `PrepHome.vue`, `copy.ts` | amends a frozen assumption → commit only after Luna ratifies |
| 5 | `fix(tp): 合规修复——enable_thinking 形状 + 80B 路由 + 4B 回落 + 超时抬升` | `app/llm/{config,gateway}.py` | independent; highest latency payoff |
| 6 | `test(tp-prep): UoW 父子行 flush 顺序回归锁` | `tests/unit/teacher_prep/test_uow_fk_ordering.py` | depends on 1 |
| 7 | `fix(frontend): slides 兼容闸门契约对齐（stale router 断言）` | FE `test/teacher/router.test.ts`, `legacyKernelRoutes.red.test.ts` | independent |

Do **not** bundle 1–7 into one commit; do **not** `git add .`.

### v2-7. Open P0 ledger after this pass (authoritative; replaces the Assumptions item 6 reading)

| ID | Finding | Status after v2 |
|---|---|---|
| P0-1 | `create_session` parent-row FK 500 | **Fixed** (`fac13bc`), regression lock now executes & passes |
| P0-2 | `propose_blueprint` parent-row FK 500 | **Fixed** (`fac13bc`), same lock |
| P0-3 | 8100-issued token rejected by 8000 → 503 / wasted 404 round-trip (also the `prep/sessions` p50 66.9 ms cause) | **Partially addressed**; catalog adapter rewritten, underlying cross-service credential mismatch not proven fixed end-to-end |
| P0-4 | 8100 token **revocation ineffective** | **STILL OPEN — no code.** Last hard P0. |
| P0-5 | private artifact/deck **cross-teacher read** | Helpers exist + wired; **unproven** (no two-teacher test) |
| P0-6 | deck **revert unauthorized write** | Same as P0-5 |
| P0-7 | governance: T24 EVIDENCE-INDEX missing, manual-PASS gap, baseline drift | **Still open**; this v2 section is itself a baseline-drift correction |
| E-1 | HTML in model-authored blueprint text | Fixed + wired; **needs real-model verification** |
| E-2 | class chip label sourced from 8100-local table, not 8000 truth | **Not addressed this pass** |
| E-3 | availability too strict | Implemented; **needs Luna ratification** |
| E-4 | bogus `mode=vnext` ids 404 in-place | **Not addressed** (minor) |

Teacher Domain **remains `NO-GO`** until P0-4 is implemented and P0-3/P0-5/P0-6 are proven by real tests and a real browser Golden Path.

### v2-8. Security follow-up

**Resolved this pass (assistant-side hygiene):** the leaked key prefix had propagated into the assistant's own planning artifacts. All occurrences were irreversibly replaced with `ak-****REDACTED****` / `sk-****REDACTED****` placeholders in:

- `.workbuddy-ai/memory/2026-09-11.md` (11 tokens across the file)
- `.workbuddy-ai/memory/2026-09-14.md` (1 token, self-inflicted this turn)
- `.workbuddy-ai/memory/ENV-NOTES.md` (4 tokens)
- this document (§v2-8 originally quoted the prefix; now redacted)

Verified: a recursive scan of `.workbuddy-ai/memory/` for the four known key prefixes (`ak-5c****`, `ak-d4****`, `sk-lu****`, `ak-58****`) returns **no matches**. Secrets must never be copied into planning artifacts; this was a violation and is now corrected.

**Still open (owner action required):**

1. `deliverables/ai-butler-phase4/evidence/full-regression.txt` (8,228,123 bytes, **untracked, and `git check-ignore` reports it is NOT ignored** — so it is one careless `git add .` away from being committed). Contains hundreds of key-shaped matches. Recommended: **rotate the key** (already presumed compromised), then delete or relocate the file and add an ignore rule.
2. **New cross-domain finding (reported, deliberately NOT fixed):** `services/research-vnext/tests/unit/test_redaction.py:28` is a *tracked* file that uses a **real key prefix as a test fixture** — `("ak-****REDACTED****…", "sk-" + "z" * 32)`. A redaction test must not embed the real secret it is meant to catch; it should use a synthetic value. This file belongs to the **research-vnext** domain, which is outside this Teacher-scoped task's ownership, so per the repo boundary rule it is reported here rather than edited. Owner of `services/research-vnext` should replace the fixture with a fake token.
3. The 8000/8100 `.env` plaintext keys referenced in the memory notes remain the root source; rotation is the only real remediation.

### v2-9. Answers to the user's product concerns (analysis; implementation only where noted)

**a) "每一个老师都可以使用，不需要这么严格的" (permissions too strict).**
Root cause identified: the send gate hard-required a confirmed 8000 class, and the account in the screenshots (`13800000000`) has an empty `/api/classes/mine`. The relaxation is now implemented (v2-1, E-3). What remains is a **product ratification**, plus E-2 (the chip label must show the 8000 truth, not a local table) so the relaxed UI does not become a *lying* UI. Security red lines are untouched: cross-teacher reads, revert authorization, and revocation stay fail-closed.

**b) "教师和 AI 的对话极其古板，完全不是智能体平台" (the dialogue feels rigid).**
Three stacked causes are visible across the five screenshots:
1. *Language layer* — `prep.focused.*` copy reads like an error page ("刷新查看新版本" / "重试"), not like an agent speaking. Screenshot ④ literally offers 「AI 暂时不可用，你可以手工填写」 in the middle of a conversation.
2. *Missing noise floor* — internal diagnostic codes (`no_units`, `rule_extraction_fallback`, `conflicting_evidence`) are rendered raw to the teacher; screenshot ⑤ shows `no_units` repeated **ten times** plus two competing 「取消」 buttons.
3. *State layer* — `tp_sessions.intent` was not being persisted per turn, so the same proposal was re-derived and re-shown (screenshot ③ shows two near-identical proposals). The orchestrator now writes the merged selection into `tp_sessions.intent` and adds an explicit `section_no_authorized_content` card for the empty-slice case (v2-1) — this is the structural half of the fix.

Direction: add a warning-code → teacher-language projection layer, give `FocusedState` a conversational recovery action instead of refresh/retry, and deduplicate proposals by intent hash. None of this is a visual redesign — it is copy + projection discipline.

**c) "整条链路的速度极慢" (latency), incl. the doubao model swap.**
The decisive model-layer fact is already measured: the gateway was sending the **wrong** thinking payload shape, which *inflated* the 4B model's reasoning (25.4 s → 108.4 s, 1897 → 8821 tokens). A single controlled blueprint run took **272,348 ms** with **72 % of output tokens spent on reasoning**; a compacted contract still needed 163.9 s with 82 % reasoning and then failed schema validation on 10 fields. This pass confirmed the corrected `enable_thinking` shape, an 80B primary route for `teacher_prep_*`, a 4B fallback, and timeouts raised to 180 s/300 s are all in the tree.

**Important caveat:** switching to a different model (doubao) addresses only *which* model runs. It does not by itself fix a wrong request shape, a timeout budget too small for reasoning-heavy prompts, or a prompt that invites unbounded reasoning. Before declaring victory, re-run the controlled blueprint measurement against the new model and record the reasoning-token share — if the new endpoint does not emit reasoning tokens the problem may vanish, but that has **not** been measured here. Independent of the model, the export path has its own serial bottleneck (8200→8201 per-page headless render plus Windows PowerPoint COM, measured 300 s+), which needs page-parallel rendering, page-artifact caching, and deterministic python-pptx assembly.

## Assumptions and decisions

1. **(AMENDED by the PM product-decision input above, pending Luna confirmation)** Originally: "every teacher can use it" meant entry reachability with a truthful authority error, while class-scoped sessions still required a confirmed 8000 class relationship. The user now asks that every authenticated teacher be able to actually run prep sessions without a confirmed class, with class-scoped enrichment degrading gracefully. Security boundaries (cross-teacher/cross-class private data, revocation) do not change. Luna must re-baseline Assumption 1 and acceptance criterion 1 before implementing.
2. The formal session URL is `/teacher-v3/prep/session/:sessionId`. `/teacher-v3/prep-immersive` remains a historical shadow/harness route and cannot be an essential navigation target or fallback.
3. The only allowed textbook input is authorized pre-sliced data. No new PDF, `TextbookPage`, `page_refs`, or `pNN` read/write is permitted. Missing active slices is a truthful unavailable-data condition, never a reason to use legacy sources.
4. A server-side membership/ownership verdict remains mandatory before class-scoped evidence or an existing private resource is returned. Availability improvements must distinguish 401/403 (actual denial) from 5xx/network authority failures; do not turn an unavailable authority into a false permanent permission denial.
5. Browsers communicate only with 8100 for the VNext artifact path; 8100 delegates PPT work to 8200. Browser-to-8201 calls are forbidden.
6. T25 P0s remain open: 8000 identity/membership authority, parent/child flush regression, private artifact/deck read and rollback authorization, 8100 revocation enforcement, and genuine Golden Path evidence. Neither old tests nor a mocked harness closes any of them.

## Scope

- Reconfirm and, only with owner approval, finish the front-end VNext-to-legacy-shell assembly without starting the legacy generation kernel.
- Repair and prove the minimum 8100/8000 identity, session-write, ownership, rollback, and revocation controls needed for a secure teacher flow.
- Rebuild the local runtime and run the only acceptable real Golden Demo using an actual teacher session and actual services.

## Out of scope

- Research and Student work.
- PPT or lesson-plan visual redesign; deletion or refactoring of frozen legacy editors.
- Reopening old generation, content, export, or wizard kernels.
- Browser direct access to 8201.
- Any business fact in `localStorage`, mock identity, fake class/profile data, or `setTimeout`-simulated work/progress.
- PDF or `TextbookPage` fallback when authorized slices are unavailable.

## Contracts and data changes

- Canonical route contract: first successful create returns a server session ID and navigates to `/teacher-v3/prep/session/:sessionId`; all session recovery and artifacts use that same session identity.
- PPT gate contract: `/teacher-v3/slides` can render frozen `SlidesView` only when query includes `mode=vnext`, `session`, and `deck`/equivalent artifact identity. All other old-slides requests redirect to the canonical session when known, otherwise prep home.
- Legacy identity contract: consumers accept `id`, `spec_id`, or `deck_id`; spec revision and lineage remain server-returned metadata. Projection is display-only and never becomes a legacy generation or persistence payload.
- Teacher availability/auth contract: 401 means sign in; a confirmed 403 means the requested class/resource is not available to that actor; authority/network 5xx is a recoverable service-unavailable state. A VNext UI must not label the latter as “no class preparation permission.”
- Security contract: each read, export, ticket issue/redeem, audit-list, edit proposal, apply/reject, and revision revert uses one reusable owner/ACL decision. Revoked 8000 credentials are rejected by 8100 before any protected response.

## Implementation steps

### Work card A — front-end assembly and availability messaging

- [x] **Precondition / ownership stop:** the user explicitly confirmed ownership for the dirty/untracked bridge files; unrelated dirty files remain preserved and out of scope.
- [x] Read the complete affected files and existing route, adapter, and bridge tests before editing: `D:\frontend\src\router\index.js`, `src/router/slidesCompat.ts`, `src/pages/teacher-v3/SlidesCompatRedirect.vue`, `src/pages/teacher-v3/prep/{PrepCenterView,PrepEditor,legacyArtifactAdapter,legacyKernelBridge}.ts`, and the focused prep-surface route.
- [x] Preserved the canonical session route and server-issued `id/spec_id/deck_id` identity mapping; no legacy-shell bypass was introduced.
- [x] Kept the slides gate exact; the focused retired-slides and bridge suites cover naked/partial/valid VNext query cases.
- [x] Adjusted user-facing prep error mapping so 8000 authority/network failures are temporary authority unavailability, while true 401/403 handling remains unchanged.
- [x] Verified front-end state uses API snapshots and server revisions, with no new `localStorage` business state or timer-simulated progress.
- [x] Stopped the card after focused tests, production build, and a 1440×900 real-browser route probe; the remaining backend/security work is separate.

### Work card B — 8100 session/identity P0 and class-scoped availability

- [ ] **Precondition / ownership stop (updated 2026-09-14 late PM):** `app/teacher_prep/**` is now git-tracked and carries uncommitted diffs **27 files / +617 / −113** across teacher_prep + llm + tests + 2 new migrations. Owner unconfirmed (presumed the prior Sol/agent session). Obtain an owner-approved baseline or isolate the change; never commit/overwrite those diffs in place. See §v2-6 for the recommended atomic commit grouping.
- [x] Under the confirmed ownership boundary, updated only the catalog class-directory adapter/router; `teacher_prep/service.py` and other untracked session sources were not overwritten. (Late PM: the service file itself now carries session-owned diffs; catalog work is complete.)
- [x] **(late PM)** Add/confirm focused regression tests proving `create_session`, conversation create, and a new `propose_blueprint` persist their parent rows before their command/revision rows. `tests/unit/teacher_prep/test_uow_fk_ordering.py` (3 tests) uses a private in-memory SQLite engine with `PRAGMA foreign_keys=ON` and asserts real FK enforcement. **This test was ERRORing (cross-dialect bug, §v2-2) and now passes: 3 passed in 0.68s.** Remaining gap: it proves FK ordering at the unit level, not through the real Postgres flow.
- [ ] Choose one coherent auth path: actual teacher entry uses an 8000-verifiable identity; remove the known useless `/api/classes/{id}` probe if the authoritative members endpoint is the contract; keep 8000 member lookup fail-closed for class profile/evidence and private resources. If 8100 login remains public, either mint a 8000-verifiable credential or mark and route it out of the teacher-prep path so it cannot issue a misleading successful login followed by 503. **(P0-3: only partially done — catalog adapter rewritten; credential mismatch unproven.)**
- [x] **(late PM)** Implement the availability policy server-side: authenticated teachers retain access to the conversation-first home; class-scoped creation/data returns a precise 403 only for a confirmed membership denial and a precise retryable 503 for authority loss. Class is now optional end-to-end (`_class_ref()`, nullable `class_id`, migration `c8d9e0f1a2b3`), with class-only capabilities raising an explicit `InvalidActionError` (`_require_class_id`) instead of inventing a class.
- [ ] Implement/complete 8100 revocation verification compatible with 8000 `sid`/security-version semantics before protected routing. Add positive, revoked, expired, malformed, and tenant-crossing tests. **P0-4: STILL NO CODE. `app/core/security.py` has no `sid`/`security_version` enforcement — only a payload-format comment at `:50`. This is the last hard P0.**
- [ ] Stop after focused teacher-prep/auth tests and an owner-reviewed diff; artifact/deck authorization is a separate card.

### Work card C — private artifact/deck authorization P0

- [ ] **Precondition / ownership stop:** storage, PPT V2, spec-router, and artifact-router sources are shared work. Secure an owner-approved baseline first. **(late PM: the helpers now exist and are wired — see the next item.)**
- [x] **(late PM, wired not proven)** Centralize an actor-to-spec/artifact ownership decision: `app/storage/artifacts.py` `acl_allows:160` / `spec_access:178` / `issue_ticket:228` / `redeem_ticket:317` / `revoke_artifact_access:412`, enforced at `ppt_v2/api/router.py:42,396,427,443,461,464` and `teacher_prep/specs/router.py:34`. An explicit deny when no ownership evidence exists is present in the helper contract.
- [ ] Apply that decision before **every** protected surface — audit the full list (deck/lesson-plan read, page read, revision list, audit list, ticket issue/redeem, patch, apply/reject, export, **revision revert**) rather than assuming the six wired call sites are exhaustive. Keep only opaque browser tickets; never reveal buckets, object keys, or presigned URLs.
- [ ] Add two-teacher integration tests for private read, page read, audit read, ticket redemption, patch/write, and revert. Assert denied calls are 403, make no revision/side-effect, and create the required denial audit record. Add revoked-credential variants. **Not written.** "Wired" is not "proven" — T25's P0-5/P0-6 findings stand until this runs.
- [ ] Confirm 8200 remains service-to-service behind 8100 and no browser endpoint reaches 8201. (Browser-probe evidence to date shows zero 8201 calls; re-confirm on the next probe.)
- [ ] Stop after security tests and a reviewable evidence bundle; do not claim Teacher Domain GO.

### Work card D — real Golden Demo re-acceptance

- [ ] Start a clean, owner-approved local environment with 8000 identity/membership, 8100 teacher prep, 8200 PPT Studio V2, workers, storage, authorized active textbook slices, and a real teacher/class relationship. Record readiness and versions without exposing secrets.
- [ ] In a real 1440×900 browser, authenticate as a teacher through the real 8000 flow. From `/teacher-v3/prep`, send the first message and verify the browser reaches `/teacher-v3/prep/session/:sessionId`.
- [ ] Capture server/network evidence for the one session: TeachingSession, canonical conversation, direct authorized chunk locators, diagnostic option/skip, class-profile Evidence cards (including unknown/confidence), blueprint proposal/confirmation, and persisted LessonPlanSpec plus TeachingDeckSpec with revision and lineage.
- [ ] Open the LessonPlanSpec and TeachingDeckSpec through their allowed legacy editor shells. Prove the PPT URL contains exactly the VNext session/deck gate, causes no old generation request, and calls 8100 rather than 8201.
- [ ] Make one authorized revision and one rollback; refresh/reopen and prove server-side revision/lineage consistency. Separately prove a second teacher and a revoked token receive 403/401 and cannot read or mutate it.
- [ ] Capture browser console/network summary, key 1440×900 screenshots, and actual 8100/8200 artifact evidence (PPTX/PDF, render/open check, hashes). Do not substitute mock, harness, curl-only, old screenshot, or pre-existing artifact evidence.
- [ ] Re-run focused unit/integration suites plus `pnpm typecheck`, focused front-end tests, production build, and `git diff --check`. Update the T25 release decision only if every P0 and required real Golden Path check is independently green.
- [ ] **Status 2026-09-14 PM (from the real-browser probe):** steps 1–2 of this card were exercised with real services and the existing confirmed teacher/class (13900009971 + 「E2E 班级」 `f80f3d90…`): first message → canonical session URL works, T23 slice/section proposals stream. The run **stops at blueprint render** (Work card E-1 HTML blocker), so steps 3–7 remain unproven in the browser. Do not substitute harness evidence.

### Work card E — new P1 blockers found by the 2026-09-14 real-browser probe

- [x] **E-1 blueprint payload hygiene (blocks the Golden Path): (IMPLEMENTED late PM, needs real-model re-verification)** `.  The root cause turned out to be broader than HTML injection: the old validity regex `<[A-Za-z/!][^>]{0,N}>` also **mis-flagged mathematical inequality chains** (`0<a<1 时 f(a)>0`) as HTML tags, and authorized textbook slices are full of such text — so context/blueprint cards were rejected at the `catalog.validate_component` persistence gate (42200) and the whole prep command failed. Fix: new `app/teacher_prep/text_safety.py` with a **real-tag whitelist** regex plus `strip_html_tags()` for untrusted text (block tags → newline, real tags stripped, math text preserved, entities deliberately not un-escaped). Wired into `blueprint/schema.py`, `conversation/catalog.py`, `conversation/schema.py`, `diagnostic/schema.py`, and `context/builder.py:133,144,223,232`. Tests: `test_text_safety.py` (green). **The frontend contract validator was NOT weakened** — correct. Remaining: re-run the real-model flow to confirm `confirm_proposal → project_specs` is reachable through the UI.
- [ ] **E-2 class display truth:** the workspace class chip label comes from 8100-local `classes.name` (`service.py:187-193`, `load_class_names` `:211`) and showed 「高二(3)班」 while the 8000 authoritative name is 「E2E 班级」. Derive the chip label from the 8000-derived catalog mapping server-side; add a test that the label equals the 8000 class name for a freshly created session. **Not addressed this pass — still open.**
- [x] **E-3 availability relaxation: (IMPLEMENTED late PM; pending Luna ratification of the amended Assumption 1)** session `class_id` is now optional end-to-end — backend `_class_ref()` / `_require_class_id()` guards, nullable `class_id` on context snapshots (migration `c8d9e0f1a2b3`), class removed from the `selection_complete` missing list; frontend send gate is `:disabled="!draft.trim() || busy"` with new copy `prep.home.class_optional` / `class_optional_hint` explaining that profile/diagnostic-publish need a bound class. 401/403 vs 503 semantics preserved; class-only capabilities now raise an explicit error instead of being silently skipped. Tests: `test_e3_optional_class.py` (green). **Luna must still ratify before the T25 gate can cite this.**
- [ ] **E-4 minor UX:** `mode=vnext` query with syntactically valid but nonexistent session/deck ids leaves the user on the slides route with a 404 from the unknown-deck fetch; add a canonical fallback (session → prep home) once E-1 unblocks real ids. **Not addressed; still minor.**

## Validation

- [x] Focused frontend bridge/availability suites remain green after work card A: 5 files, 39 tests. Re-run 2026-09-14 PM: **40/40 PASS** (red suite grew by one test).
- [x] **(late PM)** Full `test/teacher/` suite: **417/417 PASS** after correcting two stale router assertions (§v2-3). Two files were failing before the correction; **no product code was changed to make them pass.**
- [x] **(late PM)** `pytest tests/unit/teacher_prep -q` → **277 passed, 4 pre-existing async warnings** (was 128 passed / 149 ERROR before the cross-dialect fix, §v2-2).
- [x] **(late PM)** `pytest tests/unit/teacher_prep/test_uow_fk_ordering.py -q` → **3 passed** (was 3 ERROR). This is the P0-1/P0-2 regression lock; it now actually executes.
- [x] **(late PM)** `alembic heads` → `trial_001_teacher (head)` — exactly one head, single chain. Migrations `c8d9e0f1a2b3_e3_optional_class_id` and `trial_001_teacher` are correctly linked.
- [x] **(late PM)** `npx vite build` → **built in 37.36s**, no errors.
- [ ] Add and run isolated teacher-platform tests for parent-before-child persistence, 8000 membership statuses, 8100 revocation, and two-teacher artifact/deck ownership/revert. Use `uv run pytest` from `services/teacher-platform`; do not seed/migrate shared data during audit. **(FK-ordering part now covered; revocation and two-teacher parts still missing.)**
- [ ] `pnpm build` passed; `pnpm typecheck` was run but remains blocked by five errors in the separately untracked `test/chatCardContract.test.ts`, so this criterion is not accepted as green. **(Not re-adjudicated late PM.)**
- [x] Real browser route probe at 1440×900 reached 5176 with healthy 8000/8100/8200, confirmed the honest empty-class state, and verified bare Slides fallback; canonical session/deck gate remains unproven because the authority returned no class.
- [x] 2026-09-14 PM real-browser probe (see Sol closeout section): with a confirmed teacher/class the first message creates a real session at the canonical URL and T23 slice/section proposals stream; the chain then blocks at the blueprint HTML contract violation (Work card E-1). Zero browser→8201 calls; bare-slides gate redirects to prep; class-chip label violates display truth (E-2).
- [ ] Run a server-backed restart/reopen check plus authorized/unauthorized/revoked identity cases. A mock/harness success is supplementary only.
- [ ] Run `git diff --check` on each owned worktree and preserve unrelated dirty changes.

## Acceptance criteria

- [ ] An authenticated teacher can enter conversation-first prep and create a new server session without a false “no class prep permission” banner when the authority is temporarily unavailable.
- [ ] True cross-class and cross-teacher attempts remain rejected; no private artifact/deck/audit/revert operation leaks or mutates data for another teacher, including after credential revocation.
- [ ] The first message produces a persisted TeachingSession and canonical route; the complete flow produces server-owned blueprint, T23 evidence/profile cards, LessonPlanSpec, and TeachingDeckSpec.
- [ ] The legacy lesson-plan/PPT surfaces act only as VNext shells. Bare legacy slides cannot invoke the old generator; valid VNext slides identity uses `mode=vnext`, session, and deck.
- [ ] No new PDF/TextbookPage/page_refs/pNN access, browser-to-8201 call, localStorage business state, mock class/profile data, or timer-simulated work appears.
- [ ] A real teacher-browser Golden Demo, not merely API/harness/unit evidence, passes at 1440×900 with artifacts verified and server state durable across refresh/reopen.
- [x] **(late PM)** Model-authored text (blueprint objectives and sibling fields) is sanitized server-side via `app/teacher_prep/text_safety.py` before persist/projection, and the frontend contract validator was **not** weakened (Work card E-1). *Caveat: verified by unit tests, not yet by a real-model run.*
- [ ] The workspace class chip label equals the 8000-authoritative class name for freshly created sessions (Work card E-2). **Still open.**
- [x] **(late PM, pending Luna ratification)** Every authenticated teacher can create and run prep sessions without a confirmed class, with class-scoped evidence degraded to an explicit unavailable state — and with no relaxation of cross-teacher/revocation security (Work card E-3). *This amends Assumption 1 and criterion 1, so ratification is still required before the release gate may cite it.*
- [ ] Teacher Domain remains `NO-GO` unless all inherited T25 P0 findings are retested green against the final owned code and the human release gate accepts the evidence.

## Risks and rollback

- The shared dirty/untracked state is the highest immediate risk. If ownership cannot be established, stop without edits; create an isolated worktree or request the owner to stage/commit a baseline. Never overwrite a dirty bridge or backend source.
- Do not interpret the static `flush` now present in untracked `service.py` as a resolved P0. Regression-test it against real FK ordering before carrying it into an owned patch.
- Do not satisfy “every teacher can use it” by bypassing membership or ownership checks. The safe rollback is to keep class-scoped data fail-closed while retaining a truthful availability/retry state at entry.
- Auth changes can accidentally re-enable revoked tokens or create cross-service token confusion. Roll back only the changed auth adapter/validator after tests prove the old secure behavior; do not roll back migrations or shared data.
- Keep old editor files frozen. If the compatibility shell breaks, route back to the canonical session/home; never re-enable old generator endpoints as a fallback.
- If authorized slices are not active, stop the T23 demo and report data readiness; do not fall back to PDF or legacy pages.

## Execution notes

- Sol planning completed first; the handoff was moved to `in-progress` only after the user explicitly confirmed owner handoff for the previously dirty/untracked Teacher Prep and frontend bridge files. Unrelated dirty files remain out of scope.
- 2026-09-14 Work card A bounded changes: `prepApi.ts` now classifies 8000 authority/network failures (`50300`/`50301` or authority wording) as `prep.error.authority_unavailable`; `PrepHome.vue` keeps class-directory outages separate from the genuine empty-class state; the stable home copy now tells a teacher without an authoritative class to create or join one. True 401/403 mapping remains unchanged. Added red-green coverage in `test/teacher/teacherHybrid.red.test.ts`.
- 2026-09-14 Work card B bounded change: `/api/teacher-v3/classes` now reads the authenticated teacher's confirmed class directory from 8000 `/api/classes/mine` through `HttpxApiTransport` and returns explicit 401/403/503 semantics. `normalize_authorized_class_items` filters to teacher/owner/assistant, confirmed rows, and stable IDs. This fixes the stale 8100-local-ID mismatch without weakening membership checks. The externally owned `teacher_prep/service.py` was not overwritten; its parent/child flush risk remains unproven.
- Verification: focused frontend suites passed 39/39; `pnpm build` passed; catalog + teacher_prep backend tests passed 243/243 (with two existing async resource warnings). `pnpm typecheck` still fails on five errors in the separately untracked `test/chatCardContract.test.ts` (`never.items` and nullable `clarify`), not on the changed teacher-prep files. Ruff reports the repository's existing catalog-router rule violations; no formatter sweep was applied.
- Real browser/service evidence: 5176→8100→8000 is now live for the authenticated class-directory read; 8200 is healthy and 8201 remains unreachable from the browser. The current account's 8000 `/api/classes/mine` is empty, so the browser correctly cannot create a class-scoped VNext session yet. No class, membership, token, or artifact data was fabricated or created during this probe.
- T25 remains `NO-GO`: 8000 identity authority availability/credential compatibility, parent-before-child flush regression, private artifact/deck IDOR and rollback authorization, 8100 revocation enforcement, and a real end-to-end Golden Demo are still open P0s. This execution does not change the T25 release decision.
- 2026-09-14 PM closeout (read-only): re-verified bridge/P0 code with file:line evidence, re-ran focused suites (40/40), and ran a real-browser probe that (a) reproduced the user-reported availability/permission blocker for teachers without a confirmed 8000 class, (b) proved first-message → canonical session creation and T23 slice/section proposals with a confirmed teacher/class, (c) hit a new Golden-Path blocker (HTML markup in model-authored blueprint objectives rejected by the frontend contract validator, `request_id: fb77563b93d94ebe`), and (d) found the class-chip label sourced from 8100-local `classes.name` instead of the 8000 authoritative name. Recorded as Work card E; no product code was modified.
- **2026-09-14 late PM closeout v2 (this turn; one narrowly-bounded defect repair + full re-baseline):** see §"Sol closeout v2". Summary: (1) discovered the workspace had advanced far past the morning picture — E-1, E-3, P0-5/P0-6 wiring, the `enable_thinking` fix, the 80B route with 4B fallback, real material upload, and trial-feedback plumbing are all present but **uncommitted**; (2) found and fixed a genuine cross-dialect regression in the new `trial_models.py` (raw PG `JSONB`/`UUID` poisoning `Base.metadata` → **149 ERROR** across the suite, including the P0-1/P0-2 regression lock itself) by conforming to the repo's existing `with_variant(JSON(), "sqlite")` convention — suite went from `128 passed / 149 ERROR` to **`277 passed`**; (3) corrected two stale frontend router assertions that contradicted this task's own frozen slides-gate boundary — `test/teacher/` went from `2 failed / 415 passed` to **`417/417`**, with no product code changed; (4) verified `alembic heads` = single head and `vite build` ✓; (5) confirmed **P0-4 (8100 revocation) still has no code at all**, and E-2/E-4 remain unaddressed. No commit was made and no commit is recommended inside this pass — §v2-6 proposes the atomic grouping. A real browser probe was **not** run (browser automation is unavailable on Windows); it must be redone after E-1/E-3 landed. Teacher Domain remains **`NO-GO`**.
- Status remains `ready-for-luna`. Execution stops here and waits for the user to switch to 5.6 Luna and reply 「继续执行」.
