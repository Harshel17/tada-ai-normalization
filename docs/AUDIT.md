# Independent audit against the assignment

Method: each requirement in the assignment was traced to code and tests, then three end-to-end runs
(one per provider: real HTTP → engine → PostgreSQL) were inspected for data loss, wrong mappings,
secret exposure and tenant leakage. A sentinel test (replace each source value with a unique marker,
require it in the output) and a contract-driven generator were added to find losses by machine
rather than by eye. Sections 1-5 are the original audit (kept as evidence); sections 6-7 were added after live validation and
the final submission audit. **Current state: 1127 backend tests pass (1074 offline, 53 against PostgreSQL; the database tests are
skipped, not failed, when PostgreSQL is unavailable), 406 frontend unit tests, 50 browser tests and 7 production-build browser tests pass, Ruff clean.** Counts quoted inside
sections 1-5 are the historical ones from the time they were written.

## 1. Requirement matrix

Status: **Met** · **Partial** (works, with a stated limit) · **Not done**.

| # | Assignment requirement | Implemented in | Verified by | Status |
|---|---|---|---|---|
| 1 | Normalize company, employee, direct-deposit, deduction and payroll data from PrismHR, isolved, UKG Ready | `adapters/prismhr.py`, `isolved.py`, `ukg_ready.py`; `service.py` | `test_prismhr.py`, `test_isolved.py`, `test_ukg_ready.py`, `test_service.py::TestEveryCombination` (15 provider×entity combos) | Met |
| 2 | Discover provider mappings; design own canonical schemas and enums | `canonical/models.py`, `canonical/enums.py`; `docs/PROVIDER_MAPPINGS.md` | `test_contract_fields.py` (fixture fields exist in contracts), `test_base.py::TestCanonicalModels` | Met |
| 3 | Employee: profile/contact, status and dates, job/organization, compensation; "all available fields" | `Employee`, `Compensation`; every unread source field kept in `extras` | `test_*::TestEmployee`, `test_no_data_loss.py`, `test_generated_payloads.py` | **Partial → improved** (§5): 36-47 % of documented employee fields now map to typed canonical attributes; the rest is preserved losslessly, most of it by design |
| 4 | Direct deposits: account types, allocations, amounts/percentages, priority, status/effective dates | `DirectDepositInstruction`; `repository.list_direct_deposits` ordering | `test_*::TestDirectDeposit`, `test_persistence.py::TestOrdering` | Met where documented. Priority: isolved/UKG `sequence`, PrismHR array position only. Effective dates: UKG only (PrismHR/isolved document none) |
| 5 | Deductions: setup *and* deductions recorded in payroll; codes/types, amounts or percentages, rules/dates | `DeductionSetup`; `DeductionLine` on `Paycheck` | `test_*::TestDeductionSetup`, `TestPayroll`, `test_prismhr.py::test_setup_is_not_a_paycheck_line` | Met |
| 6 | Payroll: runs/periods, paychecks, earnings, hours, gross/net, taxes, deductions, benefit contributions | `PayrollRun`, `Paycheck`, `EarningLine`, `TaxLine`, `DeductionLine`, `BenefitContributionLine`, `PaycheckPayment` | `test_*::TestPayroll` | Met where documented. UKG: no net pay, no benefit lines (documented gaps); PrismHR runs derived from `batchId` |
| 7 | Keep company ↔ employee ↔ payroll relationships | natural keys on every record; FK columns resolved in `persistence/repository.py::link_records` | `test_service.py::TestRelationshipIntegrity`, `test_persistence.py::TestRelationships` | Met |
| 8 | Preserve meaning; do not guess undocumented codes | raw-next-to-canonical rule (`*_raw`), `unmapped_code` issues | `test_*` unknown-code tests, `test_prismhr.py::test_unknown_method_is_not_guessed` | Met |
| 9 | Synthetic test data; generate and validate own fixtures; deterministic, offline, no credentials | `tests/fixtures/` (hand-written), `scripts/generate_fixtures.py` + `tests/support/schema_gen.py` (seeded, from the contracts) | `test_generated_payloads.py` (determinism, no crash, no loss, no leak), `test_contract_fields.py` | Met (the generator was **missing** before this audit; added) |
| 10 | Document gaps, assumptions, decisions with reasons | `DECISIONS.md` (short) and `docs/ENGINEERING_NOTES.md` (long), `docs/PROVIDER_MAPPINGS.md`, this file | n/a | Met |
| 11 | README: approach, how built, setup and run | `README.md` | commands in the README were executed during the audit | Met |
| 12 | Use isolved's primary snapshot only; UKG material is not OpenAPI | only `isolved-v6.swagger.json` is read | `test_contract_fields.py` | Met |
| 13 | Keep live tokens and downloaded records out of Git | `.env` and `.live/` are ignored; the live probe keeps responses in memory and prints structure only; no secret or live identifier appears in any file that would be committed (scanned in section 7); fixtures are synthetic | `.gitignore`, secret scan, `tests/test_live_probe.py` | Met (Git not initialised yet, as instructed) |
| 14 | Submit a link to your own repository | none yet | n/a | **Not done**, by instruction |
| 15 | Optional: write path to a provider | none | n/a | Not done (optional) |
| 16 | Optional: live demo data access | `scripts/live_probe.py` (read-only, host-validated, structure-only output) | `tests/test_live_probe.py`, `tests/test_*_live_shapes.py`; results in section 6 | Met, beyond the minimum (scenarios the demo tenants permit; blocked and untested areas listed) |

## 2. Defects found and fixed by this audit

Found by the sentinel test, the generator, or the end-to-end runs:

| Defect | Where | Fix |
|---|---|---|
| **Secret exposure:** PrismHR `hsa.hsaDepositSettings[].accountNum` (unmapped) was stored in full inside `extras` | `adapters/base.py` | `scrub()` replaces sensitive-named keys (SSNs, account numbers, passwords, tokens, PINs…) with `[redacted]` in every leftover and lists the path in `redacted_paths` |
| PrismHR `clientMaster.status` dropped whenever `statusClass` was present | `adapters/prismhr.py` | `status` → `status_raw`; the class is encoded by the enum |
| Unmapped PrismHR `statusClass` (company and employee) lost | `adapters/prismhr.py` | kept in `extras` |
| PrismHR employee-level `achStatus` / `voucherType` lost when an employee had no ACH rows | `adapters/prismhr.py` | stay in employee `extras` |
| PrismHR voucher `earnAmount` and a differing `checkNum` dropped | `adapters/prismhr.py` | kept in `extras` (differing `checkNum` also warns) |
| PrismHR deposit `amount` dropped for an unknown `method` | `adapters/prismhr.py` | kept in `extras` |
| UKG house number (`number`, `number_addition`) lost when `address_line_1` was set | `adapters/ukg_ready.py`, `Address.house_number` | new field |
| UKG managers beyond the first lost | `adapters/ukg_ready.py` | `extras["other_managers"]` |
| UKG compensation `amount` lost when `hourly_pay` was also present or the period was unknown | `adapters/ukg_ready.py` | kept in `extras`; mismatching `employee.account_id` now warns |
| UKG national-ID `type` lost | `adapters/ukg_ready.py` | kept (the value stays withheld) |
| isolved override address not used for paychecks lost its flag | `adapters/isolved.py` | left whole in `extras` |
| isolved "Net Pay" deposit (`sequenceNumber 0`) listed first | `persistence/repository.py` | remaining-balance deposits sort last |
| Fixture generation from the contracts (a starter requirement) missing | `scripts/generate_fixtures.py` | restored, seeded, byte-identical across runs |

Each fix has a test in `tests/test_audit_regressions.py`, `test_persistence.py` or `test_generated_payloads.py`;
`test_no_data_loss.py` stays as a permanent guard.

## 3. Evidence from the three end-to-end runs

Each run: `uvicorn` on a clean database, `POST /ingest` for company, employee, direct deposit,
deduction and payroll (tenant A), the same again (idempotency), the same under tenant B with one
changed name, then `GET` reads and direct SQL.

| Check | PrismHR | isolved | UKG Ready |
|---|---|---|---|
| Records stored (A) | 1 company, 2 employees, 3 deposits, 5 deductions, 1 run, 2 paychecks | 1, 2, 3, 2, 1, 1 | 1, 1, 4, 2, 1, 2 |
| Re-ingest of identical payloads | all `unchanged` | all `unchanged` | all `unchanged` |
| Tenant B changed name visible only to B | yes | yes | yes |
| Unknown tenant sees data | `[]` / 404 | `[]` / 404 | `[]` / 404 |
| Rows per table (both tenants) | exactly 2× tenant A | 2× | 2× |
| Cross-tenant foreign keys | 0 | 0 | 0 |
| Unlinked rows | 0 | 0 | 2 paychecks: statement 990002 names employee 7770002, which is not in the fixture (correct: not invented) |
| Source secrets (SSN, full account numbers) in DB dump / API responses / server log | none / none / none | none / none / none | none / none / none |
| `extras` returned by any GET | always `{}` | `{}` | `{}` |
| Decimal scale in PostgreSQL | `100000.00`, `80.00` kept | `40040.00`, `40.00` kept | n/a (hourly only) |

Mapping spot checks against the contracts (all as documented): PrismHR `usePercent` decides amount vs
percent; method `B` → remaining balance; voucher `type R/S` → regular/supplemental; employer and
employee taxes split into `liability`; isolved `isEmployeeTax` missing → `liability` null, `isMemoEarning`
kept; UKG `FLAT_AMOUNT` → fixed, `GROSS_EARNING`/`NET_PAY` → percentage with basis, `FULL` left raw,
EE/ER tax amounts become two lines.

How much lands in dedicated canonical fields versus `extras` (first audit, contract-generated payloads
that fill every documented field): employee 14 % PrismHR, 20 % isolved, 30 % UKG; payroll 28–35 %;
company 7 %. Nothing is lost; the remainder is verbatim in `extras`. **Employee figures were
improved afterwards: see §5.**

## 4. Remaining limitations (not fixed, deliberately)

1. **Live validation was added later** (section 6): the adapters were run against the Tada demo API for all three providers.
   Ambiguous PrismHR fields (`earning[].payAmount`, `unitRate`, `totalEarnings` as gross) still follow the field
   descriptions and are flagged in ENGINEERING_NOTES.md; the live data did not contradict them.
2. **"All available employee fields"** is met by typed attributes for the documented fields with clear
   meaning (§5) and by lossless preservation for the rest: 53-64 % of the documented employee values are
   still only in `extras`, mostly provider workflow/UI metadata, health and skills records, document
   metadata and protected demographics that are deliberately not promoted.
3. **Provider gaps** (documented, not invented): UKG has no company master, net pay, check number or
   benefit lines; PrismHR has no payroll-run object, termination date or deposit effective dates;
   isolved statuses, account types and pay types are free text, and a check does not name its run.
4. Referential integrity is **eventual** (nullable surrogate FKs filled in at later ingests) so data can
   arrive in any order.
5. The API has **no authentication**; it also returns names and birth dates. Local use only. Account numbers are masked
   and the salted account hash is internal (never returned, section 7).
6. Redaction of unmapped fields is a recognition policy, not a guarantee (`src/normalization/sensitive.py`): keys are
   judged on their WORDS (`taxpayerNumber`, `socialSecurityNo`, `bankAccountNumber`, `apiToken`, `password`, ...), an
   object labelled as sensitive (`{"type": "SSN", "value": ...}`) has its value redacted, and any string holding an
   SSN-formatted number (`###-##-####`) is redacted under any key. A secret under a generic, unlabelled name with no
   SSN formatting (e.g. an unformatted account number under `"detail"`) cannot be recognised and would be kept in
   `extras`. Stored read endpoints never return `extras`.
7. No deletes: a record that disappears from a provider stays in the database.
8. Not built: the optional write path. Still to do by the author: create the repository and submit its link.

## 5. Follow-up: employee mapping coverage

Goal: a meaningful improvement, not a bigger percentage. Method: classify every employee field that was
only in `extras`, promote the ones with documented, useful, cross-checkable meaning, keep the rest in
(redacted) `extras`, and re-measure with `scripts/coverage_report.py` (same generator and seed as §3).

### Measurement (seed 20260101; `uv run python scripts/coverage_report.py`)

| Provider | Documented leaf values | Audit formula, before → after | Strict canonical share (after) | Withheld | Still only in `extras` |
|---|---|---|---|---|---|
| PrismHR | 353 | 14 % → **28 %** | **38 %** (129) | 4 | 204 |
| isolved | 336 | 20 % → **46 %** | **47 %** (153) | 9 | 163 |
| UKG Ready | 152 | 30 % → **38 %** | **46 %** (71) | 13 | 68 |

* *Audit formula* = the formula of §3 (all leaves, minus extras, minus withheld); it is the like-for-like
  comparison with the earlier figures.
* *Strict* excludes subtrees owned by other entity types (direct-deposit rows, scheduled deductions) and
  counts a field as canonical if it leaves `extras` when it holds a typed value. The generator fills
  plain-string fields with placeholders such as `birthDate-12`, which the adapters correctly refuse to
  parse, so a probe retries each remaining field with a date, decimal, Y/N flag or integer. This
  step is what separates "mapped, but the placeholder is invalid" from "not mapped".
* Canonical share is the number that matters; **raw preservation is not counted as coverage**.

### Second step (final correctness pass): before → after, same generator, seed and probe

| Provider | Canonical share before → after | Typed leaves | Withheld | Still only in `extras` |
|---|---|---|---|---|
| PrismHR | 38 % → **41 %** | 129 → 140 | 6 | 202 → 191 |
| isolved | 47 % → **47 %** | 153 → 155 | 9 | 163 → 161 |
| UKG Ready | 46 % → **48 %** | 71 → 73 | 14 | 67 → 65 |

Silently dropped documented values: **0** (112 independent generated payloads, 8 seeds x 14 cases, every leaf found in a typed field, in `extras` or withheld).
The withheld counts rose slightly (4/13 earlier) because the word-based sensitive-data policy recognises more key names. The denominator is unchanged.
Remaining `extras` are provider-specific (health, skills, vehicles, property, workflow metadata), protected (ethnicity, disability, veteran, citizenship),
documents, or fields whose meaning the contract does not state. See ENGINEERING_NOTES.md section 14.

### Classification of what was unmapped, and what happened to it

| Business meaning | Promoted to structured attributes | Kept in `extras` (reason) |
|---|---|---|
| Personal | `preferred_pronouns`, `preferred_language`, `deceased_date` | ethnicity, disability, veteran, citizenship, tobacco use, marital date: **protected / sensitive classes**, deliberately not promoted so they never reach the read API |
| Contacts | `emergency_contacts[]` (PrismHR), `secondary_email`, `fax`, `preferred_phone` (UKG documented enum) | phone country codes, benefit-enrollment e-mail |
| Addresses | `mailing_address` (PrismHR alternate address; isolved override incl. foreign/military variants and the copy inside `nameAddress`), `tax_form_address` (PrismHR W-2 address; isolved override flagged for tax forms), `tax_geo_code`, `school_district_code` | isolved update-only flags (`deleteAddress`) |
| Employment | termination type/explanation, `last_worked_date`, `leave_return_date`, `eligible_for_rehire`, `peo_start_date`, `retirement_date`, `job_effective_date`, `job_group`, `soc_code`, `eeo_job_category_raw`, `full_time_equivalent`, `is_contractor/officer/business_owner/manager/supervisor/remote_worker/seasonal/union_member`, `union_code_raw`, `workers_comp_code`, `timeclock_id` | probation/review dates (meaning ambiguous), PEO-only flags, transfer history, `updated*` update-only inputs |
| Organization | isolved `team`, UKG `pay_grade` (as `org_assignments`); `labor_allocations[]` (PrismHR percentage split by location/department/job/project) | team calendar rules, custom misc fields, cost-center limits, GL codes |
| Compensation | `pay_frequency_raw`, `periods_per_year`, `standard_hours_per_day` (UKG ms → hours, flagged derived), last change date/amount/percent, `per_diem_amount`, `alternate_rates[]`; second step: last pay date, review dates, compa-ratio, last performance review, UKG standard hours per week | quartile (undocumented), auto-pay codes, hourly-rate-basis variants |
| Tax-related | `tax_withholdings[]` (federal / state / local election: filing status, allowances, exemptions, extra withholding, overrides, multiple-jobs, blocked, certificate/form filed) from PrismHR and isolved, `fica_exempt`, `non_resident_alien` | IRS lock-ins, W-5/EIC details, local certificate extras, UKG tax info (separate endpoint, not read) |
| Benefits | `benefits` profile: benefit groups, UKG medical/dental/dependent/auto-enrollment eligibility, enrollment status, cafeteria contributions, benefit salary, court-ordered medical | HSA deposit settings (bank data), ACA status text, plan-level detail |
| Provider metadata | none | checksums, UI/sync flags, onboarding workflow, attachments and I-9 metadata, health tests, skills/education, vehicles, company property |

Nothing was inferred: each promoted field has a description in the contract, undocumented code values stay
in `*_raw`, and a value that cannot be parsed stays in `extras` with a warning.

### Security consequence found on the way

isolved `profilePicture`, `attachments[].documentContent`, `i9Documents.*.documentContent` and
`documentNumber` (identity-document numbers and file bodies) were sitting in `extras`. They are now
redacted by the sensitive-data policy (`sensitive.py`). Regression test: `tests/test_employee_coverage.py`.

### Verification of this change

* `tests/test_employee_coverage.py` (26 tests): every promoted field per provider, flag/override
  edge cases, blank-vs-false rehire flag, conflicting pronouns, unsupported groups are empty rather
  than invented.
* `test_no_data_loss.py`, `test_generated_payloads.py`, `test_contract_fields.py` re-run unchanged in
  principle: nothing was lost, no secret appeared in any output, all new fixture fields exist in the contracts.
* `tests/test_persistence.py::TestEmployeeCoverageRoundTrip`: all three providers' employees come back from
  PostgreSQL identical; nested Decimals keep their scale; a changed nested value is an update, not a duplicate.
* Alembic `0002` is additive; verified downgrade/upgrade on a populated database (rows kept, JSON
  lists default to `[]`). The test database is now rebuilt from scratch each session.
* The three end-to-end runs of §3 were repeated: idempotent, tenant-isolated, no secrets in DB, API or logs.

### Remaining limitations

1. Roughly half of the documented employee values remain `extras`-only (see the table above); most of
   it is metadata with no stable cross-provider meaning, plus the protected demographics decision. That is a
   judgement, not an oversight, and can be revisited per field.
2. `tax_withholdings` exposes withholding elections on the read API (same sensitivity class as
   compensation, which was already exposed). The API remains unauthenticated, local-only.
3. PrismHR federal `fedFilingStatus`, isolved `filingStatus` and similar codes are kept raw; the
   references do not define their full code tables.
4. UKG employee tax info and emergency contacts are separate endpoints that this project does not read.
5. Live provider responses were checked afterwards (section 6); the employee mapping itself was not changed by that.

## 6. Live API validation (added after the build)

A read-only development tool (`scripts/live_probe.py`) signed in through Tada's sign-in service for each provider and called
documented GET endpoints on the returned host, validating the host first, refusing redirects and writing nothing to the database.
Responses were normalized in memory and only structure (key names, types, counts, issue codes) was printed. Details and every
decision: ENGINEERING_NOTES.md section 12.

| Provider | Verified live | Differences found and fixed | Blocked or untested |
|---|---|---|---|
| PrismHR | company, employee (person-only and full `options`), direct deposits, empty and non-empty deductions, payroll vouchers | list key `payrollVoucher` (singular); empty deduction list; person-only employee detection; repeated `employeeId` parameter for several ids | `getPayrollVouchersForEmployee`, payroll with `options`, pagination |
| isolved | companies, employees, per-employee direct deposits, run history, paged checks (21 paychecks) | paged `{results: [...]}` envelope for checks; empty page reported as empty | **deductions: 401** (provider permissions), legal-level checks and `checks/years`: 404, **paycheck-to-run link unresolved by design**, pagination |
| UKG Ready | EIN company, 103 employees, employee bundles assembled from separate sections, direct deposits, 49 payroll runs, pay statements (single and assembled) | a pay statement's `employee` reference no longer looks like an employee bundle; empty `deposits` / `pay_statements` lists | **deductions: 404** (endpoint not served), statements beyond the first payroll that has any, net pay (not exposed), pagination |

All three providers return real-looking values that the contracts say are masked or that the adapters must withhold: isolved and
UKG Ready return account numbers and SSNs **unmasked**; none reached any record, extra, provenance, trace, issue text, `repr`,
HTTP response or captured log (checked over 30 and 12 live rows respectively, plus the leak checks in `tests/test_*_live_shapes.py`).

## 7. Final submission audit: findings and resolutions

Method: every requirement re-read against the code and tests; a clean copy built from the README alone (`uv sync`, `scripts/setup_env.py`,
a fresh database, `alembic upgrade head`, full backend suite, backend started and exercised with the README `curl`, `npm ci` and
`npm run check`); a scan of every non-ignored file for the real API key, the real salt, token and key patterns, and live identifiers
(none found outside the ignored `.env`); review of logging, defaults and hygiene.

| # | Finding | Resolution |
|---|---|---|
| 1 | The decisions file still said live calls were not built | rewritten (now sections 1, 10, 12 of `ENGINEERING_NOTES.md`) |
| 2 | this file was stale (no live validation, 371 tests) | updated (header, rows 13 and 16, section 4, sections 6-7) |
| 3 | `docs/RELEASE_CHECKLIST.md` carried contradictory historical counts | rewritten as one current document |
| 4 | `account_number_hash` was returned by `/normalize`, `/convert` and the stored read endpoints | now internal: `Field(exclude=True, repr=False)`, still stored and still part of the content hash |
| 4b | found while fixing 4: PrismHR deposit `source_id` embedded 16 hex characters of the hash (a partial leak in every response, and ids changed on salt rotation) | `source_id` is now `routing-last4-type-method-override`, independent of the salt |
| 5 | `check-inputs` exits 2 with messages and nothing in the README said that is expected | documented in the README |
| 6 | README did not separate typed mapping from preserved `extras` | added: 38 % / 47 % / 46 % typed, remainder preserved verbatim, preserved is not counted as mapped |

Snapshot changes made deliberately by finding 4: the four direct-deposit snapshots lost their `account_number_hash` lines (11 values, nothing
else changed) and `prismhr-direct-deposit.json` has new `source_id` values (every other field identical); the other 13 snapshots are
byte-identical. Tests: `tests/test_hash_not_exposed.py` (18).

