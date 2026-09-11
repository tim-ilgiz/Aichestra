# Aichestra

[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/tim-ilgiz/Aichestra)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Use the right AI for each part of software development — as one coordinated team.**

Aichestra is a portable AI development environment for coordinating multiple AI agents, models and developer tools inside any software project.

Instead of choosing one AI tool for everything, you can assign different agents or models to different roles:

* **Coding**
* **Research**
* **Tests**
* **Documentation**

Aichestra keeps this configuration inside your project and connects your project, AI tools and orchestration engine into one development workflow.

Today, Aichestra uses **Orca (`stablyai/orca`)** as its primary orchestration backend.

```text
You
 │
 ▼
Aichestra
 │
 ▼
Orca
 │
 ├── Codex
 ├── Cursor
 ├── Claude
 ├── Gemini
 ├── OpenCode
 └── local models
```

Aichestra is intentionally designed not to be tied to a single AI provider, model, IDE or orchestration engine.

**Orca is the first supported orchestration backend. Additional orchestration engines may be supported in the future.**

---

## Mission

AI-assisted development should not depend on one model doing everything.

Different AI systems are better suited to different tasks. One may be better at implementation, another at research, another at tests or documentation.

Aichestra makes it possible to combine them into one configurable development environment.

The goal is simple:

> Enter any project, run `aichestra init`, choose which AI should do what, and let Aichestra coordinate the rest.

Aichestra aims to make AI-assisted development:

* **Portable** — configuration belongs to the project, not to one machine or IDE.
* **Provider-independent** — use different AI providers, runtimes and models.
* **Role-based** — choose the best AI for coding, research, tests and documentation.
* **Orchestrator-independent** — use Orca today while keeping the architecture open to other orchestration engines.
* **Coordinated** — agents working on the same task can exchange context and results.
* **Verifiable** — real project tests and checks can decide whether work is complete.
* **Optional** — you can always continue using Codex, Cursor or other tools directly.

---

# How it works

Aichestra sits between your project and the tools performing AI work.

```text
                   ┌────────────────┐
                   │  Your project  │
                   └───────┬────────┘
                           │
                           ▼
                   ┌────────────────┐
                   │    Aichestra   │
                   │                │
                   │ configuration  │
                   │ role routing   │
                   │ policies       │
                   │ verification   │
                   └───────┬────────┘
                           │
                  orchestration backend
                           │
                           ▼
                      ┌─────────┐
                      │  Orca   │
                      └────┬────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
       Coding           Research        Tests / Docs
       Codex             Cursor         Claude / local
```

### Aichestra is responsible for

* project-level AI configuration;
* role → runtime/model assignments;
* provider and model selection;
* project context and instructions;
* quota fallback policy;
* verification rules;
* preparing a task for orchestration.

### Orca is responsible for

* Runs and Tasks;
* agent workers;
* execution state;
* isolated worktrees;
* agent lifecycle;
* actual workflow execution.

Aichestra does **not** hard-code the agent workflow itself.

The orchestration engine owns how a task is decomposed and executed.

Today that engine is **Orca**.

---

# Installation

Aichestra requires:

```text
Python 3.11+
```

Clone the repository and install the package:

```bash
git clone <Aichestra repository>
cd Aichestra

python3 -m pip install .
```

Verify the installation:

```bash
aichestra doctor
```

For Aichestra development:

```bash
python3 -m pip install -e ".[dev]"
pytest
```

---

# Quick start

## 1. Open your project

Aichestra is not tied to any particular repository.

Go to the project where you want to use it:

```bash
cd ~/projects/my-project
```

---

## 2. Initialize Aichestra

Run:

```bash
aichestra init
```

Aichestra creates a project-local configuration:

```text
my-project/
├── .aichestra/
│   └── project.json
├── src/
├── tests/
└── ...
```

This configuration belongs only to this project.

Another project can use a completely different set of agents and models.

For non-interactive initialization:

```bash
aichestra init --yes
```

---

## 3. Configure your AI team

Run:

```bash
aichestra settings
```

You can configure the AI used for different responsibilities:

```text
Coordinator     → Cursor
Coding          → Codex
Research        → Gemini
Tests           → OpenCode + Ollama
Documentation   → Claude
```

The exact combination is up to you.

Inspect the current configuration:

```bash
aichestra settings show
```

Settings can also be changed directly:

```bash
aichestra settings set roles.implement=codex
aichestra settings set roles.research=cursor
aichestra settings set roles.tests=cursor
aichestra settings set roles.docs=claude
```

---

## Models and providers

Roles can optionally specify a provider and model.

For example, tests could be executed using OpenCode with a local Ollama model:

```bash
aichestra settings set roles.tests.runtime=opencode
aichestra settings set roles.tests.provider=ollama
aichestra settings set roles.tests.model=qwen2.5-coder:14b
```

This makes it possible to mix cloud and local AI inside the same project.

For example:

```text
Coding          → Codex
Research        → Gemini
Tests           → local Qwen
Documentation   → Claude
```

---

# Verification

Aichestra can run real project commands before considering work complete.

For a Python project:

```bash
aichestra settings set verify='["pytest","-q"]'
aichestra settings set verification.enabled=true
```

For a project with several checks:

```bash
aichestra settings set verify='[["npm","test"],["npm","run","lint"]]'
aichestra settings set verification.enabled=true
```

These are actual subprocess commands.

Their exit codes determine whether verification succeeds.

---

# Run a task

Once the project is configured:

```bash
aichestra orchestrate \
  --prompt "Implement user authentication, add tests and update the documentation" \
  --project-root .
```

Aichestra prepares the project context and creates one coordinated Orca Run.

The configured AI workers can then be used for the appropriate parts of the task.

Conceptually:

```text
                         User task
                             │
                             ▼
                         Aichestra
                             │
                             ▼
                           Orca
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
          Research      Implementation     Tests
              │              │              │
           Cursor           Codex       Local model
              │              │              │
              └──────────────┼──────────────┘
                             │
                             ▼
                       Documentation
                             │
                           Claude
                             │
                             ▼
                       Verification
```

The exact task graph is controlled by Orca.

Aichestra supplies the project configuration, role bindings, policies and verification around that workflow.

---

# Orca integration

**Orca is currently Aichestra's primary orchestration backend.**

Orca provides the execution environment Aichestra needs for coordinated multi-agent work, including:

* agent workers;
* isolated Git worktrees;
* task execution;
* Run state;
* worker lifecycle;
* agent communication.

Aichestra builds on top of those execution primitives.

The responsibilities are intentionally separated:

```text
Aichestra
    │
    │  What AI should do what?
    │  Which policies apply?
    │  Which model/provider should be used?
    │  How should the result be verified?
    │
    ▼
Orchestration backend
    │
    │  Run the tasks
    │  Manage workers
    │  Maintain execution state
    │  Isolate worktrees
    │
    ▼
AI agents
```

Today:

```text
Aichestra
    │
    ▼
Orca
```

The architecture is intended to allow other orchestration backends in the future:

```text
                     ┌──► Orca
                     │
Aichestra ────────────┼──► Future orchestrator
                     │
                     └──► ...
```

A project should not need to redesign its AI roles simply because the underlying orchestration backend changes.

---

# You can still use AI tools directly

Aichestra does not replace Codex, Cursor, Claude or other AI tools.

It does not intercept their normal usage.

You can continue using them exactly as before.

For example:

```bash
codex
```

or open Cursor normally.

Aichestra orchestration is **opt-in**.

---

# Ways to work

There are currently three main ways to use the tools.

### Direct

Use an AI tool directly:

```text
You
 │
 ▼
Codex / Cursor / Claude / ...
```

Aichestra and Orca are not involved.

---

### Orca directly

Use Orca's own environment and agents:

```text
You
 │
 ▼
Orca
 │
 ▼
AI agent
```

Aichestra is not required.

---

### Aichestra orchestration

Use project-specific roles, policy and verification:

```text
You
 │
 ▼
Aichestra
 │
 ▼
Orca
 │
 ├── Coding agent
 ├── Research agent
 ├── Test agent
 └── Documentation agent
```

You can switch between these approaches whenever you want.

---

# Supported AI runtimes

Aichestra can work with runtimes such as:

* Codex
* Cursor
* Claude
* Gemini
* OpenCode

OpenCode can also be connected to providers such as Ollama for local inference.

The important part is that **roles are not tied to vendors**.

For example:

```text
Project A

Coding          → Codex
Research        → Cursor
Tests           → Ollama
Documentation   → Claude
```

while another project could use:

```text
Project B

Coding          → Cursor
Research        → Gemini
Tests           → Codex
Documentation   → Gemini
```

Each project owns its own configuration.

---

# Quota fallback

A coding runtime can have a fallback.

For example:

```bash
aichestra settings set roles.implement=codex

aichestra settings set quota.mode=auto
aichestra settings set quota.roles.implement=cursor
```

Conceptually:

```text
Codex
  │
  │ quota exhausted
  ▼
Cursor
```

Automatic fallback applies only when Aichestra receives supported quota-failure evidence from the running worker.

To require manual intervention instead:

```bash
aichestra settings set quota.mode=manual
```

---

# Useful commands

Check the environment:

```bash
aichestra doctor
```

Initialize the current project:

```bash
aichestra init
```

Configure agents and models:

```bash
aichestra settings
```

Show project settings:

```bash
aichestra settings show
```

Change one setting:

```bash
aichestra settings set roles.implement=codex
```

Run an orchestrated task:

```bash
aichestra orchestrate \
  --prompt "Implement the feature" \
  --project-root .
```

Inspect the detected machine environment:

```bash
aichestra profile
```

---

# Machine-local configuration

Project configuration lives in:

```text
.aichestra/project.json
```

Machine-specific configuration is kept separately.

Local AI is disabled by default:

```text
local.enabled = false
```

This means Aichestra does not assume that every machine has Ollama or local models installed.

Run:

```bash
aichestra doctor
```

to see which supported tools are available on the current machine.

---

# External tools

Aichestra coordinates existing AI development tools rather than replacing them.

Depending on your project configuration, you may use:

* Orca
* Codex
* Cursor
* Claude Code
* Gemini CLI
* OpenCode
* Ollama

**Orca is currently required only for Aichestra's orchestrated mode.**

Direct usage of AI tools remains independent from Aichestra.

---

# For contributors

This README intentionally focuses on the **user experience**.

Detailed architecture, execution contracts, design decisions and implementation details belong in the project documentation rather than in the getting-started guide.

See:

```text
specs/
├── 001-portable-ai-orchestration/
├── 002-project-init-role-routing/
└── ...

AGENTS.md
.specify/memory/constitution.md
```

Run the test suite with:

```bash
python3 -m pip install -e ".[dev]"
pytest
```

---

# Project status

Aichestra is under active development.

The current implementation uses Orca as its orchestration backend and focuses on building a portable, role-based AI development environment that can be reused across different software projects.

Expect APIs, commands and supported integrations to evolve while the project matures.

---

# License

MIT License — see [LICENSE](LICENSE) for details.
