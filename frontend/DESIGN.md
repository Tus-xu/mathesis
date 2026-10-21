# Design system

<!-- impeccable:design-schema 1 -->

## Direction

Operate. The active session is a focused work surface: conversation is the default canvas, typed proposals/artifacts are deliberate secondary surfaces, and evidence/status open on demand.

## Visual world

The incumbent Teacher world is retained and tightened: cool off-white page ground, white working surfaces, one primary education blue, limited cyan support, slate text, soft semantic elevation, and Chinese-first system typography. The emotional temperature is calm, capable, and legible under ordinary classroom-office light. Brand continuity beats novelty.

## Layout grammar

- Active sessions remove the global module sidebar and use a compact focus header with back navigation, real identity, context, progress summary, artifacts, and overflow actions.
- The default conversation is a centered responsive reading column with a semantic maximum in the approximate 920–1120px range.
- The composer is sticky and aligned to the conversation column; it never spans the viewport or hides the last message.
- Teacher messages are right-aligned and compact; assistant prose is left-aligned and open; typed proposals/artifacts/evidence receive structured treatment.
- Avoid card-everything composition, nested card grids, persistent empty columns, Stepper/Wizard patterns, and dashboard sprawl.

## Semantic token contract

Components consume `var(--ma-*)` semantic variables from `src/platform/design-system/tokens.css`. Foundation literals remain in the token source only. New UI must not add raw hex/rgb/rgba colors, raw shadows, or ad-hoc gradients. Shared token changes require a targeted platform governance test and must remain domain-agnostic.

## Type, motion, accessibility

Body copy uses established semantic body sizing and approximately 1.6–1.75 line height. Headings form three clear levels; metadata stays readable. Motion is short and functional (surface transition, drawer entrance, stream cursor, artifact reveal) and respects `prefers-reduced-motion`. Errors state one cause and one recovery action. Status never relies on color alone.

## Surface boundaries

Teacher domain renderers stay under `src/features/teacher-prep-v2/**`; Research VNext will be additive under `src/features/research-vnext/**`. Platform primitives accept generic slots/view models and never import Teacher or Research types. T23/T24 artifact editing is not part of this checkpoint.

## Evidence rules

## Conversation grammar

Every turn is an authored unit: a visible role header names `教师` or `智学助教`, uses a project icon, and may show the compact server `created_at` time. Teacher turns are compact, right-aligned tinted surfaces; assistant turns remain left-aligned open prose. A typed proposal or artifact attaches below the assistant prose with a domain label, section headings, evidence affordance, and action footer. The layout separates turns through spacing and grouping rather than a permanent divider or a card around every sentence.

Pattern references (interaction patterns only, not branding): [NotebookLM](https://notebooklm.google.com/), [Perplexity](https://www.perplexity.ai/), and [Claude](https://claude.ai/). Their transferable signal is the separation of authored conversation, source/evidence context, and structured work surfaces.

The actual Vue route/component tree is the visual harness. Sanitized server-shaped fixtures may be injected only under an explicit E2E/test harness flag and must be labeled as harness evidence. Real-route screenshots, console/network output, and service availability must be reported separately from contract/harness evidence.
