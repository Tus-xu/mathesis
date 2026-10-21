# Teacher session surface brief

Mode: Operate. Primary target: `src/features/teacher-prep-v2/surface/ImmersivePrepSurface.vue` and the actual `/teacher-v3/prep-v2` harness route.

## Audience and task

The teacher is actively preparing one lesson. They need to say what they want, see what the server knows, review a six-turn conversation and typed Blueprint proposal, confirm or reject a pending action, open a real artifact when available, and recover from error/reconnect states.

## Direction contract

### THESIS

Make the TeachingSession feel like a calm working conversation with evidence attached, not a module dashboard or mandatory wizard. The default action is reading and responding in the conversation column.

### OWN-WORLD

Use the existing cool off-white/white education surface, slate Chinese-first type, one primary blue action, restrained cyan support, semantic rings/elevations, and project icon system. Teacher messages stay compact on the right; assistant reasoning stays open on the left; typed Blueprint/proposal/artifact surfaces earn structure only when the payload is authorized.

### STORY

The teacher understands the current session identity, what is confirmed, what is proposed, and what needs their decision. They can trace evidence, confirm a server action with its revision, open an artifact when genuinely ready, and recover with one clear action when the service is unavailable.

### FIRST VIEWPORT

At 1440×900, a compact focus header anchors the top; a centered reading column occupies the visual center with the six-turn conversation or honest empty state; a sticky composer aligns to that same column. Evidence/status remain quiet, on-demand surfaces rather than permanent empty columns. At 1280 and 1920 the column breathes fluidly without changing the hierarchy.

### FORM

Incumbent actual Vue route/component tree, extended only after runtime evidence. Seed key: `teacher-session-operate-2026-09-12-incumbent-b3`. This checkpoint documents and captures the harness; it does not wire T23 cards or replace dirty production surfaces.

### FINISH

unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Conversation grammar

Each timeline turn keeps visible authorship: a role header with project icon, localized `教师`/`智学助教` label, and compact server `created_at` time. Teacher prompts use a compact right-aligned tinted surface. Assistant replies stay left-aligned as open prose, followed only when authorized by a typed proposal/artifact surface with a domain label, evidence affordance, and action footer. This grouping makes the teaching decision legible without turning every sentence into a chat bubble or card.

Pattern references (not branding targets): [NotebookLM](https://notebooklm.google.com/) for source/chat/studio separation, [Perplexity](https://www.perplexity.ai/) for answer-plus-evidence hierarchy, and [Claude](https://claude.ai/) for chat/canvas separation.

## Required evidence matrix

Harness-only sanitized states: empty, six-turn Blueprint proposal, pending confirmation, artifact ready, error, reconnecting. Capture at 1280×800, 1440×900, and 1920×1080. Keep harness, contract, and live-route evidence explicitly separated.

## Open decisions

The current runtime composition remains a baseline/anti-reference in places identified by the handoff. Visual acceptance is reserved for the human; Sol reviews contract, product, visual, runtime, and accessibility evidence at each checkpoint.
