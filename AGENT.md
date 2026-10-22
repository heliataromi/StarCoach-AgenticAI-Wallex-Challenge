# AGENT.md — StarCoach Wallex Algorithmic Trading Challenge

**Parent context:** Root
**Generated:** 2026-10-22 · **Updated:** 2026-10-22
**Project Repository:** `git@github.com:heliataromi/StarCoach-AgenticAI-Wallex-Challenge.git`

---

## 1. Project Purpose & Challenge Overview
This repository contains the architecture, trading engine, backtesting harness, decision logging, and deployment automation for the **Wallex Hackathon Challenge (StarCoach Agentic AI)**.
- **Goal:** Develop an autonomous, intelligent algorithmic trading engine powered by Agentic AI that receives market data from Wallex, identifies trading opportunities, executes automated orders via the Wallex API / Wallex MCP on a 5,000,000 Toman credit account, minimizes drawdown, and realizes all profits in Iranian Toman (TMN / IRT) before competition deadline.
- **Evaluation Criteria (300 Total Points):**
  - **Trading Performance (150 pts):** Net profit % (90 pts), Drawdown control (30 pts), Algorithm activity (15 pts), Execution stability (15 pts).
  - **Technical Quality & Repeatability (150 pts):** Strategy logic & decision explainability (40 pts), Real Agentic AI utilization (35 pts), Capital & risk management (30 pts), Reproducibility & clean code (25 pts), Backtesting & Shadow testing (20 pts).
- **Hard Red Lines:** No manual trades, no self-trading, no wash trading, trading only in authorized Wallex markets, and zero coin inventory remaining at deadline (100% liquidated to Toman).

---

## 2. Core Discipline: Ponytail (Lazy Senior Dev Mode)
This repository strictly enforces the **Ponytail** engineering discipline (`/ponytail`). Efficiency and simplicity beat complexity every time.

### The YAGNI Ladder (Ascend before writing code)
Before writing any code or introducing any abstraction:
1. **Does this need to be built at all?** (YAGNI).
2. **Does it already exist in this codebase?** Reuse existing helpers, utils, and patterns.
3. **Does the standard library already do this?** If yes, use stdlib.
4. **Does a native platform feature or existing dependency cover it?** Use it.
5. **Can this be one line or a small function?** Keep it minimal.
6. **Only then:** write the minimum code that works.

### Non-Negotiable Rules
- **No speculative abstractions:** No abstract factories, premature microservices, or complex wrappers that weren't explicitly requested.
- **No unnecessary dependencies:** Keep dependencies lean; audit every package added.
- **Deletion over addition:** Fewer files, shorter diffs, boring and robust over clever.
- **Target the root cause:** Fix shared logic once at the source rather than patching symptoms across callers.
- **Minimal runnable check:** Every non-trivial logic component must leave ONE simple, runnable verification check (an assert-based self-test or minimal unit test).
- **Deliberate shortcut marker:** If a deliberate simplification is made (e.g., in-memory cache, basic heuristic), label it with a comment:
  `# ponytail: <ceiling and upgrade path>`

---

## 3. Version Control: Git Workflow Standard
All repository operations must adhere to `/git-workflow` and the **Mandatory 3-Gate User Approval Protocol**.

### Dual Base Branch Architecture
- `main` / `master`: Release-ready production code. Direct commits and direct feature PRs are prohibited.
- `develop`: Primary integration branch. All feature and fix branches originate from and merge into `develop`.

### Branching Conventions
- Features: `feature/<task-name>` (branching off `develop`, merging into `develop`)
- Bugfixes: `fix/<task-name>`
- Chores / Refactoring: `chore/<task-name>` or `refactor/<task-name>`
- Releases: `release/v<semver>` (merging `develop` into `main`)

### Mandatory 3-Gate User Approvals
1. **Gate 1 (Pre-Commit):** Show `git status`, changed files, and proposed Conventional Commit message (`<type>(<scope>): <summary>`). Wait for explicit user confirmation before committing. Commits must be strictly atomic.
2. **Gate 2 (Pre-Push):** State branch name and remote destination (`origin <branch>`). Wait for explicit user confirmation before pushing.
3. **Gate 3 (Pull Request):** Prepare high-signal PR template targeting `develop` (or `main` for release), prefer GitHub MCP tool (`call_mcp_tool: github-mcp-server:create_pull_request`), and wait for explicit user confirmation.

---

## 4. Multi-Agent & Teamwork Protocol
When orchestrating tasks via `/teamwork-preview` and `/team`:
- **Orchestrator Role:** Decompose roadmap items into file-scoped, logically separated tasks.
- **Specialist Routing:**
  - `planner` / `analyst`: Requirements elicitation and PRD specifications.
  - `architect`: Technical design, system interfaces, risk guardrails.
  - `executor`: Implementation following the Ponytail ladder.
  - `verifier` / `test-engineer`: Backtest validation, shadow test verification, and edge-case execution.
- **Living Handoffs:** Maintain stage decisions and rationale in `.omc/handoffs/` or `docs/handoff.md`.

---

## 5. Technical Architecture & Component Structure
```
├── CHALLENGE.md         # Official competition challenge document
├── AGENT.md             # Project operating guidelines and rules (this file)
├── specs/               # Detailed feature specifications and architectural plans
├── docs/                # Architecture diagrams, decision matrices, handoff logs
├── decision_logs/       # Mandatory trade execution and signal logs for judges
├── src/                 # Engine source code (Data ingestion, Strategy, Risk, Execution)
└── tests/               # Backtesting harness, shadow testing engine, unit verifications
```

## Manual Notes
Notes under this heading are preserved across documentation updates.
