# 006 — Orca-owned model catalog and selection

Status: registration boundary implemented; catalog integration OPEN.

## Brief and research

All model/provider addition belongs to Orca, including local inference. Aichestra
owns role policy only and never requests endpoints or credentials for registration.
Feature 005 allowed local registration; this requirement supersedes that behavior.
The existing integration exposes account hints, not runtime/model inventory.
Do not invent a CLI catalog command or treat local probes as a substitute.
The installed `orca agent-context --json` inspected on 2026-09-11 exposes
`worker-start --agent / --model / --effort`, but no catalog command. Its notes
limit opaque native model selection to Claude, Codex and Cursor; OpenCode model
dispatch must not be claimed from these flags alone.

Selection requires `runtime`; `model` is an optional opaque runtime-owned id.
`provider` is optional metadata only when explicitly useful, never inferred from
the model string. A model such as `openrouter/example` remains unchanged.
The internal catalog contains runtime availability and models with optional
provider, capabilities and effort choices. Catalog availability is not launch
proof: every dispatch still checks `launch.effective.agent` and, when selected,
`launch.effective.model` against the requested values.

## Plan

Remove local backend addition from settings and reject legacy registration helpers
before writes. Offer runtime defaults only until a documented Orca catalog exists.
Preserve native tools, existing role policy and diagnostic probes. Run the
maintenance gate, update canonical documentation and behavioral regression tests.
Add a typed internal catalog boundary to DiscoveryFacts and consume its available
models in settings. Until a documented transport exists, production discovery
leaves this catalog absent. Do not parse a hypothetical Orca JSON schema or call
underlying provider CLIs. Test this boundary separately from live integration.

## Acceptance and tasks

- [x] Settings directs model/provider management to Orca without endpoint prompts.
- [x] Legacy model registration helpers fail without modifying configuration.
- [x] Local probe models cannot populate interactive role model selection.
- [x] Account hints remain explicitly distinct from catalog and launch evidence.
- [x] Add internal runtime/model catalog and settings selection boundary.
- [ ] Consume a documented Orca runtime/model/capabilities catalog.
- [ ] Validate runtime/model selections and launch proof through real Orca integration.

The last two tasks are blocked on the external Orca catalog contract. Existing
non-interactive role policy remains a reference, never registration or launch proof.
