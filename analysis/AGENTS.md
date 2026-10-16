# analysis/ — Strategy Review Artifacts

## Purpose

Durable records of strategy review cycles: multi-model panel findings, backtest evidence, and the Lead's decision document. These are ANALYSIS artifacts — they explain *why* the trading logic is what it is. Binding implementation contracts live in the repo's code-owning AGENTS.md files (`backend/AGENTS.md`, `tests/AGENTS.md`).

## Ownership

Owner: FUBOT project team (Lead). Panelists write only their own assigned file inside `panel/`.

## Local Contracts

- `panel/<analyst>-<topic>.md` — one file per panelist per round (independent analysis: leaks with `file:line` + numbers, proposals, risks, what to cut).
- `panel/DECISION.md` — Lead-owned. The single source of truth for what was adopted, rejected, or deferred, with the evidence and the resulting commit/deploy for each item. Updated at the end of every review cycle.
- Round 2+ critiques go in `panel/<analyst>-<topic>-r2.md` — never edit another panelist's file.
- Production is live money: every artifact must respect read-only production access (no orders, no settings changes, no service restarts, never write the production DB, never print secrets).
- Every claim carries either a `file:line` citation or a measured number with its sample size; anything else is marked as a hypothesis.

## Work Guidance

- Evidence standard: no lookahead in replays, intrabar ordering stated (stop-before-target), fees stated, and cells with fewer than 20 trades marked inconclusive.
- Reusable experiments belong in `/tmp` on the VPS (or as a throwaway script) — only harness changes that should survive go into `backend/backtest.py` with tests.
- Strategy changes are never applied from an artifact alone: they need the user's explicit approval, the test suite, and a recorded deploy in `DECISION.md`.

## Verification

Artifacts are checked by the Lead for citation/sample-size compliance. The decisions they produce are verified by `PYTHONPATH=. pytest tests/backend/ -q` and by the monitoring window recorded in `DECISION.md`.

## Child DOX Index

- `panel/` — per-cycle panel artifacts and the Lead decision document; owned by this doc.
