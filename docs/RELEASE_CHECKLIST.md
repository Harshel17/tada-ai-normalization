# Release checklist

One current document: the state of the submission, the evidence for it, and what is still the author's to do.
Counts below were produced by running the commands shown, in this working tree and in a **clean copy built from the README
alone** (no `.env`, `.venv`, `node_modules` or caches). Earlier revisions of this file accumulated per-change addenda with
historical test counts; those were folded into the sections below and into ENGINEERING_NOTES.md section 12, so no stale number remains.

**Recommendation: GO**, with one decision for the author (repository visibility, section 9) and one task (creating the repository, section 10).
No blocking defect is known; the limitations in section 8 are documented and acceptable.

## 1. Mandatory requirements of the assignment

| # | Requirement | Evidence | Status |
|---|---|---|---|
| 1 | Normalize **employee data** (profile/contact, status and dates, job/organization, compensation) for PrismHR, isolved, UKG Ready | `src/normalization/adapters/*.py`, `Employee`; `test_prismhr.py`, `test_isolved.py`, `test_ukg_ready.py`, `test_employee_coverage.py` | Met. Typed mapping covers 38 % / 47 % / 46 % of the documented employee fields; the rest is preserved verbatim in `extras` (not counted as mapped), see README and AUDIT section 5 |
| 2 | **Direct deposits**: account types, allocations, amounts/percentages, priority, status/effective dates | `DirectDepositInstruction` (`account_type`, `allocation_type`, `amount`, `percentage`, `sequence`/`source_position`, `status`, `effective_from/to`) | Met (effective dates only where a provider has them) |
| 3 | **Deductions**: setup and deductions recorded in payroll | `DeductionSetup` vs `DeductionLine` | Met |
| 4 | **Payroll**: runs, paychecks, earnings, hours, gross/net, taxes, deductions, benefit contributions | `PayrollRun`, `Paycheck` and four line types | Met (UKG exposes no net pay; PrismHR runs are derived from `batchId`; documented) |
| 5 | Keep company ↔ employee ↔ payroll relationships | natural keys + FK linking; `test_service.py::TestRelationshipIntegrity`, `test_persistence.py` | Met (isolved paychecks are linked to a run only when the caller supplies the run; documented) |
| 6 | Document gaps, assumptions, decisions with reasons | `DECISIONS.md` (short), `docs/ENGINEERING_NOTES.md` (long), `docs/PROVIDER_MAPPINGS.md`, `docs/AUDIT.md` | Met |
| 7 | Discover mappings; own canonical schemas/enums; **synthetic test data** | `src/normalization/canonical/`; hand-written fixtures plus seeded `scripts/generate_fixtures.py` | Met |
| 8 | Preserve meaning; undocumented codes stay unknown | `*_raw` fields beside every enum, `unmapped_code` issues, no-data-loss and generated-payload tests | Met |
| 9 | Use isolved's primary snapshot; UKG is not OpenAPI | only `isolved-v6.swagger.json` is read | Met |
| 10 | Deterministic tests runnable **without credentials or live services**; tokens/records out of Git | 1074 tests need nothing; live probe is a separate tool; secret scan clean (section 6) | Met |
| 11 | **README**: approach, how built, setup and run | `README.md`; every command executed in a clean copy (section 3) | Met |
| 12 | **Markdown file of assumptions and decisions** | `DECISIONS.md` | Met |
| 13 | **Submit a link to your own repository** | not created yet (by instruction) | Pending: section 10 |
| – | Optional: write path to a provider | dry-run only: `POST /migrate/preview/ukg-ready` + Migration Preview screen (README, ENGINEERING_NOTES section 13); nothing is ever sent | Done as a dry run; no real write |
| – | Optional: live demo access | `scripts/live_probe.py`; results in ENGINEERING_NOTES.md section 12 | Done for the scenarios the demo tenants permit |

## 2. Verification results

| Check | Command | Result |
|---|---|---|
| Backend, everything | `uv run pytest` | **1127 passed**, 2992 subtests |
| Backend, offline | `uv run pytest -m "not db"` | 1074 passed (no database needed) |
| Backend, PostgreSQL | `uv run pytest -m db` | 53 passed (schema built by the real Alembic migrations); **skipped, not failed, when PostgreSQL is unreachable** |
| Canonical snapshots | `uv run python scripts/snapshot_canonical.py` | 17 verified (pinned test-only hash salt, independent of the developer's `.env`) |
| Lint / format | `uv run ruff check .` / `uv run ruff format --check .` | All checks passed / 84 files formatted |
| Frontend | `cd frontend && npm run check` (typecheck, lint, unit tests, build) | **406 passed** in 30 files; clean |
| Browser (real FastAPI + PostgreSQL + Vite + Chromium) | `cd frontend && npm run e2e` | **50 passed** on a separate, rebuilt `tada_normalize_e2e` database |
| Browser, PRODUCTION build (`vite build` + `vite preview`) | `cd frontend && npm run e2e:prod` | **7 passed**: all three one-click examples, the migration preview and its address-country action, readable relationships, no dev-only requests, no live-shape data in `dist/` |
| Migrations | `alembic upgrade head` on an empty database; round trip and drift check done at the original review | 13 tables; downgrade/upgrade round trip correct; no model/migration drift |
| Reference pack | `uv run tada-normalize check-inputs` | **exit 2 is expected** (the starter pack's own distribution flags; explained in the README) |

## 3. Setup from a clean environment

A copy without `.env`, `.venv`, `node_modules` or caches was set up in the README's order: `uv sync` → `uv run python scripts/setup_env.py`
(creates `.env`, generates a random 256-bit `ACCOUNT_HASH_SALT`, mode 600; nothing printed) → a fresh database → `uv run alembic upgrade head` → tests →
`uvicorn` → the README `curl` (inserted 2, then unchanged 2; read-back correct; a direct-deposit ingest works, so the salt is read from the generated
`.env`) → `cd frontend && npm ci && npm run check`. No step outside the README was needed. `docker compose up -d` was replaced by a separate database on an
already-running PostgreSQL only to avoid disturbing a developer's running container.

## 4. CLI and API

* **CLI:** `normalize` runs for all 15 provider × entity combinations (exit 0, expected counts); overwrite refusal, missing-tenant message, unreadable
  input and a missing or weak salt (usage error, secret never shown) behave as documented.
* **API:** `/health` reports `database: up`; all combinations return 200 on `/normalize` and `/ingest`; invalid JSON 400, unknown provider 404, payload
  errors 422 with nothing stored, a stranger tenant gets `[]` / 404, a misconfigured salt gives 500 with a safe message. Stored reads never contain
  `extras`; no response, file or trace contains the account hash.
* **Isolation and idempotency:** the same payload under two tenants gives separate rows; re-ingest is `unchanged`; a changed value is an `updated` row, never a duplicate.

## 5. Live API validation

Read-only, in memory, host-validated, nothing written to the database (ENGINEERING_NOTES.md section 12 has every decision; AUDIT section 6 the matrix).
Verified for all three providers: companies, employees, direct deposits, payroll runs and paychecks. **Blocked by the demo tenants:** isolved deductions
(401, provider permissions) and UKG Ready deductions (404, endpoint not served), isolved legal-level checks and `checks/years` (404).
**Untested:** pagination past the first page, PrismHR single-employee payroll and payroll with `options`, UKG pay statements beyond the first payroll that
has any. **Unresolved by design:** isolved paycheck-to-run linking (documented evidence and why it was not adopted).

## 6. Security review

| Check | Result |
|---|---|
| Real API key / real salt in any non-ignored file | none (scanned ~675 files; both exist only in the ignored, owner-only `.env`) |
| Token, key, bearer-token patterns | none in our files (the only hits are sample tokens in the vendor's cached UKG HTML) |
| Live demo identifiers or personal data in the repo | none; `tests/fixtures/live_shapes/` are structural copies with synthetic values |
| Logs | only counts, exception type names and non-secret messages; the CLI prints no payload data |
| Insecure defaults | **no default salt**: outside `APP_ENV=test` a missing, placeholder, short or low-variety `ACCOUNT_HASH_SALT` is a clear error; `APP_ENV` defaults to `production`; `scripts/setup_env.py` generates a secure one |
| Account hash | stored for persistence, **never serialized** (API, CLI file, traces, `repr`, OpenAPI, snapshots); record ids do not embed account data and do not depend on the salt |
| Unmasked live data | live isolved/UKG account numbers and SSNs reach no output, trace, log or HTTP response (tests and in-memory checks) |
| API exposure | unauthenticated and local-only, documented; it returns names and birth dates |
| Dev credentials | `tada_dev_password` appears in the local database defaults (`docker-compose.yml`, `.env.example`, `settings.py`, test config); local development only |

## 7. Repository audit (what Git would track)

| Check | Result |
|---|---|
| Files / size | 634 files, 17.3 MB (394 in `contracts/`, whose largest file is 2.9 MB); checked by staging a copy in a scratch repository |
| Ignored and present locally | `.env`, `.venv`, `frontend/node_modules`, `dist`, `playwright-report`, `test-results`, caches |
| Tracked on purpose | `.env.example` (no salt value), `uv.lock`, `frontend/package-lock.json`, `tests/snapshots/` |
| Junk (screenshots, databases, logs, `.DS_Store`, zip/bak) | none |
| Contracts | the organizer's reference pack, **not byte-identical to the vendors' originals** (isolved/UKG copies modified by the organizer: authentication examples redacted, notices added); PrismHR licence and NOTICE files kept |
| Sensitive-looking fixture values | SSN-shaped values are `123-45-6789`, `987-65-4321` and the invalid 9xx range (`900-44-3333`, `912-34-6789`, `907-31-4417`, ...), never an issued-looking number; fake account numbers, all synthetic; routing numbers are public bank identifiers |

## 8. Known limitations (documented, not blockers)

* Typed employee mapping is 41 / 47 / 48 % (PrismHR / isolved / UKG); the remainder is preserved verbatim in `extras` by design (protected demographics, provider metadata).
* Provider gaps: UKG has no company master, net pay or benefit lines; PrismHR has no run object or termination date; isolved statuses are free text and a
  check does not name its run.
* Fixed in the final correctness pass: a payload with the same record id twice and different content is now rejected before storing (CLI, API and app); see ENGINEERING_NOTES.md section 14.
* An existing database needs `alembic upgrade head` once (migration 0003 adds nullable columns); nothing else about stored data changes.
* Live validation limits in section 5; deduction adapters for isolved and UKG Ready have fixture coverage only.
* The API has no authentication; the optional write path is not built; fetching or paging provider APIs is out of scope.

## 9. Repository visibility: PRIVATE (decided)

`contracts/isolved/NOTICE.md` and `contracts/ukg-ready/NOTICE.md` state that the organizer authorized a **private** upload and that **no provider
redistribution grant or public publication approval** is represented. PrismHR's spec is Apache-2.0. The assignment asks for a link to a repository
"containing everything needed to run your solution", and the tests need the contracts. Decision: **publish as a private repository and give the
reviewer read access** (workflow in section 10). Making it public requires the organizer's written confirmation that sharing the reference pack is allowed;
nothing in the code changes either way.

## 10. Publishing commands (not yet run) and private collaborator access

Review first; none of this has been executed.

```sh
cd /Users/harshel/Desktop/TadaNormalize

# 1. initialise, then PREVIEW what would be staged (nothing is added yet)
git init -b main
git add -A -n | wc -l                                                                                   # 634
git add -A -n | grep -E '(^|/)(\.env$|\.venv|node_modules|dist/|__pycache__|\.pytest_cache|\.ruff_cache|playwright-report|test-results|\.live)'   # nothing
git add -A -n | grep -c '\.env\.example'                                                               # 1

# 2. stage and re-verify
git add -A
git ls-files | wc -l                                                                                    # 634
git ls-files | grep -iE '\.(pem|key|db|sqlite|log|har|zip)$|(^|/)\.env$'                                  # nothing
git status --short --ignored | grep '^!!'                                                               # only ignored local files

# 3. commit
git commit -m "Tada Normalize: PrismHR/isolved/UKG Ready normalization, API, CLI, console, UKG migration dry run

Normalizes company, employee, direct deposit, deduction setup and payroll data from
three providers into one canonical model, with provenance, integrity checks, idempotent
PostgreSQL ingestion, a FastAPI layer, a CLI, a local testing console and a UKG Ready
create-employee dry-run preview (nothing is ever sent).

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"

# 4. create a PRIVATE repository and push
gh auth status
gh repo create <you>/tada-normalize --private --source=. --remote=origin --push

# 5. give the reviewer READ-ONLY access (replace the username); they accept the emailed invitation
gh api -X PUT repos/<you>/tada-normalize/collaborators/<reviewer-github-username> -f permission=pull
gh api repos/<you>/tada-normalize --jq .private                                                          # true

# 6. verify the remote copy works from nothing
git clone "$(gh repo view --json url -q .url)" /tmp/tada-normalize-verify
cd /tmp/tada-normalize-verify && uv sync && uv run python scripts/setup_env.py && docker compose up -d \
  && uv run alembic upgrade head && uv run pytest -q
```

Without the GitHub CLI: create an empty **private** repository in the web UI, add the reviewer under Settings → Collaborators, then
`git remote add origin <url> && git push -u origin main`. Do not switch the repository to public without the organizer's written confirmation.

## 11. The optional testing console (frontend)

`frontend/` is optional (React, TypeScript, Vite, Tailwind, TanStack Query, Monaco; no CDN, no third-party requests). It opens on **Normalize**
(select provider → upload JSON or try example data → automatic detection → normalize → results written in plain words for all six record types, with Save to Database as a separate explicit step), with a
**Pipeline Explorer** (original source → transformation → standard output, grouped, from provenance recorded by the adapters), Data Explorer, Test Runs, an
Advanced mode and a "System status" page. Browser tests prove detection and normalization per provider, ambiguity and unsupported-file handling, missing-id
blocking, save separation and idempotency, the pipeline groups, withheld values, and that **no request leaves localhost and nothing is written to browser
storage**. Run `cd frontend && npm install` after cloning; `npm run e2e` needs Docker PostgreSQL and `npx playwright install chromium` the first time.
