# Feature Specification: Interactive Agents & Models setup

**Feature Branch**: `005-interactive-providers`

**Created**: 2026-09-11

**Status**: In progress

**Input**: Users must choose agent runtimes and (when needed) local inference
backends for project roles without editing JSON by hand. Prefer an interactive
`aichestra settings` flow. Reuse Orca account/agent signals when available.
Do not turn Aichestra into a second orchestrator or an LLM API client.

## Architecture constraints

- Orca remains the only Mode C control plane.
- Distinguish Agent Runtime vs Model Provider vs ExecutionTarget.
- Aichestra owns **policy/selection** (`runtime` / `provider` / `model`), not
  credentials, env-var names for secrets, Authorization headers, or authenticated
  provider HTTP login.
- Authenticated cloud providers (OpenRouter, OpenAI, Anthropic, Gemini accounts,
  Codex/Claude sessions, …) are discovered via **Orca**, never by reading API keys
  in Aichestra.
- Local inference discovery (Ollama / local OpenAI-compatible without auth) is
  allowed as presence/reachability only — not as inference execution.
- Machine-local provider/endpoint settings stay untracked (`machine.local.json`).
- Local HTTP accepts only localhost, 127.0.0.1, and ::1, without credentials,
  queries, fragments, environment proxies, or redirects. localhost is pinned to
  literal loopback. LAN and remote endpoints are rejected.
- Project policy cannot add or override connection endpoints or probe settings;
  it may still select roles and disable providers.
- OpenAI-compatible discovery uses one model-response snapshot per cycle.
- Never store API keys/tokens or `api_key_env` names in Aichestra config.
- A configured local provider without a reachable probe stays `available=false`.
- Adding a provider MUST NOT add Mode C workflow phases (MODE-C-019).
- Raw model providers are not workers (MODE-C-020); pair with a runtime
  (typically OpenCode) and a proven Orca launch path.
- Built-in agent runtime ids (`codex`, `cursor`, `claude`, `gemini`, `opencode`, …)
  MUST remain extensible via machine-local registration (custom id + binary).

## User stories

### US1 — Agents & Models menu (P1)

Developer runs `aichestra settings`, chooses **Agents & Models**, and can:

1. Discover from Orca (agents/accounts usable or not authenticated)
2. Add / enable an agent runtime (Codex, Cursor, Claude, Gemini, OpenCode, custom)
3. Add a **local** inference backend (Ollama or local OpenAI-compatible)
4. Show discovered / registered capabilities

**Acceptance**

1. Interactive menu item without leaving settings.
2. No prompt for API keys or `api_key_env`.
3. Ollama path writes `execution.model_providers.ollama` + can set `local.enabled`.
4. Local OpenAI-compatible path writes `execution.model_providers.<id>` with
   `api_style=openai` and endpoint only (no credential fields).
5. Reachable **local** endpoints may list models on discovery; unreachable stay
   configured-only. Authenticated remote availability is reported from Orca hints,
   not from Aichestra Bearer probes.
6. Optional OpenCode pairing adds an `execution.bindings` row and does not
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

1. Missing Orca degrades gracefully (no crash; manual local add still works).
2. Suggestions are runtime enablement / availability hints, not Mode C dispatch proof.
3. UX surfaces authenticated vs not (e.g. “authenticated in Orca (launch not verified)” /
   “not authenticated in Orca”) without asking for keys.

## Out of scope

- Storing provider API keys or `api_key_env` in Git, project.json, or machine-local
- Aichestra becoming an authenticated LLM HTTP client
- Claiming a new provider is Mode C-runnable without Orca launch proof
- New orchestration backends
- Full GUI installer


## Review gaps (2026-09-11)

- **OPEN — Orca model catalog:** Discover from Orca must ultimately supply
  selectable runtime/provider/model tuples for Coding, Research, Tests, and Docs.
  The installed CLI agent-context schema has account list and opaque worker-start
  --model support, but no model/provider catalog command. Do not invent model ids,
  read credentials, or claim account hints implement this requirement. Integration
  remains blocked on an Orca catalog contract and a reachable runtime.
- **PARTIAL — Orca availability without local PATH:** exact proven Orca-native
  launch evidence now satisfies target availability independently of local PATH.
  Account hints alone remain insufficient. Settings still needs the above Orca
  capability source to expose runtimes missing from local PATH.
