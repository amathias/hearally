# ADR 0002: Production is the only environment

- Status: Accepted
- Date: 2026-09-22

## Context

The build runs to a hackathon deadline with one developer. Judges assess what is deployed and readable. Parallel environments multiply Terraform state, DNS names, certificates, Cognito pools and cost, and they invite drift between what is described and what exists.

## Decision

Deploy exactly one environment, production, in `us-east-1`.

- Resources are named `hearally-prod-*`.
- There is no `-dev`, `-test` or `-staging` resource, and no environment selector variable.
- One Terraform state: `hearally/prod.tfstate` in `holds-terraform-state-files`.
- Local development uses mock providers and unit tests, not cloud resources.

A second environment requires a new recorded decision and separate state.

## Consequences

- What the judges see deployed is the only thing that exists, so the documentation cannot drift from reality.
- Changes are validated locally by `terraform plan` and unit tests before apply, because there is no staging to catch them. GitHub Actions are removed for now and can be reintroduced later if needed.
- External messaging defaults to mock mode in development so that testing cannot send a real text to a real ally.
- Cost stays close to the minimum for the whole build.
