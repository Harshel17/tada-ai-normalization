# Decisions and assumptions

This is the short version of why the project looks the way it does. I built it with an AI coding assistant (Claude Code), which the assignment allows. What I describe as verified is backed by tests or by the read-only live checks described in the engineering notes. Anything that is an assumption is labelled as one.

## Scope

- I covered all four data types (company/employee, direct deposits, deduction setup, payroll) for PrismHR, isolved and UKG Ready. The starter had three entities; I added `direct_deposit` and `deduction` as their own entities because the assignment asks for them explicitly.
- The core engine never calls a provider. It normalizes responses you hand to it, so the whole test suite runs offline on synthetic fixtures.
- I did not build real provider write-back. The optional extension is a dry run only: it turns one canonical employee into the draft body of a UKG Ready "create employee" request and sends nothing.
- After the build I checked the adapters against the live Tada demo API with a separate, read-only tool (`scripts/live_probe.py`), in memory and without database writes. The permanent tests use sanitized structural copies of those responses.

## The canonical model

- Every record carries provider, tenant, company and the provider's own id, and those four values are the database key. Providers never send a tenant id, so the caller supplies it. This keeps two providers or two companies from overwriting each other.
- A canonical enum exists only where the provider documents what a code means. Everything else stays in a `*_raw` field and the enum is null. I would rather show "unknown" than guess; isolved employment status, for example, is free text, so I keep only the raw text.
- Missing stays null and never becomes zero. A real `0` stays `0`. Money is an exact `Decimal` written as plain text, so `100.00` keeps its scale and `1E+3` becomes `1000`.
- Pay set-up (compensation, standing deposits and deductions) is kept apart from what a particular paycheck actually paid. Mixing the two would make comparisons between them meaningless.
- Employees, deposits, deductions, paychecks and runs point to each other by natural keys. In the database those links are filled in once both sides exist, so records can arrive in any order. The cost is that referential integrity is eventual, not enforced.

## Inputs

- The input is the documented response body, unchanged, plus a small context (tenant, company, sometimes an employee id) for values that live in the URL. UKG spreads one person over several endpoints, so I defined two bundle wrappers (UKG employee, isolved payroll) that just group unmodified responses. That is my convention, not the providers'.

## Provider trade-offs

- PrismHR: vouchers have no payroll-run object, so I derive a run per `batchId` and flag it as derived. The pay amount field is ambiguous in the documentation (it reads like a rate), so I mapped it to the line amount and added a check that warns when lines and totals disagree. Live data did not contradict this, but it is still an interpretation.
- isolved: almost everything is free text, so I map only self-explanatory labels (Checking, Savings, Male, Female). The company is the legal company, with the client as its parent. A live check carries a run number but no run id, and I found no documented link, so I do not link paychecks to runs unless the caller supplies the run. An empirical match existed on the demo tenant, but it was undocumented and covered only one run type.
- UKG Ready: the reference has no company master, so a company holds only its EINs, and the app says so. Pay statements have no net pay, so gross is derived as the sum of the earnings and flagged as derived. The documented `payments` array is kept as typed lines, but I derive nothing from it because its meaning is not documented.

## Employee coverage

- The assignment asks for complete employee data. Every documented field is either typed or preserved verbatim in `extras`; I tested that with generated payloads that fill every documented field and found none dropped.
- But only about 41% (PrismHR), 47% (isolved) and 48% (UKG) of the documented values have a dedicated typed field. I promoted fields with a clear documented meaning and left the rest in `extras` rather than guess. I count preservation in `extras` as preserved, not as mapped.
- Ethnicity, disability, veteran status, citizenship and tobacco use are deliberately not typed. The read API has no authentication, and the assignment gives no reason to expose them.

## Security

- SSNs are reduced to the last four digits. Account numbers become `****1234` plus a salted hash that is used internally and is never returned by any API, file or trace. Routing numbers are kept because they identify a bank, not a person.
- Unmapped fields can still carry secrets, so one policy redacts sensitive-looking key names, labelled identity values and SSN-shaped strings in `extras`. It is not perfect: a secret under a generic, unlabelled key name cannot be recognised. I documented that instead of claiming full protection.
- The hash salt has no default. Outside tests, a missing or weak salt is an error, and `scripts/setup_env.py` generates a strong one locally.
- The API has no authentication and is meant for localhost only. It returns names and birth dates.

## Correctness choices

- Saving is atomic and idempotent: a payload with any error is rejected before anything is written, and saving the same payload twice changes nothing.
- If one payload contains the same record id twice with different content, I reject the whole payload instead of letting the later record win silently. An identical repeat is only a warning.
- A provider error response (for example a PrismHR `errorCode`) is reported as an error, not mapped as if it were a record.
- Employee ownership is where the data cannot decide. A bare deposit or deduction list does not say whose it is, so the employee id you supply is assigned to it. The app never guesses the owner, asks for explicit confirmation, and calls the result assigned, not verified. Records that carry their own owner always keep it. The integrity checks can show that an owner exists, but not that it is the right one.
- The data-quality checks are informational. I left out two rules that gave false alarms on live data (gross minus taxes and deductions equals net, and check amount equals net pay); they show as "not evaluated" with the reason.

## How I tested

- Fixtures are hand-written from the contracts, plus a seeded generator that fills every documented field. The tests check for crashes, lost values, leaked secrets, determinism, tenant isolation, idempotent and concurrent saves, and relationships. A snapshot of the canonical output guards against accidental changes.
- The live checks covered all three providers except two endpoints that were blocked: isolved deductions returned 401, and UKG deductions returned 404 (its reference page is tagged `dev`). Those two adapters are verified on fixtures only.
- Not verified: pagination beyond the first page, regular (non-conversion) isolved payroll, and any real production tenant.

## Assumptions I could not confirm

- The PrismHR interpretations above, including that `totalEarnings` is gross pay.
- That the example employee ids used by the one-click examples are the right owners for their bare files. That is the example set's own convention, not something the files state.
- That only documented codes should become enums, even where the meaning looks obvious (for example gender codes beyond male and female).

## Known limitations

- Typed employee coverage is below half, and UKG has no company master, net pay or benefit lines in its reference.
- There is no authentication, so the API must stay local.
- Saving a record that two files both contain stores it once but counts as an update each time. Whether that should create a new version is an open decision that I did not change.
- Typing more fields, such as manager names or probation dates, would need more database columns. I stopped at an additive set.

## Where the details are

- [Engineering notes](docs/ENGINEERING_NOTES.md): the full reasoning, live-validation findings, hardening history and implementation details behind each decision above.
- [Provider mappings](docs/PROVIDER_MAPPINGS.md): the field-by-field mapping for each provider.
- [Audit results](docs/AUDIT.md): requirement matrix, coverage measurements and findings.
- [Release checklist](docs/RELEASE_CHECKLIST.md): verification results and publishing notes.

Section numbers cited in code comments and in other documents ("DECISIONS.md section 12") refer to the engineering notes, which keep the original numbering.
