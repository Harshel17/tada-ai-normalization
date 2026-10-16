# Engineering notes

This is the long form of [DECISIONS.md](../DECISIONS.md): the detailed reasoning, implementation notes, live-validation findings and
historical changes behind each decision. DECISIONS.md is the short version meant to be read first. Section numbers here are the original
ones; code comments and other documents that cite "DECISIONS.md section N" mean section N of this file. Per-field mappings are in
[PROVIDER_MAPPINGS.md](PROVIDER_MAPPINGS.md); audit results are in [AUDIT.md](AUDIT.md).

## 1. What was built

All four required data types for all three providers, plus the company link:

| Assignment data type | Canonical records | Entity type (CLI/API) |
| --- | --- | --- |
| Company | `Company` | `company` |
| Employee | `Employee` (+ nested `Compensation`) | `employee` |
| Direct deposits | `DirectDepositInstruction` | `direct_deposit` |
| Deductions (setup) | `DeductionSetup` | `deduction` |
| Payroll | `PayrollRun`, `Paycheck` with `EarningLine`, `TaxLine`, `DeductionLine`, `BenefitContributionLine`, `PaycheckPayment` | `payroll` |

The starter exposed three entities (`company`, `employee`, `payroll`) and a `normalize(entity, response,
reference)` adapter signature. I kept the three provider modules and the `normalization` package, and
added `direct_deposit` and `deduction` as their own entities because the four data types were asked
for explicitly. The adapters take an extra `context` argument (see section 4).

Not built: the optional "write canonical data to a provider" extension. The normalizer itself never calls a
provider: the core engine, CLI and API work on responses you give them, and the test suite runs offline on synthetic
fixtures. After the build was complete I validated the adapters against the **live Tada demo API for all three
providers** with a separate, read-only development tool (`scripts/live_probe.py`, in memory, no database writes). What was
verified, what changed because of it and what is still untested (deductions on isolved and UKG Ready, pagination, ...) is in
section 12 ("Live PrismHR / isolved / UKG Ready validation"); the permanent regression tests use sanitized structural copies
of those responses (`tests/fixtures/live_shapes/`).

## 2. Architecture

```
canonical/   models + enums          no imports from the rest of the app
adapters/    one module per provider  depend only on canonical/ and adapters/base.py
service.py   dispatch + error shape   depends on adapters
persistence/ SQLAlchemy + repository  depends on canonical/ (reads the result of service)
api/, cli.py                           depend on service (+ persistence for ingest/reads)
```

`service.normalize` never raises for bad *data*: it returns a `NormalizationResult` whose `issues` carry
a severity, a code and a path (never a source value, so issues are safe to log). It raises only for a
caller bug (unknown provider/entity). An `ERROR` issue means a record was skipped or the input was
unusable; the good records in the same payload are still returned.

Adapters are deliberately plain, explicit, line-per-field code. The only helper machinery is
`Src` (`adapters/base.py`): a wrapper that remembers which keys were read, so that **every unread key
is returned as `extras`**. That gives "do not silently discard unknown fields" without a mapping DSL.

## 3. Canonical model rules

1. **Identity.** Every record carries `provider`, `tenant_id`, `company_id`, `source_id`. `tenant_id` is
   supplied by the caller (provider payloads never contain it). `company_id` is the PrismHR `clientId`,
   the isolved **legal** company id, or the UKG company id. These four fields are the database
   uniqueness key, so providers, tenants and companies cannot overwrite each other.
2. **Raw next to canonical.** A canonical enum member exists only when a provider document gives that
   code a meaning. Otherwise the canonical field is `null` and the source value sits in `*_raw`, with an
   `unmapped_code` info issue. Example: PrismHR `statusClass` A/L/T → `EmploymentStatus`; isolved
   `employmentStatus` is free text with no documented values, so it is only `employment_status_raw`.
   I did not map the PrismHR deduction-type table (`V`, `C`, `G`, …) because the lookup file documents
   it for `DeductionCodeDetail.deductionType`, while the fields I read (`getDeductions.deductionType`,
   voucher `deductType`) are only described as "defined by the service provider".
3. **null / empty / zero.** `""` and whitespace become `null` for text and dates. `0` and `0.00` are
   values and stay `0`. A field that is absent stays `null`; we never default it to zero. An
   unparseable number/date becomes `null`, produces a warning (without echoing the value), and the
   original value stays in `extras`.
4. **Decimals.** All money is `Decimal`, never float. JSON is parsed with `parse_float=Decimal` (CLI and
   API) so `100000.00` keeps its scale, and PostgreSQL columns are unconstrained `NUMERIC`, which also
   keeps scale. Serialized as strings.
5. **Dates.** `date` for calendar dates (isolved's `...T00:00:00` datetimes are reduced to the date),
   no timezone guessing.
6. **Setup versus actuals, never mixed.**
   * `Compensation` (on `Employee`) is pay *setup*; `Paycheck.gross_pay/net_pay` and lines are what a
     check *actually* paid.
   * `DirectDepositInstruction` / `DeductionSetup` are standing configuration; `PaycheckPayment` /
     `DeductionLine` are what happened on one check. They are different tables and different types.
   * `Paycheck.pay_date` (when paid) and `period_start/period_end` (the work period) are separate
     fields everywhere, including on `PayrollRun`.
7. **Relationships.** Children store natural keys (`company_id`, `employee_id`, `run_id`). Paychecks
   point at employees and runs; deposits/deductions point at employees; every record points at its
   company.

## 4. Input conventions (and why)

Provider responses omit ids that live in URLs, and UKG spreads one person across several endpoints, so
the input is the **documented response body, unchanged**, plus a small `context`:

| Context field | Needed when |
| --- | --- |
| `tenant_id` | always |
| `company_id` | all but PrismHR/isolved `company` (their body names the company) |
| `parent_company_id` | isolved client id, optional (otherwise taken from `legal.client.id`) |
| `employee_id` | employee-scoped responses that do not repeat it (PrismHR `getDeductions`, isolved/UKG deposit and deduction lists) |
| `payroll_id` | a lone UKG pay statement |

Two *bundle* conventions of mine, because I cannot call the APIs: UKG employee
`{"employee", "demographics", "pay_info", "compensation_history"}` and payroll
`{"payroll", "pay_statements"}`, and isolved payroll `{"payroll_run", "checks"}`. Each part is an
unmodified provider response; a lone profile / pay statement / check also works.

## 5. Provider-specific decisions and gaps

* **PrismHR.** One `getEmployee` response feeds three entities (employee, direct deposit,
  `scheduledDeduction`); the sections owned by another entity are not reported as unmapped.
  `getDeductions` (recurring) and `scheduledDeduction` (one-time/temporary) both become `DeductionSetup`,
  told apart by `setup_kind`; `usePercent` decides whether `amount` is a dollar value or a percent.
  Vouchers have no payroll-run object, so a `PayrollRun` is **derived** per `batchId`; its dates are kept
  only if all vouchers in the batch agree (flagged in `provenance.derived_fields`).
  *Ambiguity:* `earning[].payAmount` is described as "pay per hour or unit" (sounds like a rate) and
  `unitRate` has no description. I mapped `payAmount` → line `amount` and `unitRate` → `rate`, and added
  a check that warns when `sum(payAmount)` disagrees with the voucher's `earnAmount`.
  `benefit[]` overlaps `deduction[]` (the same plan money from the benefits side), so benefit lines are
  informational and must not be added to deductions.
  `totalEarnings` is treated as gross pay (documented as "total earnings during the pay period").
  There is no explicit termination date; `statusDate` with class `T` is the closest, and I did not
  promote it.
* **isolved.** Company = **legal** company, with the client as `parent_company_id`. Almost everything is
  free-text description rather than code (account type, deposit status, employment status, pay type), so
  only self-explanatory labels are mapped (`Checking`/`Savings`, `Male`/`Female`); everything else is raw.
  Remaining-balance deposits sort last when listing. Direct deposit `sequenceNumber 0` is documented as "Net Pay" → `remaining_balance` (flagged derived).
  Deduction `calcParams[].paramType` meanings are undocumented, so values stay in `calc_params` and
  `amount`/`percentage` are `null`. A check carries a snapshot of the employee (name, SSN, address);
  I withhold it (listed under `redacted_paths`) because it belongs to the `Employee` record and is
  sensitive. `taxes[].isEmployeeTax` missing → `liability` is `null`.
* **UKG Ready.** There is no company-master endpoint, so `Company` holds only the company id and its
  EINs as `legal_entities`; name/status/address are `null` (an info issue says so). A pay statement
  documents no gross, net or check amount: gross is **derived** as the sum of earning lines (flagged),
  net is left `null`, and `payments[]` (undocumented meaning) stays in `extras`. A tax line with both an
  employee and an employer amount becomes two `TaxLine`s. UKG documents no benefit lines on a pay
  statement, so `benefits` is empty rather than guessed from deductions. For compensation I use the
  record with the latest `effective_from` and keep the older ones in `extras`.
  `calc_method.type`: the create docs say `FLAT_AMOUNT` needs `amount` and `GROSS_EARNING`,
  `NET_PAY`, `DIRECT_DEPOSIT_PER_OF_REMAINDER` need `percentage`; I use that. `FULL` is not described, so
  it stays raw.
* **Employee coverage.** Documented employee fields with a clear, useful meaning are promoted to typed
  attributes or nested models (emergency contacts, tax withholding elections, benefit profile, labor
  allocations, alternate pay rates, extra addresses, employment flags and dates). Strict canonical share is
  now 41 % (PrismHR), 47 % (isolved), 48 % (UKG) of the documented employee values (38/47/46 before section 14); see
  [AUDIT.md](AUDIT.md) section 5 for method, classification and what stays in `extras`.
* **Protected demographics are not promoted.** Ethnicity, disability, veteran status, citizenship and
  tobacco use stay in `extras` (which the read API never returns). A typed field would put them on an
  unauthenticated API for no stated assignment need.
* **Navigation links** (`links`, `_links`) are HATEOAS URLs, not data, and are intentionally dropped.
* **Gender.** Only `male`/`female` are canonical (PrismHR `M`/`F`, UKG `MALE`/`FEMALE`). PrismHR `X`/`D` and
  UKG `UNDEFINED` have no documented meaning in the references, so they stay raw.

## 6. Sensitive data

* SSNs are reduced to `ssn_last4`; full values are never stored, returned or logged.
* Bank account numbers become `account_number_masked` (`****1234`; nothing is shown for ≤4 characters)
  and a salted SHA-256 `account_number_hash`, which lets us recognise the same account without storing
  it. The routing number is kept (it identifies a bank, not a person). Consequence: the stored data
  cannot be used to *write* a deposit back to a provider; a write path would have to re-fetch.
* **The hash is internal** (decided at the final audit): it stays on the canonical model and in the database
  (`direct_deposit_instructions.account_number_hash`) but is declared `Field(exclude=True, repr=False)`, so no serializer
  can emit it: not `/normalize`, `/convert`, the stored read endpoints, error responses, the OpenAPI schema, the CLI output
  file, traces, issue text or `repr`. Persistence reads the attribute directly, and the content hash used for idempotency
  still includes it. Reason: with short account numbers a hash is only as strong as the salt, so it should not leave the
  process at all. `tests/test_hash_not_exposed.py` covers every path. The four deposit snapshots lost their
  `account_number_hash` lines deliberately (11 values; nothing else in them changed).
* **Record ids never embed account data.** PrismHR gives an ACH voucher no id, and the adapter used to build `source_id`
  from the first 16 hex characters of the account hash, which leaked a hash prefix through every API response and made
  the id change whenever the salt was rotated. It is now `routing-last4-accountType-method-override` (values the record
  already shows; exact duplicates still get `#2`, `#3`), so it leaks nothing and is independent of the salt. Only
  `prismhr-direct-deposit.json` changed (its `source_id` values). Rows stored under the old ids (the 3 synthetic local demo
  rows) would be inserted again under the new ids on re-ingest; nothing was deleted or migrated.
* Every withheld field is listed in `provenance.redacted_paths` (path only).
* Safety net for fields nobody mapped: unread source fields are kept in `extras`, so one centralized policy
  (`src/normalization/sensitive.py`, applied to every record's extras and line-item extras, nested objects and arrays included)
  replaces sensitive content by `[redacted]` and lists its path in `provenance.redacted_paths`. Three layers: (1) a key is
  judged on its WORDS, not its spelling (`taxpayerNumber`, `socialSecurityNo`, `bankAccountNumber`, `apiToken`, `password`,
  `accessKey`, ... are sensitive; `businessName`/`className` are not although they contain the letters "ssn"); (2) in an object
  labelled as sensitive (`{"type": "SSN", "value": ...}`) the value-carrying keys are redacted; (3) a string holding an
  SSN-formatted number is redacted under any key. Empty values and booleans are left alone; a bare `bankAccount` is hidden only
  when its value looks like an account number (4+ digits), so a short code such as `OPS-1` is preserved. Safe unknown fields
  are preserved untouched. The first version of this net closed a real leak (PrismHR HSA deposit account numbers); the policy
  was generalized after an independent evaluation showed `taxpayerNumber`, `bankAccountNumber` and `apiToken` surviving.
  **Remaining limit:** a secret under a generic, unlabelled key name with no SSN formatting cannot be recognised.
* Input hygiene: a NUL character or a lone surrogate anywhere in a payload or context is refused with an `invalid_text`
  error (HTTP 422, location only, never the text) before any adapter, serialization or database write. A provider field of
  the wrong type is a controlled `unexpected_type` / `unsupported_structure` error, never an uncaught exception (HTTP 500).
* `ssn_last4` is derived only from a nine-digit SSN or a digit-masked one (`XXX-XX-1234`); anything else gives null.
* Issue messages and logs never contain source values; the API logs counts, never payloads.
* Stored records are returned by the API without `extras`. `birth_date`, names, home address, emergency
  contacts (third-party phone numbers), withholding elections and compensation *are* returned, so the read API
  is not safe to expose without authentication, which is not implemented (local only).
* `.env` is git-ignored; `.env.example` has no real secret; PostgreSQL is bound to `127.0.0.1`.

## 7. Persistence and idempotency

* Tables mirror the canonical model. Every canonical field is a column; JSONB is used only for
  `extras`, `provenance` and nested values nobody queries (addresses, org assignments, calc params,
  line accumulators). Enum columns have `CHECK` constraints; `provider` is checked too.
* **Uniqueness:** `(provider, tenant_id, company_id, source_id)`; deposits and deductions add
  `employee_id`. Lines are unique per `(paycheck, line_no)`.
* **Idempotency:** one `INSERT ... ON CONFLICT DO UPDATE ... WHERE content_hash <> new`. Same hash →
  nothing written (`unchanged`); different → full replace and `version + 1` (`updated`); new → `inserted`.
  The hash covers the whole record **including `provenance`**, so idempotency is per source location: the same payload twice is `unchanged`, but the same logical
  record arriving from a different file or path (e.g. isolved deposit 301 as a bare list and embedded in an employee) is one row that is `updated` each time the
  source changes (`version` + 1, `provenance` last-write-wins). Whether provenance-only changes should be versions is an open decision (see the end of this section).
  Concurrent identical ingests are safe because the database enforces the key (tested with 6 threads).
  Updates *replace*, so a field that disappears at the source becomes `null` instead of lingering.
  Paycheck lines are deleted and re-inserted with their paycheck, only when it changed.
* **Atomicity:** one ingest call = one transaction; a result with any `ERROR` issue is rejected before
  anything is written (no half-loaded payloads). `ingest_runs` records each successful call.
* **Out-of-order arrival:** a deposit/paycheck may arrive before its employee/run. Natural-key columns
  are always stored; the surrogate foreign key (`employee_pk`, `run_pk`, `company_pk`) is `NULL` until the
  parent exists and is filled in at the end of any later ingest for that provider+tenant. Links never
  cross tenants (tested). Deleting a parent sets the child's FK to `NULL`, not cascade-deleting history.
  The trade-off versus hard `NOT NULL` foreign keys is that referential integrity is "eventual".
* No deletes: records that disappear from a provider are not removed (a payload is a snapshot of
  *some* records, not necessarily all).

* **Open decision: provenance-only changes (investigated, not changed).** `content_hash` includes `provenance`, which is stored on the row last-write-wins
  and also feeds `version`. Findings: (1) provenance says *where* a record was read, not what it says, so a provenance-only change is not a business change;
  (2) hashing only the business content would make cross-file re-saves `unchanged`, but the stored hashes of existing rows were computed with provenance, so
  the first re-ingest of each row would be a one-time spurious `updated`, and the stored provenance would have to be refreshed by a separate rule;
  (3) provenance is the only place that records the source path, so it must not simply be dropped; `ingest_runs` keeps per-call counts and a payload hash but not
  per-record source locations; (4) tracking every occurrence of a record (a source-occurrence table) is the complete answer and needs a schema change.
  Recommendation: keep the current behavior and wording for now (documented above); if approved, the smallest safe change is to exclude `provenance` from the hash
  and refresh it without bumping `version`, accepting one spurious update per existing row. Nothing in hashing, identity, schema or version semantics was changed.

## 8. API and CLI decisions

* The API is thin: parse → `service` → shape the response. It reads the raw request body so numbers
  stay `Decimal` (FastAPI's own JSON parsing would make floats); the OpenAPI doc is patched to still
  show the request schema.
* `422` is used for "your payload had errors", with the full issue list (and any good records for
  `/normalize`); `404` for unknown provider/entity; `503` for database errors.
* No authentication, rate limiting or TLS; documented as local-only. Request bodies over 20 MB are refused.
* The CLI refuses to overwrite an output file without `--force` (kept from the starter) and exits `1`
  when the payload had errors but still writes the output so the issues can be read.

## 9. Testing approach

* Fixtures are **hand-written** in the documented response shapes (the assignment says not to treat
  vendor samples as contracts), plus a **seeded generator** (`scripts/generate_fixtures.py`) that fills every
  documented field of the contract response schemas. Generated payloads are stress inputs: they must not
  crash, lose a value or leak a secret (`test_generated_payloads.py`). `test_no_data_loss.py` does the same
  for the realistic fixtures by swapping each value for a marker. `test_contract_fields.py` checks every fixture key exists in the
  contract, which already caught an invented field while I was writing them.
* Coverage by theme (see the test names): mappings per provider/entity, nested and repeated records,
  multiple deposits, setup vs actual deductions, earnings/taxes/benefits/net pay, missing/null/empty/zero,
  unknown codes, decimal precision (also through HTTP and PostgreSQL), tenant isolation (service, SQL,
  HTTP), idempotent and concurrent ingest, rollback, invalid input, raw-field preservation, sensitive
  data absence, and relationship integrity.
* I mutation-checked the riskiest logic by hand (idempotency guard, `usePercent`, zero-as-null, tenant
  in link queries); that found one gap in the link tests, now covered.
* Not covered: real provider payloads (none available offline), load/performance, migrations from a
  non-empty database.

## 10. Known limitations / what I would do next

* Live validation is done for the scenarios the demo tenants allow (section 12); still untested live: deductions on
  isolved (401) and UKG Ready (404), pagination beyond the first page, PrismHR payroll for a single employee, and
  paycheck-to-run linking on isolved (unresolved by design). The ambiguous PrismHR payroll fields above were not
  contradicted by the live data but remain interpretations of the field descriptions.
* Add authentication/authorization before any shared deployment.
* Candidate canonical enums that need tenant lookup data (employment status, pay type, deduction codes)
  would need per-tenant code tables.
* Pagination wrappers are read but only the records are used; fetching/paging provider APIs is not built.
* The optional write path (canonical → provider) is not implemented.

## 11. Optional testing console (frontend)

Added after the backend was complete, to make manual testing easier. It is **optional**: nothing in the
backend, CLI or tests depends on it, and it is not part of the assignment's required behaviour.

* **One backend addition, `GET /stats`.** The console needs to know which tenants and companies exist and what
  the database holds, and no existing route could say that (every read route needs the ids up front). `/stats`
  returns the backend's own provider/entity lists and record **counts** per provider/tenant/company, never record
  data (tested). Nothing else in the API changed.
* **Fixtures never go through the backend.** The synthetic example files are compiled into the app by Vite
  (`import.meta.glob` over the three provider folders only, each file a lazy chunk), so the example buttons work in `npm run dev` and
  in a production build with no middleware and no new file-reading API surface. `live_shapes` is never matched.
* **Numbers keep their scale.** The request body is built by inserting the payload text as written (not parsed
  and re-serialised), so `100000.00` reaches the backend, which parses it as Decimal, exactly as typed.
* **Masking is display-only.** Sensitive values are masked in every view, including the user's own input, by a
  text-level mask that leaves everything else (number scale, key order) untouched; requests carry the originals.
  Writing this exposed two real defects that tests then pinned: the browser's own `JSON.parse` error text quotes
  part of the input (so validation uses its own scanner and never echoes input), and the Monaco wrapper kept an
  old change listener that overwrote the real input with the masked text when leaving edit mode.
* **Test Runs are not simulations.** Each step performs real HTTP calls and the verdict is computed from the
  responses; later steps are *skipped* when an earlier one fails. Unit tests prove that the runner fails on a wrong
  backend (a non-idempotent save, a tenant leak, wrong counts).
* **Loopback only.** Vite binds `127.0.0.1`; the API remains unauthenticated and local.
* **Limits.** Developer tool, desktop-first; the example files are bundled into the app; it does not edit or delete
  stored data; it is verified against synthetic fixtures like the rest of the project.

### Guided experience (UX revision of the console) — superseded by section 12

The first version of the Convert page asked for implementation vocabulary (tenant, fixture, entity type, JSON)
before showing any value. It is now a guided walkthrough by default, with the original tool kept as **Advanced mode**.

* **Story first, jargon behind expanders.** Three steps (*Choose Provider → Load Example Data → Convert and Compare*), plain labels
  ("Example Data", "Convert Data", "Save to Database"), friendly example descriptions, a "What is this?" explanation,
  and an Overview box with a link into the demo. Raw JSON, provenance, issues and the request context sit in *Technical details*.
* **Ids are never guessed.** An example supplies the ids it needs from its own metadata (the same metadata the Test Runs verify),
  only the ids that kind of data needs are sent, and every id sent is shown with where it came from. For an uploaded file the page
  asks what the file contains and which ids to enter, names them in the provider's own words, and blocks Convert until they are filled.
* **The comparison is computed, not authored.** For each converted value the page searches the record's own original data. It names
  an original field only when **exactly one** place holds the same value; if several do it lists them and claims none; converted
  codes point to the original code kept beside them; calculated and user-supplied values are labelled as such. Numbers are shown as
  written (a number-preserving reader), and sensitive original values are masked. This heuristic is described on the page itself.
* **Explanations come from the response.** Counts (converted / kept as provider-specific / could not be interpreted / kept private)
  and every plain-English sentence are derived from the API response; each issue code has a fixed explanation, unknown codes fall back
  to the server's own message. Nothing is shown as a success without a response.
* **Saving is its own step.** A panel states that conversion saves nothing, stays disabled until a conversion without errors exists, and
  reports the outcome from the real `stored` counts ("2 new employee records saved", "already saved, so nothing changed").
* **Limits.** The comparison cannot name an origin for values assembled from several fields (addresses) or looked up; it says so.
  Some examples are shaped by the provider's contract only (UKG company has no name); the page states those gaps.

## 12. Detection, preview conversion and provenance (Normalize + Pipeline Explorer)

The console's main screen was redesigned: pick a provider, add JSON (or the provider's example set), let the system
detect what each file contains, normalize, then explore how each field was converted. The earlier value-matching
comparison ("which original field holds the same value?") was **retired** from this flow: equal values in different
fields made it ambiguous, so it is replaced by provenance recorded by the adapters.

**Detection (`detect.py`, `POST /detect`).**
* Deterministic rules per provider, no LLM. Wrapper keys first, then distinctive field sets; generic fields (`id`, `name`,
  `status`, `amount`) never count as evidence. Every result lists its evidence.
* Statuses: `ok`; `ambiguous` (weak evidence only: candidates are listed and the user's choice is validated against the structure
  the adapter expects, so a wrong choice is rejected rather than "converted"); `unsupported` (with a hint when another provider's
  rules match strongly; the provider is never switched automatically); `empty`.
* Counts come from the input. Sections nested in another record type (PrismHR/isolved employee files carrying deposits and
  deductions) are reported once each as `nested_in: employee`. UKG/isolved bundle wrappers (`{employee, demographics, ...}`,
  `{payroll_run, checks}`, `{payroll, pay_statements}`) are our own convention and are flagged as such.
* The backend owns the table of context ids each record type needs (it is derived from the same rules as detection).

**Preview conversion (`POST /convert`).** Detects, slices each file to the keys a unit reads, and calls the existing
`service.normalize`. **There is deliberately no save option**: saving is a separate explicit user action through the existing
`/ingest`. Missing context returns `needs_context` for that unit and normalizes nothing. Ids found in sibling files are returned
as **suggestions** the user must confirm; nothing is invented and no relationship is created silently.

**Provenance (`"trace": true`).**
* Captured by the adapters, not inferred: typed reads take an optional `to="canonical_field"`; enum lookups, derived values and
  combinations call small helpers. A pending read becomes a trace step only if its destination ended up non-empty in the built
  record; when several reads feed one destination (fallbacks) only the first one that held a value is kept, matching `a or b`.
  Source reads that are not tied to a destination are simply not traced.
* Response-only: nothing is stored, no migration. Raw source values come from the payload and canonical values from the finished
  model, never from comparing them. Sensitive source values (anything read via `secret()` or recognised by the policy in `sensitive.py`) and any
  destination containing "hash" are shown as `[withheld]`; the SSN step shows the last four digits only. Provider "extras" are not
  part of traces.
* **Coverage is incremental by decision.** Phase 1 instrumented employee identity, status (with PrismHR status/gender/pay-method and
  isolved/UKG gender enum steps), dates, job information and top-level compensation for all three providers (UKG's compensation amount is
  routed by `amount_period`; work-day hours are derived from milliseconds). **Phase A1** added every header field of the other record
  types: company, direct deposit, deduction (recurring, scheduled and configured), payroll run and paycheck header, for PrismHR, isolved and
  UKG Ready (100 % of the filled header fields on all 17 fixture runs; the tests enforce it). **Not yet instrumented:** paycheck line items
  (earnings, taxes, deductions, benefits, payments: Phase A2), and for employees addresses, tax withholdings, org/labor allocations,
  benefits and manager ids. They show as "Source mapping not available", the Pipeline Explorer names the line-item lists that are not traced
  yet, and no source path is invented.
* **Steps that are decided, not copied, say so.** Besides copy/parse/enum/withhold steps there are `route` (an `amount` becomes a
  `percentage` when `usePercent` is true; PrismHR `method` F/P decides amount vs percentage), `derive` (a deposit's allocation type from
  which of amount/percent is set; UKG gross pay as the sum of earning lines), `assigned` (a value the normalizer set itself, e.g.
  `setup_kind`: shown as "No source field", never a fake path) and `context` (an id the file does not contain, e.g. the employee id you entered:
  shown as `context.employee_id`).
* **Records built without a single source object.** UKG's company record is built directly from the EIN list: its legal entities cite
  `$.items[i].…` and its id is a `context` step (the company id you supplied), not an EIN. PrismHR's payroll runs are derived from voucher
  `batchId`s: each run date cites the real voucher paths (at most three shown, with the count) and appears only when every voucher in the batch
  agrees, exactly like the value itself. Account numbers are `[withheld]` in sources, the account hash never appears, and the browser hides any
  sensitive-named source or hash destination again even if a server ever sent one.
* **Instrumentation must be side-effect free.** `Src.sub()` / `.items()` register child objects that decide what lands in `extras`, so tracing
  code reuses the existing objects and never calls them again; a first attempt that did so was caught by the `ukg-payroll` snapshot and by the
  new test that runs every scenario with and without `trace` and requires identical output.
* A regression guard protects all of it: 17 canonical-output snapshots must stay byte-identical (`tests/snapshots/canonical/`).

**Known limitations.** Detection recognises only the response shapes the adapters accept (listed in the `unsupported` message);
file-level detection cannot tell two bare lists apart when both match weakly (it asks instead); the example buttons use files bundled into the app (dev and production alike); the Pipeline Explorer shows the most recent
conversion in memory only (a page reload clears it); saving re-sends the original file once per detected record type, which is
safe because ingest is idempotent.

### Live PrismHR validation (demo API) and what it changed

A read-only probe (`scripts/live_probe.py prism`, development tool) signed in through Tada's sign-in service, called documented GET endpoints
on the returned host and normalized the responses in memory (no database writes, no credentials or personal values in its output). The returned
host (`uatapi.prismhr.com`) is not named in the assignment materials, so the probe refuses it unless allowed explicitly with `--allow-host`.
The live responses matched our adapters closely. Differences found, and decisions:

* **`getPayrollVouchers` returns `payrollVoucher` (singular); the contract and our fixtures say `payrollVouchers`.** Both are now accepted by the
  adapter and by detection (the documented plural wins if both are present). Provenance records the key actually used. Fixture:
  `tests/fixtures/live_shapes/prismhr/payroll_voucher_singular.json` (live structure, synthetic values, masked accounts).
* **`getDeductions` can return `{"employeeDeductions": {"deduction": []}}`.** An empty list is now detected as a valid zero-record deduction response
  (it still needs `company_id` and `employee_id`) and produces no records, only the existing `no_records` warning. Rows without a `deductionCode`,
  a null list or a missing list remain "unsupported".
* **`getEmployee` without `options` returns every section name with null values** (`person: null`, `client: null`, ...). Three or more of PrismHR's
  own section names plus an id and a name are now accepted as strong evidence for an employee, with the evidence stated. A bare name, or fewer than
  three section names, remains ambiguous and asks the user. No deposits or deductions are invented from null sections.
* **Multiple employee ids are sent as repeated query parameters** (`employeeId=A&employeeId=B`), which is how the contract types the parameter
  (an array). A comma-joined value returned 404 ("Employee ID ... does not exist"). `options` is a plain string and accepted comma-separated keywords.
* **PrismHR masks PII in live responses** (SSN as `***-**-****`, deposit account numbers all asterisks), as its contract says. `ssn_last4` is therefore
  empty on live data. No raw sensitive value reached any normalized output or trace in the leak check.
* Live-only fields not in the contract (company: `eVerify`, `externalPtoUsed`, `notificationEmailList`, `payrollFinalizationNotification`,
  `positionRatesUsed`, `reverseWireAccount`, `taxesToSuppress`; voucher: `hsaPeoMatch`, `isManualCheck`; employee: `client.hawaiiMedWaiver`,
  `compensation.payPeriodsPerWeek`, `person.autoPolicy`, `person.employerId`) are kept as extras, so nothing is lost.
* **Verified live on the demo tenant (in memory):** company, employee (5 employees, 60-63 filled fields, no issues), direct deposits (employees
  with ACH rows, 1-2 records each), non-empty standalone deductions (2 and 11 records), empty deductions, and payroll (1 run, 5 paychecks).
  **Not yet verified live:** isolved and UKG Ready; PrismHR payroll with `options`, voucher pagination beyond the first page, and
  employee-level payroll (`getPayrollVouchersForEmployee`).

### Live isolved validation (demo API) and what it changed

Same read-only probe (`scripts/live_probe.py isolved`; the returned host `myisolved.com` is the Swagger host, so no allowance was needed).
Every live record type matched the isolved contract with **no fields outside it**, so the fixtures (written from the contract) were structurally right.
Differences and decisions:

* **Employee checks arrive in the paged envelope** `{"results": [...], "nextPageUrl", "previousPageUrl", "totalItems", "totalPages"}`. The payroll adapter
  now accepts it (also inside our `{payroll_run, checks}` wrapper); a bare list, a single check and the wrapper behave as before. Detection already
  recognised the shape. Fixture: `tests/fixtures/live_shapes/isolved/checks_paged.json`.
* **An employee with no checks returns an empty page** (`results: []`). Detection now reports it as `empty` ("an empty page of results") instead of
  unsupported, and the adapter produces no records (only the `no_records` warning). Only a dict whose keys are all paging keys with an empty `results`
  qualifies.
* **Paycheck-to-run relationship: unresolved, deliberately.** A live check carries `payrollRunNumber` and `payrollRunType` (documented only as
  "Payroll Run Number" / "Payroll Run Type") but no run id; run history has `id`, `runNumber` ("Run sequence number"), `payrollRunType`, period and pay
  dates. Nothing documents that the two numbers identify the same run. A read-only in-memory analysis on the demo tenant (company with data: 2 pay groups,
  231 runs, 430 checks) found that `runNumber` alone is shared by 195 runs, but `runNumber + type + period begin/end` matched exactly one run for all
  430 checks (and no two runs share that key). Caveats: all 430 checks were of one run type ("Data Conversion"), so regular payroll is untested; the match
  also needs the run history of every pay group, which `/convert` does not receive; and it is an empirical correlation, not a documented link. So the
  adapter does **not** link checks to runs: `run_id` stays empty unless the caller supplies the run in the `{payroll_run, checks}` wrapper, and
  `payrollRunNumber` / `payrollRunType` are preserved in each paycheck's extras. A linking rule would need organizer confirmation first.
* **Deductions: live validation is blocked by provider permissions.** `GET /api/corestream/.../deductions` returned 401 ("Authorization has been
  denied for this request.") for every employee in both demo companies with the token the sign-in service issued. It was not bypassed or retried with other
  credentials. The deduction adapter therefore has only synthetic-fixture coverage for isolved.
* Also unresolved on the demo: `GET .../legals/{l}/checks` and `.../checks/years` returned 404; the first company's `payrollRunHistory` returned 404 while
  the second returned 231 runs. Employee list pages were not followed past the first (50 of 118).
* **Live bank data is unmasked at the provider** (account numbers of 6-10 digits, 9-digit routing numbers). Verified in memory over 30 live deposit rows
  (25 employees): no raw account number appears in records, extras, provenance, traces, issue text, `repr`, `/normalize`, `/convert` or `/detect`
  responses, or captured logs/stdout/stderr. The record keeps `****last4`, the routing number (a bank identifier, by decision above) and the salted
  `account_number_hash`, which is internal and never returned (see section 6; at the time of this validation it was still returned, which is
  why the final audit removed it from every response). With short account numbers a hash is only as strong as the secret `ACCOUNT_HASH_SALT`,
  hence the salt hardening below.

### Live UKG Ready validation (demo API) and what it changed

Same read-only probe (`scripts/live_probe.py ukg`). The returned host `secure2.saashr.com` (path `/ta`) is the base URL used throughout the cached UKG
reference pages, so it is on the allowlist for UKG Ready only. The direct-deposit call needs the documented header `X-End-of-Support-Timeline`
(allowlisted per call; authentication headers can never be overridden). Live records matched the UKG reference schemas (the only field outside them was
`ein_name` on the employee profile). Employee profile, demographics, pay-info and compensation parts assembled in memory into our bundle shape converted
with no issues; so did the employee list (103), payroll runs (49), non-empty direct deposits and full pay statements. Differences and decisions:

* **A single pay statement carries `employee: {"account_id": ...}`**, which detection mistook for an employee bundle (so it asked, and payroll could not
  even be chosen). `employee` now counts as a bundle only when it holds a profile (a first or last name) or bundle parts are present; otherwise it is a
  reference to the owner. A pay statement is recognised by a pay date or period together with paycheck line lists (any filled, or at least two present);
  a date alone, lists alone, a payroll run or a deposit are not enough.
* **Empty `{"deposits": []}` and `{"pay_statements": []}` are valid zero-record responses** (they still need their context ids and create no records,
  only the `no_records` warning). Only a dict whose other keys are underscore/links/envelope keys qualifies; a non-list, a list of non-objects or of
  unrelated objects, or unrelated top-level keys are still rejected. Empty deduction and payroll-run lists are unchanged.
* **Pay statements do not expose net pay** on the live API (the adapter records this as an info note and does not guess it).
* **Deductions: live validation is blocked.** `GET /employees/{aid}/deductions` returned 404 "Unresolvable URL" for every employee tried, so this demo
  deployment does not serve the documented list endpoint. isolved's deductions endpoint returned 401 (provider permissions). Deduction adapters for both
  have only synthetic-fixture coverage.
* **Live SSNs are unmasked at the provider** (`social_security`, `national_id_numbers[].value`). Verified in memory over 12 profiles (108 serialized
  outputs plus captured logs): none, nor their digits-only form, reached any output.
* Not verified live: UKG deductions, pay statements beyond the first payroll that has them, and employee-list pagination (103 returned in one response).

### Account hash salt (`ACCOUNT_HASH_SALT`) hardening

The hash stored as `account_number_hash` is only as strong as its salt, and live demo providers return real-looking account numbers. Findings and changes:

* **The previous setup was unsafe:** the local `.env` held the `.env.example` placeholder, and the code fell back to a built-in default when it was unset.
* **Dependencies inspected first.** The salt is used in one place (`hash_account`). Nothing compares hashes across rows (row identity is the source id, not
  the hash). Stored hashes live in `direct_deposit_instructions.account_number_hash`; the local development database held 3 synthetic `guided-demo` rows.
  The four direct-deposit canonical snapshots contain hashes and had silently depended on the developer's local salt, so tests and the snapshot script now
  pin an explicit test-only salt (`APP_ENV=test`), which also makes them independent of the real secret. **No migration is needed and nothing was deleted.**
* **Rotation consequence (by design):** raw account numbers are never stored, so existing hashes cannot be recomputed. After a
  salt change, a stored row keeps its old hash until its source is ingested again; that shows as an *update* (a changed hash
  changes the row's content hash) and refreshes the hash, never a duplicate: record ids do not depend on the salt (see
  section 6, "Record ids never embed account data"; this was **not** true of PrismHR deposits before the final audit, and
  `tests/test_hash_not_exposed.py` now pins it for all three providers). Hashes made with different salts are not
  comparable. For the 3 local demo rows, re-ingest the example deposits (they are synthetic) or ignore them.
* **No insecure fallback:** there is no default salt. Outside `APP_ENV=test` a missing, empty, placeholder (`change-me`, `dev-only-salt`, ...), shorter than
  32 characters or low-variety salt is a `ConfigurationError` when an account number is hashed: HTTP 500 with a message that never contains the secret
  (other entities keep working) and a usage error from the CLI. `APP_ENV` defaults to `production`.
* **Development/test setup:** tests pin `APP_ENV=test` and a fixed test salt in `tests/conftest.py`. For local runs, `uv run python scripts/setup_env.py`
  creates `.env` from `.env.example`, generates a random 256-bit salt (`secrets.token_hex(32)`), writes the file owner-only (0600) and never prints the
  salt; it keeps an existing secure salt (no silent rotation) and `--check` verifies without changing anything.

### Payroll integrity checks (`integrity.py`, `/convert` with `"integrity": true`)

A pure, deterministic function over the canonical records of one conversion (no database, no network, never modifies a record). The statuses carry
meaning: **failed** only for rules that cannot legitimately be false (a record id used by different content, a paycheck naming a run that was not
converted, a period or effective range that ends before it starts); **warning** for rules that normally hold but have legitimate exceptions (a
referenced employee may be in another file, a rehire can look like a termination before hire, money totals that differ); **not_evaluated** whenever an
input or assumption is missing, always with the reason; **passed** only when every required input was present.

The rule set is what the live demo data supports. A read-only run on 839 live records (PrismHR, isolved, UKG Ready) produced no failure and, after the
two refinements below, no warning. Two candidate rules were **left out because live data showed they give false alarms**: gross - employee taxes -
deductions = net (off on 20 of 25 PrismHR vouchers because deductions mix pre-tax, benefit and employer-paid items, and off on nearly every isolved
check because those responses carry no tax lines) and check amount = net pay (a split paycheck's check is smaller than net; 16 of 25 PrismHR vouchers).
The gross-to-net bridge is listed as not evaluated, with that reason. Refinements made from the live run:
* **Money rules are provider-scoped.** Earnings = gross held on all 50 live PrismHR vouchers (payments = net on all 32 with payments); on isolved
  every comparable check differed by small amounts, so it is not evaluated there (stating the evidence) instead of flagging every paycheck; on UKG
  Ready the adapter calculates gross from the earnings, so the comparison would prove nothing, and net pay is not exposed.
* **Deposit rules are scoped by employee and voucher type.** One live PrismHR employee has a remaining-balance deposit for regular (R) and one for
  supplemental (S) pay: independent allocations, so not a duplicate. Percentages are also summed per allocation kind (P vs PN), and inactive or
  prenote deposits are ignored.
The tolerance is one cent (rounding, never a real difference). The report contains only record ids, field names, dates, amounts and sentences. The
console shows it as "Data checks" in the Normalize results; it informs and does not block saving. Not built: a stored-scope integrity endpoint and
integrity in Data Explorer (out of scope by decision).


## 13. Migration preview to UKG Ready (optional extension: dry run only)

**Scope.** The assignment's optional write path is implemented as a *dry run for one destination* and nothing more. No provider write,
no credential, no network, no database access: the mapper is a pure function (`src/normalization/migration/`), separate from the source
adapters, and the API route (`POST /migrate/preview/ukg-ready`) only returns a draft.

**Why UKG Ready.** Compared on the local contracts: UKG's create-employee schema is in the repository, strict (`additionalProperties:
false`), and requires only `username`, `first_name`, `last_name`. isolved `newEmployee` requires ten fields including the full SSN, tenant
codes (`legalCode`, `payGroup`, `workLocation`, ...) and an integer `payFrequency` with no local lookup. PrismHR's `importEmployees` + `commitEmployees`
requires eleven fields including the full SSN and tenant codes, in a two-step session flow. We never keep a full SSN, so those two
could only ever produce BLOCKED drafts.

**Assumptions and limits.**
* The UKG reference is a snapshot ("not a complete endpoint contract", no verified OpenAPI). READY therefore means "passed the
  documented local checks". Nothing was run against a UKG tenant.
* The draft is validated against the documented schema loaded from the contract file (`migration/schema_check.py`), not against our own reading of it.
* `username` is never inferred. A source username is a suggestion that needs explicit confirmation in the UI; the API only accepts what
  the operator sends.
* Source ids are not destination ids. `employee_id` (max 20) / `external_id` are sent only on explicit operator confirmation.
* Left out on purpose and reported: termination / retirement / deceased dates (UKG `status` is read-only on create), gender (a separate documented
  call), manager ids (not destination account ids), compensation, taxes, SSN last four, bank data. Over-long values are dropped, never truncated.
* Address country must be ISO alpha-3; other values are not converted (no ISO table is shipped) and the operator can supply one.
* Compensation, demographics, tax and direct-deposit writes are separate UKG resources and are out of scope.

**Product presentation (same change).** The first screen states the product in one line, offers a one-click example per provider (bundled
example files, the ids those examples were written with, then the normal `/convert` call), and summarizes the result. The summary
counts only what the API returned: typed fields that hold a value, values preserved in `extras`, references between returned records (found / pointing
outside the data), integrity totals, and protected values; it is not a coverage percentage.

## 14. Final correctness pass (duplicates, error envelopes, decimals, coverage step 2, UKG gaps, live validation)

**Duplicate records inside one payload (`src/normalization/duplicates.py`, applied in `service.normalize`, so the CLI, `/normalize`,
`/convert`, `/ingest` and the browser all behave alike).** A record's identity is what persistence keys on: provider, tenant, company and the
provider-local id, plus the owning employee for deposits and deductions (a company has no local id). Two records with the same identity and
*different* content are an ERROR (`duplicate_record_id`): the payload is rejected before anything is written (`ingest` already refuses a result with
errors, inside one transaction, so a rejected batch leaves no partial writes, not even its innocent records). Two with the *same* content are a WARNING
(`duplicate_record_identical`): both stay in the result and ingestion writes the record once (the repeat counts as `unchanged`). The same ids in different
tenants or companies, or under different employees, are not duplicates; separate requests are never compared (re-ingesting a stored record is the normal
idempotent update). PrismHR already disambiguates two identical ACH vouchers (`#2` suffix), so that adapter cannot collide on deposits. Verified:
`tests/test_duplicate_records.py` (unit, API, CLI, convert, PostgreSQL). The cross-file case (isolved deposit 301 as a bare list and inside an
employee) is two *requests* and stays an update with the explained warning from section 12.

**Provider error responses (`src/normalization/provider_errors.py`, checked before any adapter).** PrismHR documents `errorCode` ("unique error
code; "0" on success"), isolved `ValidationErrorModel` (`error`, `validationResults[].message`), UKG Ready `{"errors":[{code,message}],
"user_messages":[...]}`. A body that holds ONLY those keys is an error envelope and yields one `provider_error` issue (no records, nothing stored, no
"missing id" follow-up errors); the shown message is the provider's text, shortened, with SSN-formatted numbers and long token-like strings removed;
the payload is never echoed. A body that carries real records next to a non-empty message is still normalized (the existing
`provider_error_message` note remains). Detection names an error file for what it is. Verified for all five entities and all three providers.

**Monetary representation.** Canonical amounts, rates, percentages and hours are exact `Decimal`s in Python and PLAIN decimal text in JSON
(`models.Dec`): `1E+3` is `"1000"`, `1E-7` is `"0.0000001"`, `100.00` keeps its zeros, nothing is rounded and no binary float is involved. The database
receives the Decimal itself. Parsing rejects non-finite values and sizes outside 1e-30 to 1e31 (`1e999999999` is a valid `Decimal` that would need a
billion digits to print); a rejected value stays in `extras` with an `invalid_decimal` issue. Extras keep source numbers as received.

**Employee coverage, second step.** Inventory method: `scripts/coverage_report.py` fills every documented employee field with a unique value, normalizes it
and sorts each leaf into canonical / withheld / extras with a reason; 112 independent generated payloads (8 seeds x 14 cases) confirmed that no documented
value is ever lost. Promoted, each with a contract description: PrismHR `compensation.lastPayDate`, `compReviewNextDate`, `compaRatio`, `perfReviewLastDate`,
`perfReviewLastRating`, `perfReviewLastTitle`, `perfReviewNextDate` (into `Compensation`), `compensation.paidThruDate` (into `benefits`), `client.typeDate`,
`client.unionDate`, `client.workPhoneExt`; isolved `employmentCategoryEffectiveDate` and `acaEmploymentStatus` (shared `employee_type_effective_date`
with PrismHR `typeDate`); UKG `compensation_history.time` + `time_period` (milliseconds; WEEK and PAY_PERIOD only, derived and flagged in the trace; MONTH
and YEAR stay in extras). Coverage went from 38 / 47 / 46 % to 41 / 47 / 48 % with the same generator, seed and probe. Deliberately NOT promoted: protected
demographics and documents (privacy), `quartile` (no description in the contract), `reportsTo` ("PTO approver", not a manager), `probationDate`
(meaning unclear), pay-rate-basis variants and anything whose semantics the contract does not state. Persistence: migration 0003 adds 12 nullable columns
(`work_phone_extension`, `employee_type_effective_date`, `union_start_date`, `aca_employment_status_raw`, and `comp_*` for the compensation fields); the
benefit field lives in the existing JSONB column. Existing databases need `alembic upgrade head`; no existing row or query changes. Further promotion
(manager/supervisor display names, adjusted service date, probation dates) would need more top-level columns and was not done.

**UKG Ready reference gaps (re-examined).** `payments`: the pay-statement schema DOES document a `payments` array (`ee_amount`, `type_name`, `id`, `hours`,
`quantity`, `batch`); it was only kept in extras. Now `ee_amount` and `type_name` are typed payment lines. What a payment is stays undocumented, so no net
pay is derived and the net-pay reconciliation stays "not evaluated" for UKG. Net pay: no total in the native pay statement; `external-pay-statements` has
`total_net_pay` but is a different resource (imported statements), not used. Benefit contribution lines: none documented; employer amounts of deductions
(`deductions[].er_amount`) are already typed as `employer_amount`; `GET /benefits` lists plan enrollments (coverage level and dates), which is not a contribution
and has no canonical entity (not added). Company master: no company GET exists in the reference (only `lookup/eins`), so a UKG company stays EIN-only
(`company_master_unavailable`), never presented as complete.

**Live validation of the two blocked deduction endpoints (re-examined without new calls).** isolved: the probe's path
`GET /api/corestream/clients/{clientId}/legals/{legalId}/employees/{employeeId}/deductions` is exactly the documented one; the 401 "Authorization has been
denied" is a permission of the demo account, not a wrong path. UKG Ready: `GET /v2/companies/{cid}/employees/{aid}/deductions` is the documented path, but its
reference page is tagged `dev` (15 pages carry that tag; 209 are `v2`), which fits the 404 "Unresolvable URL" of the production-like demo. The documented UKG
direct-deposit GET is tagged "deprecated" (the probe already allows the `X-End-of-Support-Timeline` header). No endpoint was retried and no credential was used; the
adapters keep their fixture-based verification and are documented as live-unverified.
