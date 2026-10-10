# FUBOT_STEPS/ — Build Step-by-Step Guide

## Purpose

Contains 42 sequential markdown step files (PHASE1–PHASE6) used by human agents to build the FUBOT platform from scratch. Each step is a single markdown file with precise commands, file contents, and verification steps.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/FUBOT_STEPS/`

## Local Contracts

- Steps are numbered and phased: `PHASE1_STEP1.md` through `PHASE6_STEP6.md`
- Steps must be executed in order — later steps depend on earlier ones
- Each step file is self-contained: it states the goal, provides exact commands/file contents, and ends with a verification check
- The `FUBOT_PRODUCT_PLAN.md` in the repo root is the source of truth — step files implement it

## Work Guidance

### Phase Structure

| Phase | Steps | Focus |
|-------|-------|-------|
| PHASE1 | STEP1–7 | Project foundation, FastAPI backend scaffold, auth |
| PHASE2 | STEP1–8 | Database models, Alembic migrations, PostgreSQL setup |
| PHASE3 | STEP1–7 | Core services, Binance integration, WebSocket |
| PHASE4 | STEP1–4 | Trading strategy engine, indicators, signal generator |
| PHASE5 | STEP1–10 | Frontend React dashboard, all pages, state management |
| PHASE6 | STEP1–6 | Deployment, Nginx, SSL, VPS hardening, systemd |

### Step File Format (standard)
```
## Goal
[One sentence describing what this step accomplishes]

## Commands
[Exact bash commands to run]

## File: [path/to/file]
[Complete file content to write or replace]

## Verify
[Commands to confirm the step succeeded]
```

### Key Constraints
- Steps use `uv` for Python package management
- Backend runs on port 8000, frontend on port 5173 (dev) / 80 (prod)
- Nginx proxies frontend to `/` and backend API to `/api/v1`
- Database migrations via `alembic upgrade head`
- Bot runs as a systemd service `fubot-bot.service`

## Verification

- Each step ends with a verification block
- A step is complete when its verify commands return the expected output
- Skipping steps is not allowed — each step depends on the previous

## Child DOX Index

This directory contains only step markdown files. No further child AGENTS.md needed.
