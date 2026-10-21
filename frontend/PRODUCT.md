# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary users are Chinese-speaking teachers preparing a lesson in a real teaching context. They need to clarify textbook scope and class context, review an evidence-backed TeachingBlueprint, confirm proposals, and continue into lesson-plan and deck artifacts when the authoritative service exposes them. Researchers are a separate vertical and must not be represented by Teacher fixtures.

## Product Purpose

智学树妍 provides a server-authoritative workspace for turning an educator's teaching intent into a reviewable lesson-preparation chain: TeachingSession → TextbookTeachingContext → TeachingBlueprint → evidence/profile → revision → LessonPlanSpec / TeachingDeckSpec. Success means the teacher can understand what is known, what is proposed, what needs confirmation, and what is genuinely available without fabricated business facts.

## Positioning

The product's mechanism is an evidence-native, conversation-first workspace whose typed proposals and artifacts remain tied to server revisions and authorized source locators. It is not a generic chat skin, a mandatory wizard, or a dashboard of empty modules.

## Operating Context

The teacher works in a browser at common Windows Chinese-language scaling and may refresh, reconnect, or return to a session. 8000 owns identity/session/role authority; 8100 owns Teacher Prep authority; the browser uses the unified origin/gateway. Research has its own future service boundary. The current checkout is materially dirty and legacy routes remain available for rollback.

## Capabilities and Constraints

- Server state is authoritative for sessions, revisions, proposals, confirmations, jobs, artifacts, permissions, evidence, and status.
- Stream state reports connection, cursor, gap, and reconnect conditions; it is not business progress.
- Client persistence may contain only UI preferences and unsent drafts.
- Rendered model content is typed and allowlisted; arbitrary model HTML/CSS/JS is not a product capability.
- Teacher locators use authorized direct chunks only (`source_id`, `section_path`, `chunk_id`, `chunk_ordinal`, `content_hash`); no PDF-page fiction.
- Missing Research capabilities render honest empty/blocked states; sanitized fixtures are test/harness evidence only.
- Existing legacy Teacher/Research pages are preserved until additive routes and rollback evidence pass.

## Brand Commitments

The existing Teacher identity is a bright Chinese education SaaS: cool off-white/white surfaces, restrained blue primary action, limited cyan support, soft depth, and generous controlled space. The approved Teacher B3 reference guides composition; the existing Teacher homepage reference preserves continuity. No generic purple-gradient AI theme, neon, glassmorphism, decorative glow, emoji production icons, or persistent module sidebar inside an active session.

## Evidence on Hand

- `D:\ppt-rescue-control\20260911-math-ai-platform-vnext\T22.3\screenshots\design-preview-b3-1440x900.png` — approved direction reference, not implementation proof.
- `D:\ppt-rescue-control\20260911-math-ai-platform-vnext\T22.3\screenshots\phase-d-conversation-1440x900.png` — anti-reference for the current gap.
- `C:\Users\<user>\Pictures\Screenshots\屏幕截图 2026-09-09 154501.png` — existing Teacher homepage brand reference.
- Real 8100 contracts and the current Vue route/component tree are the runtime authority. No production proof may rely on harness fixtures.

## Product Principles

1. Server facts before client inference.
2. Conversation first; structured artifacts only when typed and authorized.
3. Evidence and revision lineage remain inspectable on demand.
4. Capability gaps are honest states, never invented success.
5. Teacher and Research share platform grammar only after real domain evidence proves the abstraction.

## Accessibility & Inclusion

Chinese-first typography must remain comfortable at common Windows scaling. Keyboard focus, semantic landmarks, visible non-color status, reduced motion, long Chinese/English/math content, responsive degradation, and WCAG AA contrast are release requirements.
