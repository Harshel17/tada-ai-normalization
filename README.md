# Tada Normalize

Normalizes **PrismHR**, **isolved** and **UKG Ready** payloads (companies, employees, direct deposits,
deduction setup, payroll runs and paychecks) into one canonical model, validates the result, and can
store it in PostgreSQL.

* **Start here: [DECISIONS.md](DECISIONS.md)**, the short explanation of my decisions, assumptions and limitations (the assignment's decisions file).
* More detail, if you want it: [engineering notes](docs/ENGINEERING_NOTES.md) (full reasoning and live-validation findings)
* **Field-by-field mappings** and documented gaps: [docs/PROVIDER_MAPPINGS.md](docs/PROVIDER_MAPPINGS.md)
* **Requirement matrix, audit findings and remaining limitations**: [docs/AUDIT.md](docs/AUDIT.md)
* **Submission readiness evidence and publishing steps**: [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)

```
provider JSON ──► adapter (explicit mapping) ──► canonical records + issues ──┬─► CLI JSON file
                  prismhr / isolved / ukg_ready    (Pydantic v2, Decimal)     ├─► POST /normalize
                                                                              └─► ingest ─► PostgreSQL
```

The normalization engine (`src/normalization/service.py` and below) knows nothing about FastAPI or
PostgreSQL. The CLI `normalize` command and all adapter tests run without either.

### What "complete employee data" means here

Two different things are measured, and they are not mixed up:

* **Typed canonical mapping** — a source field lands in a dedicated, typed canonical attribute (a date, a decimal,
  an enum with its `*_raw` twin, ...). For the documented employee fields this is **41 % for PrismHR, 47 % for isolved and
  48 % for UKG Ready** (38 / 47 / 46 % before the final correctness pass, same method) (`uv run python scripts/coverage_report.py`, seeded contract-generated payloads; the audit
  formula and method are in [docs/AUDIT.md](docs/AUDIT.md) section 5).
* **Lossless preservation** — every source field that is not typed is kept verbatim in the record's `extras`
  (sensitive names redacted), grouped by reason in the coverage report (provider workflow metadata, health and skills
  records, documents, protected demographics that are deliberately not promoted, ...). Nothing is dropped
  (`tests/test_no_data_loss.py`), but **preserved-in-extras is not counted as mapped**.

## Prerequisites

* Python 3.12 and [uv](https://docs.astral.sh/uv/) (`uv` installs Python 3.12 itself if needed)
* Docker, only for PostgreSQL (persistence, the `ingest` endpoint/command and the `db` tests)

## Setup

```sh
uv sync                              # installs everything into .venv
uv run python scripts/setup_env.py   # creates .env, generates a random ACCOUNT_HASH_SALT, chmod 600
docker compose up -d                 # PostgreSQL 16 on 127.0.0.1:54329 (skip if you only normalize)
uv run alembic upgrade head          # create the tables (an existing database: run it again, migration 0003 adds columns)
```

**`ACCOUNT_HASH_SALT` is required and has no default.** It salts the hash stored instead of bank account numbers.
`scripts/setup_env.py` generates a random 256-bit value into your local, owner-only `.env` and never prints it (it keeps an
existing secure one; `--check` only verifies). A missing, placeholder or short (<32 characters) salt makes hashing fail with a clear
error (HTTP 500 from the API, a usage error from the CLI) rather than silently using a weak one. Changing the salt changes the hash
of every account processed afterwards; see the engineering notes, "Account hash salt". Tests pin `APP_ENV=test` and a fixed test-only salt
themselves, so they need no `.env`.

The backend, CLI and API need nothing from `frontend/`. In a backend-only checkout the single test that compares the
frontend's trace-grouping reference file is skipped; every other test runs.

## CLI (no server, no database)

Provider responses do not repeat ids that live in the URL (PrismHR `clientId`, isolved `legalId`,
UKG company/employee ids), so you pass them as context: `--tenant-id` is always required.

```sh
uv run tada-normalize normalize \
  --provider prismhr --entity employee \
  --tenant-id demo --company-id ACME \
  --input tests/fixtures/prismhr/employee_get_employee.json \
  --output normalized.json            # add --force to overwrite
```

More examples (every file under `tests/fixtures/` is a runnable input):

```sh
# isolved: company_id is the legal company id; the response holds a payroll run and its checks
uv run tada-normalize normalize --provider isolved --entity payroll --tenant-id demo \
  --company-id 4501 --input tests/fixtures/isolved/payroll.json --output isolved_payroll.json

# UKG Ready: an employee-scoped response needs the employee (account) id
uv run tada-normalize normalize --provider ukg-ready --entity direct_deposit --tenant-id demo \
  --company-id CID1 --employee-id 7770001 \
  --input tests/fixtures/ukg_ready/direct_deposits.json --output ukg_deposits.json
```

Entities: `company`, `employee`, `direct_deposit`, `deduction` (setup), `payroll` (runs + paychecks).
Exit codes: `0` ok, `1` the payload had errors (the output file still lists them), `2` bad usage or
unreadable input. Issues are also printed to stderr. Store the result instead of writing a file with
`tada-normalize ingest ...` (same flags, minus `--output`).

`uv run tada-normalize check-inputs` audits the distributed reference pack under `contracts/` (checksums, licence and
distribution metadata). **Exit code 2 with a few messages is the expected result**: it reports the starter pack's own
flags (for example "Candidate-distribution permission has not been verified" for isolved and UKG Ready, and that UKG Ready
ships cached HTML pages rather than an OpenAPI file). It does not indicate a problem with the normalizer, and
`tests/test_reference_integrity.py` checks the same pack in the test suite.

## HTTP API

```sh
uv run uvicorn normalization.api.app:app --port 8000      # docs at http://127.0.0.1:8000/docs
```

> **Local use only.** There is no authentication or authorization, and the API reads and writes
> payroll data. Do not expose it beyond your own machine without adding both.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | liveness; reports `database: up/down` but never fails because of it |
| GET | `/stats` | supported providers / entity types and stored record counts per provider, tenant and company (counts only, no record data) |
| POST | `/normalize/{provider}/{entity_type}` | normalize, store nothing |
| POST | `/ingest/{provider}/{entity_type}` | normalize and store (one transaction; re-saving the same payload is a no-op. A record that also arrives from a different file or location is stored once but counts as an update: `version` + 1 and the newest `provenance`) |
| POST | `/detect/{provider}` | say what each of several JSON files contains (deterministic rules, no database, no LLM) |
| POST | `/migrate/preview/ukg-ready` | **dry run only**: canonical employee + destination company id (+ operator input) in, UKG Ready create-employee draft, readiness and what is not migrated out; read-only, nothing is sent or stored (see the migration section) |
| POST | `/convert/{provider}` | detect, then normalize every file: **preview only, there is no save option**; `"trace": true` adds provenance, `"integrity": true` adds deterministic payroll checks |
| GET | `/tenants/{tenant}/{provider}/companies[/{company}]` | stored companies |
| GET | `.../companies/{company}/employees[/{employee}]` | stored employees |
| GET | `.../employees/{employee}/direct-deposits`, `/deductions`, `/paychecks` | per employee |
| GET | `.../companies/{company}/payroll-runs[/{run}/paychecks]`, `/paychecks/{id}` | payroll |

**Input safeguards (CLI and API alike):** a payload that holds the same record id twice with different content is rejected before anything is
stored (`duplicate_record_id`, HTTP 422); an identical repeat is a warning and is stored once; a provider *error response* (PrismHR
`errorCode`, isolved `validationResults`, UKG `errors`) is reported as `provider_error` instead of as records with missing ids; amounts are
exact decimals written as plain text (`1E+3` becomes `1000`). See [docs/ENGINEERING_NOTES.md](docs/ENGINEERING_NOTES.md) section 14.

The request body is an envelope: `{"context": {...}, "payload": <provider response, unchanged>}`.

```sh
curl -s -X POST localhost:8000/ingest/prismhr/employee -H 'content-type: application/json' \
  --data "{\"context\": {\"tenant_id\": \"demo\", \"company_id\": \"ACME\"}, \"payload\": $(cat tests/fixtures/prismhr/employee_get_employee.json)}"
# {"ok":true,"counts":{"employee":2},"stored":{"employee":{"inserted":2,"updated":0,"unchanged":0}}, ...}
# send it again: {"stored":{"employee":{"inserted":0,"updated":0,"unchanged":2}}}

curl -s localhost:8000/tenants/demo/prismhr/companies/ACME/employees/E1001
```

### Live demo validation (optional, development only)

`uv run python scripts/live_probe.py prism --allow-host <host returned by sign-in>` signs in with `DEMO_SIGNIN_API_KEY` (environment or the
gitignored `.env`), calls documented read-only endpoints, normalizes the responses in memory and prints structure only (key names, types, counts,
issue codes), never values or credentials. It never writes to the database, refuses redirects, and refuses a base URL host that is not documented
unless you allow it explicitly. It is not part of the test suite (tests use fake transports and fixtures). PrismHR accepts several employee ids as
repeated `employeeId` query parameters. `uv run python scripts/live_probe.py isolved` probes isolved the same way (`--link-analysis` only counts how
checks could match run history). Findings: [docs/ENGINEERING_NOTES.md](docs/ENGINEERING_NOTES.md) section 12.

### Detection, preview conversion and provenance

`/detect` and `/convert` take `{"files": [{"name": "...", "content": <provider JSON>, "entity": "<optional confirmation>"}], "context": {...}, "trace": true}`.

* **Detection is deterministic and provider-aware** (`src/normalization/detect.py`): documented wrapper keys first, then
  fields specific to one record type; generic fields such as `id`, `name`, `status`, `amount` are never evidence. For each file
  you get the record types found (with counts taken from the input, nested records such as a PrismHR employee's deposits and
  deductions counted once), the evidence, the context ids each needs, and a status: `ok`, `ambiguous` (candidates are listed
  and your choice is checked against the structure the adapter expects), `unsupported` (with a hint when another provider's
  rules match) or `empty`.
* **Context is never invented.** Missing ids give `needs_context` and nothing is normalized. Ids stated in other files you
  sent (a PrismHR `clientMaster.id`, employee ids) come back as `suggestions`; the client must confirm them.
* **`/convert` never writes.** Saving stays a separate explicit call to `/ingest` with the same file.
* **Integrity checks** (`"integrity": true`): one report over every converted record, with eleven deterministic checks, each `passed`,
  `warning`, `failed` or `not_evaluated` (with the reason; nothing is guessed). *Identity:* no record id appears twice (different content: failed;
  an identical copy: warning). *Relationships:* every paycheck's run is among the converted runs (failed); every deposit, deduction and paycheck
  points to a converted employee (warning: it may be in another file). *Dates:* pay periods start before they end and deposits/deductions start
  before they stop (failed); employees are hired before they leave (warning: rehires). *Money:* earning lines add up to gross pay and payments add
  up to net pay, **only for PrismHR**, where this held on every live voucher, and only when every needed amount is present (a difference is a warning,
  never a failure; UKG's gross is calculated by the normalizer, so comparing it would prove nothing; isolved's differed on every comparable live
  check). *Deposits:* percentage deposits of one kind add up to 100 % or less and at most one remaining-balance deposit exists, per employee **and
  voucher type** (live PrismHR employees have separate regular and supplemental deposits). Deliberately not evaluated: gross minus taxes and
  deductions equals net, and check amount equals net pay (both gave false alarms on live data). The report holds only ids, dates, amounts and sentences,
  and checking never changes the records.
* **Provenance** (`"trace": true`): for each record, the source field(s) that produced each standard field and the operation
  (copy, number/date/boolean parse, enum lookup with the table name, combine, derive, route, withhold). Traces are captured by
  the adapters while converting (`to="field"` on typed reads, never by comparing values), include only the fields that actually
  contributed to the final value, exist only in the response, and show sensitive source values as `[withheld]`. Fields
  without recorded provenance simply have no trace entry.

Bank account numbers are never returned: records carry `account_number_masked` (`****1234`) and the routing number. The salted
account hash is stored in PostgreSQL for recognising the same account but is **never serialized** (not in `/normalize`, `/convert`,
the read endpoints, the CLI output file or traces).

Status codes: `200` ok · `400` body is not JSON · `404` unknown provider/entity/record · `413` body over
20 MB · `422` the payload had errors (the body lists the issues, nothing is stored) · `500` server misconfiguration
(for example a missing or weak `ACCOUNT_HASH_SALT` when an account number is hashed; the message never shows the secret) ·
`503` database down.
Stored records are returned without the raw `extras`, because those can contain sensitive source data.

## Migration preview to UKG Ready (optional extension, DRY RUN only)

The assignment's optional extension asks for a path from canonical employee data to a destination provider. This repository
implements the **dry-run** variant for one destination, UKG Ready, and **never sends anything**: no provider call, no credentials,
no database access, no persistence. READY means *the draft passed the documented local checks*, never that UKG accepts it.

* **Contract used:** the local UKG reference, `POST /v2/companies/{cid}/employees` ("Creates a new Employee record"), schema in
  `contracts/ukg-ready/reference-schemas.json`. The reference itself says it is not a complete endpoint contract and not a verified
  OpenAPI, and nothing was tested against a tenant. The finished draft is validated against that very schema.
* **API:** `POST /migrate/preview/ukg-ready` with `{employee: <canonical employee>, destination: {company_id, ein_id?}, operator: {...}}`
  returns the draft body, readiness (`READY` / `NEEDS_REVIEW` / `BLOCKED`), per-field mappings, requirements, issues and everything
  not migrated. Backend-only reviewers can call it with the `employee` records from `/normalize` (see `tests/test_migration_preview_ukg.py`).
* **Never invented:** `username` is required by UKG and cannot come from the source, so a missing one is `BLOCKED`; a source username is
  only a suggestion that must be confirmed. Source ids are sent (as `employee_id` / `external_id`) only when the operator ticks them; the
  destination assigns its own id. Termination, retirement and deceased dates, gender (a separate UKG call), compensation, taxes, SSN
  and bank data are never in the draft and are listed, with the reason, under "not migrated". If the source has no primary email, personal
  and work email are not chosen between.
* **In the app:** Normalize, click an example (or normalize your own data), choose an employee, enter the destination company ID and a
  username, and press "Preview migration to UKG Ready". Source, canonical and the draft are shown side by side under a
  "DRY RUN — No data has been sent to the destination provider." banner.

## Repository visibility (private) and reviewer access

The repository must stay **private**. `contracts/` contains the organizer's reference pack: PrismHR's spec is Apache-2.0, but the isolved
and UKG Ready materials were uploaded privately by the organizer and their NOTICE files state that **no provider redistribution grant or public
publication approval** exists. Publish privately and give the reviewer read access:

```sh
gh repo create <you>/tada-normalize --private --source=. --remote=origin --push
gh api -X PUT repos/<you>/tada-normalize/collaborators/<reviewer-github-username> -f permission=pull   # read-only invitation
```

The reviewer accepts the emailed invitation (or the one under github.com/notifications) and clones. Only make it public if the
organizer confirms in writing that sharing the reference pack is allowed.

## Tests

```sh
uv run pytest                  # everything; the `db` tests need `docker compose up -d`
uv run pytest -m "not db"      # offline: adapters, service, CLI, API (no PostgreSQL)
uv run ruff check . && uv run ruff format --check .
```

The tests are deterministic and offline: synthetic fixtures in `tests/fixtures/`, written in the
documented provider response shapes. `tests/test_contract_fields.py` fails if a fixture uses a field
name that does not exist in the contracts under `contracts/`. Two more suites guard the data:
`test_no_data_loss.py` replaces every source value with a marker and requires it in the output, and
`test_generated_payloads.py` runs payloads generated from the contracts (every documented field
filled, seeded, see below) and checks for loss and for leaked secrets.

Three more suites protect the newer features: `test_canonical_snapshots.py` compares the normalized output of
all 14 fixture files (17 runs) byte-for-byte with `tests/snapshots/canonical/` so adding provenance cannot change a
converted value (`uv run python scripts/snapshot_canonical.py` checks them; `--update` rewrites them after a reviewed,
intentional change); `test_detect_convert.py` covers detection for every provider/entity scenario plus mixed, ambiguous,
unsupported, empty, duplicate, multi-file, missing-context and sibling-suggestion cases; `test_integrity.py` covers every integrity check in all four statuses, the no-false-alarm cases from live data, privacy and the opt-in flag; `test_provenance.py` and `test_provenance_a1.py` assert exact
source paths (and, for every traced field of every fixture, that the path exists in the payload and the value re-derived from it equals the record), identical values in several fields, fallbacks, missing/null/invalid fields, enum conversions, derived values,
nested records and that no sensitive value appears in any trace.

Live validation left permanent regression tests that need no network or credentials: `test_prismhr_live_shapes.py`,
`test_isolved_live_shapes.py` and `test_ukg_live_shapes.py` use sanitized, structural copies of live responses
(`tests/fixtures/live_shapes/`: real key names and nesting, synthetic values); `test_live_probe.py` checks the probe's safety rules
with fake transports; `test_salt_security.py` covers the `ACCOUNT_HASH_SALT` rules; `test_hash_not_exposed.py` proves the
internal account hash appears in no API response, CLI file, trace, `repr`, OpenAPI schema or snapshot while still being stored.

```sh
uv run python scripts/generate_fixtures.py     # writes tests/fixtures/generated/*.json (seeded, byte-identical)
```

The database tests create a separate `tada_normalize_test` database and build its schema with the real Alembic migrations.
Without PostgreSQL they are **skipped**, not failed (`uv run pytest` still passes).

## Layout

```
src/normalization/
  canonical/       enums.py, models.py          the provider-independent model
  adapters/        prismhr.py isolved.py ukg_ready.py   explicit per-provider mappings
                   base.py                       Src (tracks unread fields), parsers, masking
  service.py       pick adapter, run it, return structured issues (opt-in provenance trace)
  detect.py        deterministic, provider-aware detection of what a JSON file contains
  convert.py       multi-file detect + preview conversion + id suggestions (never writes)
  integrity.py     deterministic checks over converted records (ids, links, dates, earnings/net pay, deposits); informational
  sensitive.py     the ONE policy for sensitive data kept in `extras`: word-based key names, labelled values, SSN-formatted
                   values; matches are replaced by [redacted] and listed in provenance.redacted_paths
  inputs.py        input hygiene before any adapter runs: NUL characters and lone surrogates are refused (HTTP 422)
  migration/       UKG Ready create-employee DRY RUN: pure mapper (ukg_ready.py), readiness, a checker that validates the draft
                   against the documented schema in contracts/, request/response models. Never sends anything; no DB, no network
  persistence/     tables.py repository.py db.py        PostgreSQL + idempotent ingest
  api/             app.py schemas.py             FastAPI (thin)
  cli.py           command line
alembic/           migrations
frontend/          optional local testing console (React + TypeScript), see below
scripts/           generate_fixtures.py (seeded fixtures from the contracts)
                   coverage_report.py   (employee mapping coverage, see docs/AUDIT.md section 5)
                   snapshot_canonical.py (checks/updates the canonical-output snapshots)
                   setup_env.py   (creates .env with a random ACCOUNT_HASH_SALT, owner-only)
                   live_probe.py  (optional, read-only check against the Tada demo providers)
contracts/         provider reference pack supplied with the assignment. NOT byte-identical to the vendors' originals: the
                   isolved and UKG Ready copies were modified by the organizer (authentication examples redacted, notices added;
                   see each NOTICE.md and SOURCE_MANIFEST.json). Distribution is private-only, see "Repository visibility".
```

## Optional: local testing console (frontend)

A small web console for trying the whole system by hand. **It is optional and not part of the
normalization assignment**: the engine, CLI and API work without it, and nothing in the backend depends
on it. It is a developer tool for **localhost only**; the API it talks to has no authentication.

Stack: React, TypeScript, Vite, Tailwind CSS, TanStack Query, Monaco editor (bundled locally, no CDN).

| Section | What it does |
| --- | --- |
| **System status** (footer link, `#/overview`) | Not part of the main navigation. API and database connectivity (`/health`), supported providers and entity types (`/stats`), and real stored-record counts. An empty database shows an empty state, never invented numbers. |
| **Normalize** | The landing screen (`http://127.0.0.1:5175` opens it directly). **One click:** the *PrismHR / isolved / UKG Ready example* buttons load that provider's bundled example files, use the ids those examples were written with, and normalize them; the results show a summary (records, typed fields filled, values preserved in `extras`, relationships, integrity totals, protected values), a relationships view, the integrity checks, and **Preview migration to UKG Ready** (dry run). **Step by step:** *Select payroll provider → Add your data (Upload JSON, or Try example data for the provider's complete example set) → Detect Contents → Normalize → Results*. You never say what a file is: detection does, shows its evidence, and asks only when it is genuinely ambiguous. Ids a file does not contain are suggested from your other files and need your confirmation. **Employee ownership:** a bare deposit or deduction list does not say which employee owns it, so the Employee ID you give is assigned to those records. The app says so, never picks an employee for you (other employees in your files are only candidates), shows the example set's own convention for the bundled example files (`tests/fixtures/example_owners.json`: a convention, not something those files state; never applied to uploads) and requires an explicit confirmation. The confirmation is an acknowledgment, not proof: the integrity checks can only show that the employee exists in the data. Records that carry their own owner (deposits embedded in an employee) always keep it. Results show **Data checks** (the integrity report above, as Passed / Warning / Failed / Not evaluated with reasons; informational, they do not block saving) and describe every converted record in plain words (a **What we found** section per record type, with a record picker, plus counts of what was filled in, kept as received and withheld, and each issue explained), and offer **View Transformation Pipeline**, **View Normalized JSON**, **Save to Database** (a separate explicit step through `/ingest`) and **Start New Conversion**. **Advanced mode** (raw JSON editor, tenant context, provenance, issues) is one click away. |
| **Pipeline Explorer** | For the latest conversion, opening on an employee record that has mapping evidence: a compact header (provider, data, record, status, and the real count of fields with a recorded source), then mappings grouped by their actual destination as *original source → transformation → standard output* rows. Every record type has its own groups: employees (Identity, Employment, Dates, Job, Compensation), employers (Identity, Status and dates, Contact, Address, Legal entities), bank deposits (Account, How pay is split, Status and dates, Identity and links), deductions (What it is, Amount and rules, Dates and status, Payee and references), pay runs and paycheck headers (Dates, Totals, Type and status, ...), plus Privacy for withheld values. Enum lookups, true/false and date conversions, routed and derived values and withheld values are highlighted; plain copies are quieter; a value the normalizer set itself shows "No source field" and an id you entered shows `context.…`, so no source is ever invented. Paycheck line items (earnings, taxes, ...) are not traced yet and are named as such; any row expands for details. Search, operation filter, "Only real conversions" and record switching are available. Filled fields without a recorded source stay in a separate, collapsed **Source mapping not available** list (nothing is guessed); withheld paths and provider-specific data kept as received (keys only) are listed too. |
| **Data Explorer** | Browse stored companies, employees (with direct deposits, deductions, paychecks), payroll runs and paycheck line items through the read API, filtered by provider, tenant and company. |
| **Test Runs** | Runs the synthetic end-to-end scenarios (all 15 provider × entity combinations) through the public API: normalize, save, save again (must be unchanged), read back, tenant isolation, provider isolation. Every verdict is computed from the real responses; steps after a failure show as *skipped*, never as passed. |

### Run it

Needs Node 20+. Start the backend first (see the API section), then:

```sh
cd frontend
npm install
npm run dev            # http://127.0.0.1:5175  (loopback only; fails if the port is taken)
```

The dev server proxies `/api/*` to the FastAPI server on `http://127.0.0.1:8000`. If your API runs on
another port: `API_URL=http://127.0.0.1:8001 npm run dev`. The console always uses port 5175 and stops with an
error if it is busy (`strictPort`); to use another port: `CONSOLE_PORT=5176 npm run dev`.

**Production build.** `npm run build` writes `frontend/dist/`; `npx vite preview` serves it at **http://127.0.0.1:4173** (Vite's
preview default; add `--port N` to change it). The built app is static, so it still needs the **backend API running**: the preview
server proxies `/api/*` to `http://127.0.0.1:8000` exactly like the dev server, and `API_URL=http://127.0.0.1:8001 npx vite preview`
points it elsewhere. Without the API the page loads but normalizing shows "Cannot reach the API". The one-click examples need no
dev server and no network request for the example files (below).

The example buttons use the repository's synthetic `*.json` files in `tests/fixtures/{prismhr,isolved,ukg_ready}`, which Vite
compiles into the app (`frontend/src/api/fixtures.ts`). They therefore work the same under `npm run dev` and in a production
build (`npm run build`, then `npx vite preview`), with no dev-server middleware and no request for the files. Only those three
folders are bundled (not `tests/fixtures/live_shapes`), each file as its own lazy chunk. `npm run e2e:prod` runs the browser
tests against the production build.

### Manual test: the PrismHR example set (about two minutes)

1. `docker compose up -d`, `uv run alembic upgrade head`, then start the API and `npm run dev`.
2. Open http://127.0.0.1:5175 (it lands on **Normalize**) and choose **PrismHR**.
3. Press **Try example data**: four real example files are added and **Detect contents** lists what each holds
   (`2 employees`, `3 direct deposits (inside the employee records)`, `2 deductions`, a payroll run's `2 paychecks`, ...) and why.
4. Under *Identifiers this data needs*, press **Use ACME** and **Use E1001** (suggested from your own files; nothing is used until you do).
5. Press **Normalize Data**. Nothing is saved yet. Read the headline and each section's outcome.
6. Press **View Transformation Pipeline**, choose *Employee records · employee_get_employee.json*, and read a few rows:
   `client.lastHireDate` → *Read as a date* → `hire_date`; `client.statusClass` `A` → *Looked up in the EMPLOYMENT_STATUS table* → `active`;
   the SSN source is `[withheld]` and only the last four digits are kept. Open **Source mapping not available** to see what is not traced yet.
7. Back on **Normalize**, press **Save to Database**: new records saved. Press it again: *already saved, so nothing changed*.
8. Follow **Browse saved data in Data Explorer**. **Advanced mode** is the full technical tool; **Test Runs** executes the 15 end-to-end scenarios.
   Try **Upload JSON** with a file of your own (or a file from another provider) to see ambiguity, unsupported-file and missing-id handling.

Resetting the demo data: `docker compose down -v && docker compose up -d && uv run alembic upgrade head`.

### Privacy and safety

* Synthetic data only; the console talks to `127.0.0.1` and makes no request to any third party
  (checked by an e2e test). Nothing is written to `localStorage`, `sessionStorage`, cookies or IndexedDB.
* SSNs, account numbers, document contents and credentials are masked in every view, including your own
  input. Error messages never quote request bodies.
* Use of the API stays unauthenticated and local; do not expose the dev server or the API to a network.

### Frontend checks

```sh
cd frontend
npm run typecheck      # TypeScript project build
npm run lint           # ESLint
npm test               # 406 unit / component tests (Vitest, jsdom)
npm run build          # production build
npm run e2e            # 50 Playwright browser tests against the REAL stack (dev server)
npm run e2e:prod       # 7 Playwright tests against the PRODUCTION build (vite build + vite preview)
npm run check          # typecheck + lint + tests + build
```

`npm run e2e` (first time: `npx playwright install chromium`) starts its own FastAPI on port 8010 and Vite on
5180, deliberately not the console's 5175, so it does not collide with servers you have running. If those ports are busy:
`E2E_API_PORT=8011 E2E_WEB_PORT=5181 npm run e2e`. It uses a **separate database**
`tada_normalize_e2e` (name must end in `_e2e`), dropped and re-migrated at the start of each run, so
your development data is never touched. It needs the PostgreSQL container running.

## One record, end to end

How a PrismHR `getEmployee` response becomes a canonical employee and a database row:

1. **Entry.** CLI or API calls `service.normalize("prismhr", "employee", payload, context)`.
   The API parses the body with `parse_float=Decimal`, so `100000.00` is never turned into a float.
2. **Adapter.** `adapters/prismhr.py::employee` wraps each `employee[]` object in a `Src`. Every
   mapping line reads a field (`client.text("jobCode")`), which marks it as consumed.
3. **Code tables.** Documented codes become enums (`statusClass "A"` → `active`); undocumented ones
   stay in a `*_raw` field and are reported as an info issue.
4. **Sensitive fields.** `compensation.ssn` is read with `secret()`: only the last four digits are kept,
   and the field's path is listed in `provenance.redacted_paths`.
5. **Leftovers.** `Collector.build` takes everything nobody read, `src.leftover()`, and stores it as
   `extras`, so no source field is silently dropped. The record is validated as an `Employee`.
6. **Result.** `NormalizationResult(records, issues)`; bad values become issues instead of crashes.
7. **Ingest.** `repository.ingest` rejects results with errors, then for each record runs one
   `INSERT ... ON CONFLICT (provider, tenant_id, company_id, source_id) DO UPDATE ... WHERE
   content_hash differs`: insert, update (`version + 1`) or leave untouched. Foreign keys to parents are
   resolved afterwards, in the same transaction, so ingest order does not matter.
