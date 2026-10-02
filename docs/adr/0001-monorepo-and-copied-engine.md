# ADR 0001: Monorepo, and a copied engine rather than a shared package

- Status: Accepted
- Date: 2026-09-22

## Context

Hearally reuses the design of an earlier prototype by the same author, built for a different wearable: the same grades, the same priority budget and dismissal learning, the same consent tiers, blocklist and liability rules. The two are separate projects with separate repositories.

The earlier prototype is a working monorepo with a Python Lambda backend, a Node collector, a static frontend and domain-oriented Terraform modules.

## Decision

Keep the same monorepo shape: one repository with independently deployable components: `collector`, `backend`, `web`, `infra` (and later `transcriber`).

**Copy the engine into this repository and let the two diverge.** Do not extract a shared package consumed by both repositories.

## Consequences

- A feature can update its contract, backend, infrastructure, UI, tests and documentation atomically. Review has one entry point.
- No release coordination between two repositories on different schedules.
- The two codebases will drift. That is accepted; the engine is shared in design, not in artifact.
- Fixes to genuinely shared logic must be applied twice, deliberately.
- The README describes what is new here relative to the earlier prototype, so reused and new work are clearly separated.
