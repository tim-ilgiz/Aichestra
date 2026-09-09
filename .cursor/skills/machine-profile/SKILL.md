---
name: machine-profile
description: >
  Before configuring local AI (Ollama/OpenCode models, local.enabled, preferred
  models), inspect this machine: OS/CPU/RAM/GPU, local runtimes, installed
  models, capabilities, and propose a safe configuration. Use when the user
  asks to set up local inference, choose a model, enable local-worker, or
  when machine-specific defaults must not assume a particular Mac.
---

# Machine Profile — explore before configuring local AI

Do **not** assume the developer's hardware (for example a particular Mac with
24 GB). Always inspect first, then propose configuration.

## Required workflow

1. Run the deterministic profiler (not an LLM guess):

   ```bash
   python -m aichestra profile
   # or: python scripts/machine_profile.py
   ```

2. Read doctor local-AI section:

   ```bash
   python -m aichestra doctor --json
   ```

3. From the JSON profile, record:
   - OS / architecture
   - CPU summary
   - RAM total / available
   - GPU / accelerator presence
   - Suggested hardware profile id (`suggest_profile`)

4. Discover local runtimes and installed models (Ollama initially):
   - Prefer reusing an already-installed capable model
   - Never auto-download large weights; require explicit approval
   - Vision tasks must not go to text-only models just because they are local

5. Propose machine-local config only (untracked `.local/machine.local.json`):
   - `local.enabled` true/false (false remains valid on any hardware)
   - `preferred_models` / `allowed_models` when enabling local
   - Keep `providers.preferred_lead` / `fallback_lead` and per-provider
     `enabled` flags explicit when the user wants a subset
     (Orca+Codex, Orca+Cursor, Orca without local, …)

6. Confirm with the user before writing machine-local settings or approving
   downloads.

## Boundaries

- Tracked `policies/defaults.json` stays portable — no home paths, no
  machine-specific model ids as global requirements.
- Apple M4 Pro 24 GB is a **validation fixture / example**, not a product
  requirement (FR-018).
- Mode C still requires Orca + `--project-root`; local-worker is optional.
