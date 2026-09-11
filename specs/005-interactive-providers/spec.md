# Feature Specification: Interactive Agents & Models setup

**Feature Branch**: `005-interactive-providers`

**Created**: 2026-09-11

**Status**: Settings policy and internal catalog boundary implemented.
Orca catalog transport remains OPEN.

**Input**: Users must choose agent runtimes and (when a catalog exists) models
for project roles without editing JSON by hand. Prefer an interactive
`aichestra settings` flow. Reuse Orca account/agent signals and, when
documented, Orca's runtime/model catalog. Do not turn Aichestra into a second
orchestrator or an LLM API client.

## Architecture constraints

- Orca remains the only Mode C control plane.
- Distinguish Agent Runtime vs Model Provider vs ExecutionTarget.
- Aichestra owns **policy/selection** (`runtime` / `provider` / `model`), not
  credentials, env-var names for secrets, Authorization headers, or authenticated
  provider HTTP login.
- All model/provider **addition** belongs to Orca, including local inference.
  Settings MUST NOT prompt for endpoints or credentials. Legacy registration
  helpers MUST fail closed without writing configuration.
- Authenticated cloud providers (OpenRouter, OpenAI, Anthropic, Gemini accounts,
  Codex/Claude sessions, …) are discovered via **Orca**, never by reading API keys
  in Aichestra.
- Machine-local provider/endpoint settings stay untracked (`machine.local.json`).
- Project policy cannot add or override connection endpoints or probe settings;
  it may still select roles and disable providers.
- Never store API keys/tokens or `api_key_env` names in Aichestra config.
- Adding a provider MUST NOT add Mode C workflow phases (MODE-C-019).
- Raw model providers are not workers (MODE-C-020); pair with a runtime
  (typically OpenCode) and a proven Orca launch path.
- Built-in agent runtime ids (`codex`, `cursor`, `claude`, `gemini`, `opencode`, …)
  MUST remain extensible via machine-local registration (custom id + binary).
- Selection requires `runtime`. `model` is an optional opaque runtime-owned id.
  `provider` is optional metadata only when explicitly useful, never inferred from
  the model string. A model such as `openrouter/example` remains unchanged.
- Catalog availability is not launch proof: every dispatch still checks
  `launch.effective.agent` and, when selected, `launch.effective.model`.
- Account hints, local HTTP probes, and config MUST NOT populate the Orca
  catalog. Do not invent a catalog CLI command or parse a hypothetical Orca
  JSON schema. Production discovery leaves the catalog absent until a
  documented transport exists.
- Local diagnostic probes may remain, but their HTTP-discovered models MUST NOT
  become interactive role model choices.

## User stories

### US1 — Agents & Models menu (P1)

Developer runs `aichestra settings`, chooses **Agents & Models**, and can:

1. Discover from Orca (agents/accounts usable or not authenticated)
2. Add / enable an agent runtime (Codex, Cursor, Claude, Gemini, OpenCode, custom)
3. Be directed to Orca to add/manage models and providers
4. Select role runtime (and model when a real Orca catalog is present)

**Acceptance**

1. Interactive menu item without leaving settings.
2. No prompt for API keys, `api_key_env`, or model-provider endpoints.
3. Settings directs model/provider management to Orca without writing provider
   registration from Aichestra.
4. Legacy model registration helpers fail without modifying configuration.
5. Local probe models cannot populate interactive role model selection.
6. Until a documented Orca catalog exists, interactive role selection offers
   runtime defaults only. Non-interactive role policy may still reference a
   model already configured in Orca (reference, never registration or launch proof).
7. Optional OpenCode pairing adds an `execution.bindings` row and does not
   invent a Python worker scheduler.

### US2 — Interactive add agent runtime (P1)

Developer registers a known or custom Agent Runtime binary id in machine-local
config so role bindings can select it. The built-in list is data and MUST be
extensible (new runtime id + PATH binary) without code forks for every product.

**Acceptance**

1. Built-in ids (`codex`, `cursor`, `claude`, `gemini`, `opencode`, …) and custom
   id+binary work.
2. Unknown runtime ids remain fail-closed until registered.
3. Registration lives in machine-local; secrets are never written.

### US3 — Orca capability discovery (P1)

When Orca is available, settings lists agents/accounts Orca already knows and
suggests enabling matching runtimes — without copying credentials.

**Acceptance**

1. Missing Orca degrades gracefully (no crash; manual runtime add still works).
2. Suggestions are runtime enablement / availability hints, not Mode C dispatch proof.
3. UX surfaces authenticated vs not (e.g. “authenticated in Orca (launch not verified)” /
   “not authenticated in Orca”) without asking for keys.
4. Account hints remain explicitly distinct from catalog inventory and launch evidence.

### US4 — Orca-owned model catalog (P1)

When Orca documents a runtime/model/capabilities catalog, Aichestra consumes it
through an internal adapter boundary and offers those models for role selection.

**Acceptance**

1. Internal `OrcaCapabilityCatalog` on `DiscoveryFacts` holds runtimes and models
   with availability, optional provider, capabilities, and effort choices.
2. Production discovery leaves this catalog absent until a documented Orca
   transport exists. Tests cover the boundary separately from live integration.
3. Catalog entries are not launch proof; selected agent/model must match the
   Orca `launch.effective` receipt.
4. Consume a documented Orca runtime/model/capabilities catalog (OPEN — blocked
   on the external Orca contract).
5. Validate runtime/model selections and launch proof through real Orca
   integration (OPEN — blocked on the external Orca contract).

## Out of scope

- Storing provider API keys or `api_key_env` in Git, project.json, or machine-local
- Aichestra becoming an authenticated LLM HTTP client
- Aichestra registering local or cloud model backends (endpoints, probes-as-catalog)
- Claiming a new provider is Mode C-runnable without Orca launch proof
- Inventing Orca catalog CLI commands or wire formats
- New orchestration backends
- Full GUI installer

## Review gaps (2026-09-11)

- **OPEN — Orca model catalog transport:** The installed CLI agent-context schema
  has an account list and opaque worker-start `--model` support, but no
  model/provider catalog command. Notes limit opaque native model selection to
  Claude, Codex and Cursor; OpenCode model dispatch must not be claimed from
  these flags alone. Do not invent model ids, read credentials, or treat account
  hints as this requirement. Integration remains blocked on an Orca catalog
  contract and a reachable runtime.
- **PARTIAL — Orca availability without local PATH:** exact proven Orca-native
  launch evidence now satisfies target availability independently of local PATH.
  Account hints alone remain insufficient. Settings still needs the above Orca
  capability source to expose runtimes missing from local PATH.
