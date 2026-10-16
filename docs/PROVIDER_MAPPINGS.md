# Provider mappings

Source → canonical field mappings, as implemented in `src/normalization/adapters/`. Every field name
below exists in the contract under `contracts/` (checked for the fixtures by
`tests/test_contract_fields.py`). "extras" means: not mapped, kept verbatim in `record.extras`.
`raw` means: the source value is kept in a `*_raw` field and the canonical enum is `null` unless noted.

Legend for code tables: only codes *documented* in the contract or the lookup files are mapped.

---

> **Provenance.** With `trace: true` the API reports, for the employee fields listed in the tables below that are marked in code with `to="…"` (identity, status, dates, job, top-level compensation), the exact source path each value came from. Other fields have no recorded source and the console says so. See ENGINEERING_NOTES.md section 12.

## PrismHR (OpenAPI 2025.09)

Identity: `tenant_id` = caller, `company_id` = `clientId` (context, or `clientMaster.id`).

> **Live demo notes (PrismHR).** `getPayrollVouchers` returned the list as `payrollVoucher` while the contract says `payrollVouchers`; both are accepted.
> `getEmployee` takes several ids as repeated `employeeId` query parameters (comma-joined ids returned 404). Without `options`, `getEmployee` returns
> every section name with null values. An empty `employeeDeductions.deduction` list is a valid response. See ENGINEERING_NOTES.md section 12.

### company — `GET /clientMaster/v1/getClientMaster` → `{"clientMaster": {...}}`

| Source | Canonical |
| --- | --- |
| `id` | `source_id`, `company_id` |
| `legalName` / `clientName` | `legal_name` / `display_name` |
| `statusClass` A,P,I,C,T | `status` (`active, pending, inactive, cancelling, terminated`); an unmapped class stays in extras |
| `status` (client-defined code) | `status_raw` |
| `statusDate`, `payrollDate` | `status_date`, `first_payroll_date` |
| `serviceType` | `service_type_raw` |
| `federalIdNumber`, `naicsCode`, `sicCode` | `federal_ein`, `naics_code`, `sic_code` |
| `addressLine1/2`, `city`, `stateCode`, `zipCode`+`zipSuffix` | `address` |
| `mainPhone`, `emailAddress`, `websiteUrl` | `phone`, `email`, `website` |
| everything else (pay codes, holidays, GL accounts, …) | extras |

### employee — `GET /employee/v1/getEmployee` → `{"employee": [...]}` (needs `context.company_id`)

| Source | Canonical |
| --- | --- |
| `id` | `source_id` |
| `firstName`, `middleInitial`, `lastName`, `preferredName` | `first_name`, `middle_name`, `last_name`, `preferred_name` |
| `person.birthDate`, `person.maritalStatus` | `birth_date`, `marital_status_raw` |
| `person.gender` M/F | `gender`; `X`, `D` stay in `gender_raw` only |
| `contactInformation.*` | `address` (incl. county, ZIP+4), `home_phone`, `mobile_phone`, `email` |
| `client.clientEmployeeId` | `employee_number` |
| `client.employeeStatus` | `employment_status_raw` (client-defined status id) |
| `client.statusClass` A/L/T | `employment_status` (`active, on_leave, terminated`) |
| `client.statusDate`, `jobStartDate`, `lastHireDate`, `firstHireDate`, `seniorityDate` | `status_date`, `start_date`, `hire_date`, `original_hire_date`, `seniority_date` |
| `client.termReasonCode`, `jobCode`, `employeeType`, `flsaExempt`, `manager` | `termination_reason_raw`, `job_code`, `employee_type_raw`, `flsa_exempt`, `manager_id` |
| `client.homeLocation/homeDivision/homeDepartment` | `work_location_code`, `division_code`, `department_code` |
| `client.workShift/projCostCenter/workGroupCode/subCostCenter` | `org_assignments` |
| `client.workEmail`, `workPhone` | `work_email`, `work_phone` |
| `compensation.ssn` | `ssn_last4` only; path in `redacted_paths` |
| `compensation.payMethod` S/H/C/D | `compensation.pay_type` (`salary, hourly, commission, driver`) + raw |
| `hourlyPay`, `salary`, `annualPay`, `standardHours`, `payGroup`, `effectiveDate` | `hourly_rate`, `pay_period_amount`, `annual_salary`, `standard_hours_per_pay_period`, `pay_group_raw`, `effective_date` |
| `directDeposit.achVoucher`, `scheduledDeduction` | owned by the other two entities; `directDeposit.achStatus`/`voucherType` stay in extras |
| rest (`person.*`, tax withholding, health, skills, checksums, …) | extras |

Additional employee mappings (documented fields only):

| Source (PrismHR) | Canonical |
| --- | --- |
| `client.preferredPronoun`, `person.preferredLanguage`, `person.deceasedDate` | `preferred_pronouns`, `preferred_language`, `deceased_date` |
| `person.emergencyContact[]` (`contactName/Relationship/Type/Info`) | `emergency_contacts[]` |
| `client.addressLine1/2/city/state/zip/zipSuffix` ("alternate mailing address") | `mailing_address` |
| `person.w2AddressLine1/2/City/State/PostalCode`, `person.geoCode`, `contactInformation.schoolDistrict` | `tax_form_address`, `tax_geo_code`, `school_district_code` |
| `client.returnDate`, `compensation.lastWorkedDate`, `client.rehireOk` Y/N, `client.termExplanation`, `person.peoStartDate`, `client.lastJobChangeDate` | `leave_return_date`, `last_worked_date`, `eligible_for_rehire` (blank → null), `termination_explanation`, `peo_start_date`, `job_effective_date` |
| `client.employee1099`, `officer`, `businessOwner`, `unionCode`, `clockNum` | `is_contractor`, `is_officer`, `is_business_owner`, `union_code_raw`, `timeclock_id` |
| `compensation.payAllocation[]` | `labor_allocations[]` (percent, location, division, department, job, project) |
| `compensation.payPeriod`, `lastChangeAmt/Pct`, `payChangeLastDate`, `perDiemPay`, `payRate2..4` | `pay_frequency_raw`, `last_change_*`, `per_diem_amount`, `alternate_rates[]` |
| `compensation.fed*`, `stateTax[]`, `localTax[]`, `ficaExempt`, `nonResAlien` | `tax_withholdings[]` (federal/state/local), `fica_exempt`, `non_resident_alien` |
| `client.benefitGroup`, `retirementBenefitGroup`, `cafeteriaPlan[]`, `courtOrderMedical`, `compensation.benefitSalary`, `benefitEnrollmentCompleted`, `enrollmentInProgress` | `benefits` |
| ethnicity, veteran/disability, health, skills, vehicles, checksums, … | extras (see AUDIT.md §5) |

Gaps: no job title, no termination date field.

### direct_deposit — `employee.directDeposit.achVoucher[]`

| Source | Canonical |
| --- | --- |
| `transitNum` | `routing_number` |
| `accountNum` | `account_number_masked` (the raw number is never stored); a salted `account_number_hash` is kept internally for the database and never returned |
| `accountType` C/S/IP | `account_type` `checking`/`savings`/`instant_pay` (C includes pay cards) |
| `method` F | `allocation_type=fixed_amount`, `amount` |
| `method` FS | fixed amount, `skip_if_insufficient=true` |
| `method` P / PN | `percentage` (`amount` in the source is the percent); `limit` → `cap_amount` |
| `method` B | `remaining_balance` (no amount) |
| `status` P/D/I | `prenote` / `active` / `inactive`; `active` true/false for D/I |
| `bankName` | `bank_name` |
| `voucherTypeOverride` else `directDeposit.voucherType` | `voucher_types_raw` |
| `directDeposit.achStatus` | extras `directDeposit.achStatus` on each instruction |
| array position | `source_position` (the source documents no priority field) |

`source_id` = routing number + last 4 digits of the account + account type + method + override (a stable natural
key built only from values the record already shows; PrismHR gives no id, and the key deliberately does not use
the account hash, so it leaks nothing and survives a salt change), with `#2`, `#3` for exact duplicates.

### deduction setup

* `getDeductions` → `{"employeeDeductions": {"deduction": [...]}}` (needs `context.employee_id`), `setup_kind=recurring`:
  `deductionCode`, `description`→`name`, `deductionType`→`category_raw`, `deductionMethod`→`calc_method_raw`,
  `usePercent`+`amount`→`percentage` or `amount`, `limit`→`per_period_limit`, `maximum`/`basis`→`max_amount`/`max_basis_raw`,
  `period`→`employee_frequency_raw`, `matchMethod`/`matchAmount`, `startDate`/`stopDate`, `loanOrGarnishId`,
  `benefitPlanId`, `cutbackPayeeCode`→`payee_code`, `trackArrears`, `mandatory`; rest (GL override, rate table, checksum) → extras.
* `employee.scheduledDeduction[]`, `setup_kind=scheduled`: `deductionCode`, `checkStubDescription`→`name`,
  `status` A/I/O→`active/inactive/one_time`, `amount`, `startDate`, `stopDate`, `cutbackPayeeCode`.

### payroll — `getPayrollVouchers` / `ForEmployee` → `{"payrollVouchers": [...]}`

| Source (voucher) | Canonical (`Paycheck`) |
| --- | --- |
| `voucherId`, `employeeId`, `batchId` | `source_id`, `employee_id`, `run_id` |
| `type` R/V/S/A/J | `paycheck_type` `regular/vacation/supplemental/arrears/adjustment` |
| `checkNumber` (or `checkNum`), `status`, `payGroup`, `voidDate` | `check_number`, `status_raw`, `pay_group_raw`, `void_date` |
| `payDate` / `payPeriodStart` / `payPeriodEnd` | `pay_date` / `period_start` / `period_end` |
| `totalEarnings`, `netPay`, `checkAmt` | `gross_pay`, `net_pay`, `check_amount` |
| `earning[]` | `EarningLine`: `payCode`, `payClassDescription`, `hoursWorked`→`hours`, `hoursUnits`→`units`, `unitRate`→`rate`, `payAmount`→`amount`, `chargeDate` |
| `otherEarningsDetail[]` | `EarningLine` (`source_kind=otherEarningsDetail`) |
| `employeeTax[]` | `TaxLine` employee: `empTaxDeductCode`, `empTaxAmount`, `empTaxableAmount` |
| `companyTax[]` | `TaxLine` employer: `comTaxDeductCode`, `comTaxAmount`, `comTaxableAmount`, `comTaxRate` |
| `deduction[]`, `otherDeduction[]` | `DeductionLine` (`deductType`→`category_raw`, `planId`) |
| `benefit[]` | `BenefitContributionLine`: `empContrib`, `empDeduct`→`employee_deducted_amount`, `premiumAmt` |
| `employerContributions[]`, `fsaMatch[]` | `BenefitContributionLine` employer side |
| `retirement{}` | one `BenefitContributionLine` per non-null contribution component |
| `directDeposit[]` | `PaycheckPayment` (account masked) — what was *paid*, not the instruction |
| `glAcctDistribution`, `wcInformation`, `workerComp`, `taxFilingStatus`, arrears, … | paycheck extras |

`PayrollRun` is derived per `batchId` (see ENGINEERING_NOTES.md §5).

---

## isolved (Swagger 2.0, API v6 primary snapshot)

Identity: `company_id` = **legal** company id, `parent_company_id` = client id. Bodies do not repeat them, so
`context.company_id` (legalId) is required (except `company`).

> **Live demo notes (isolved).** Checks are returned in the paged envelope `{results: [...], totalItems, ...}` (accepted); an employee without checks
> returns an empty page. A check has `payrollRunNumber`/`payrollRunType` but no run id, so paychecks are not linked to run history unless the caller sends
> the run with them. The corestream deductions endpoint returned 401 on the demo tenant (blocked by provider permissions). See ENGINEERING_NOTES.md section 12.

### company — `GET /api/clients/{clientId}/legals` → array of legal companies

`id`→`source_id`/`company_id`; `client.id`→`parent_company_id`; `client.clientName`→`parent_company_name`;
`legalName`, `dbaName`→`display_name`; `federalEIN`; `naicsCode`; `status`→`status_raw` (values undocumented);
`legalAddress.*`→`address`, `legalAddress.phoneNumber`→`phone`; rest (`payGroups`, `workLocations`, `legalCode`, …) → extras.

### employee — `.../employees` → `{"results": [...]}`

| Source | Canonical |
| --- | --- |
| `id`, `employeeNumber` | `source_id`, `employee_number` |
| `nameAddress.firstName/middleName/lastName/preferredName/prefix/suffix` | name fields |
| `nameAddress.address1/address2/city/state/zipCode` | `address` |
| `overrideAddress` (unless `useForPaychecks` is false) | `mailing_address` |
| `personal.homePhone/mobilePhone/officePhone`, `personalEmailAddress`; `emailAddress` | phones, `personal_email`, `email` |
| `birthDate`, `gender`, `maritalStatus` | `birth_date`, `gender` (`Male`/`Female` only) + raw, `marital_status_raw` |
| `ssn` | `ssn_last4` only |
| `employmentStatus`, `statusDate` | `employment_status_raw` (free text), `status_date` |
| `hireDate`, `originalHireDate`, `rehireDate`, `terminationDate`, `terminationReason` | dates / `termination_reason_raw` |
| `jobCode`, `jobTitle`, `jobFLSAExempt`, `employmentCategoryCode`, `workLocation` | job fields, `employee_type_raw` |
| `assignedManager.id`, `assignedSupervisor.id` | `manager_id`, `supervisor_id` |
| `organizations[]`, `labors[]` | `org_assignments` |
| `payType`, `hourlyRate`, `perPaySalary`, `annualSalary`, `normalHours`, `payGroup`, `salaryEffectiveDate` | `Compensation` |
| `directDeposits` | direct_deposit entity |
| `nameAddress.preferredPronouns`/`personal.preferredPronouns`, `personal.faxNumber`, `nameAddress.residentGeoCode`/`schoolDistrictCode` | `preferred_pronouns`, `fax`, `tax_geo_code`, `school_district_code` |
| `overrideAddress` (or the copy in `nameAddress`) + `useForPaychecks` / `useForTaxForms`; foreign and military variants | `mailing_address` / `tax_form_address` (flags that were not acted on stay in extras) |
| `eligibleForRehire`, `terminationType`, `jobGroup`, `jobEffectiveDate`, `socCode`, `jobEeoCategory`, `employmentCategoryFullTimeEquivalent`, `isManager`, `isSupervisor`, `isRemoteWorker`, `jobIsSeasonal`, `jobIsUnion`, `workersCompCode`, `timeclockId`, `team` | matching `Employee` attributes; `team` → `org_assignments` |
| `payFrequency`, `payRates[]` | `compensation.pay_frequency_raw` (code meaning undocumented), `alternate_rates[]` |
| `taxes.federal / residentState / nonResidentState / residentCity / nonResidentCity` | `tax_withholdings[]` with `jurisdiction` + `residency` |
| `profilePicture`, `attachments[].documentContent`, `i9Documents.*.documentContent` / `documentNumber` | **withheld** |
| rest (`i9Documents` metadata, `payFrequency`, misc fields, …) | extras |

### direct_deposit — `.../directdeposits` array (needs `context.employee_id`), or employees with `directDeposits`

`id`→`source_id`; `routingNumber`; `accountNumber`→masked+hash; `accountType` "Checking"/"Savings"→enum else raw;
`amount`→`fixed_amount`; `percent`→`percentage`; neither and `sequenceNumber == 0` ("0 = Net Pay") →
`remaining_balance` (derived); `sequenceNumber`→`sequence`; `description`→`name`; `frequency`→`frequency_raw`;
`status`→`status_raw` (undocumented values); `bankName`.

### deduction setup — `.../corestream/.../deductions` array

`id`; `employeeId`; `payItemCode`→`deduction_code`; `referenceNumber`; `startDate`/`stopDate`;
`calcParams[]`→`calc_params` (`paramType`, `paramValue`; meanings undocumented, so `amount`/`percentage` stay null).

### payroll — checks (+ optional `payroll_run`)

| Source | Canonical |
| --- | --- |
| run `id`, `payDate`, `periodBeginDate`, `periodEndDate`, `scheduledPayDate`, `payOfTheMonth`, `payrollRunType`, `status`, `runNumber` | `PayrollRun` |
| check `id`, `employeeId`, `checkNumber`, `checkTypeDescription` | `Paycheck.source_id`, `employee_id`, `check_number`, `paycheck_type_raw` |
| `checkDate` / `periodBeginDate` / `periodEndDate` | `pay_date` / `period_start` / `period_end` |
| `grossPay`, `netPay`, `checkAmount` | `gross_pay`, `net_pay`, `check_amount` (`grossWages` → extras) |
| `earnings[]` | `EarningLine`: `itemCode`, `itemDescription`, `checkHours`, `checkUnits`, `checkDollars`, `isMemoEarning`; `current/mtd/ptd/qtd/ytd/ftdDollars` → `accumulators` |
| `taxes[]` | `TaxLine`: `isEmployeeTax` true/false → employee/employer (missing → `null`), `checkDollars`, `checkTaxableWages` |
| `deductions[]`, `garnishments[]` | `DeductionLine` |
| `benefits[]` | `BenefitContributionLine`: `checkEmployeeDeduction`, `checkEmployerContribution` |
| `directDeposits[]` | `PaycheckPayment` (`account` masked, `depositAmount`) |
| `employeeSSN`, `employeeName`, `employeeAddress*`, `employeeCity/State/ZipCode` | **withheld** (employee snapshot), listed in `redacted_paths` |
| `accruals`, `taxCategories`, filing status, … | extras |

Gap: a check does not name its payroll run; the link exists only when `payroll_run` is supplied alongside.

---

## UKG Ready (cached reference docs + extracted JSON schemas; not OpenAPI)

Identity: `company_id` = company id (`cid`); employee ids are account ids.

> **Live demo notes (UKG Ready).** Base URL `https://secure2.saashr.com/ta` (documented in the reference pages); employee sections are separate calls that we
> assemble into the bundle in memory; direct deposits need the `X-End-of-Support-Timeline` header. A single pay statement carries an `employee: {account_id}`
> reference (not an employee bundle). Empty `{deposits: []}` and `{pay_statements: []}` are valid. Live pay statements expose no net pay. SSNs arrive
> unmasked and are never copied to output. The deductions endpoint returned 404 on the demo (UKG) and 401 (isolved), so deductions are fixture-tested only.
> See ENGINEERING_NOTES.md section 12.

### company — `GET /v2/companies/{cid}/lookup/eins` → `{"items": [...]}`

One `Company` (`source_id` = `cid`) with `legal_entities`: `id`, `ein_name`, `ein_tax_id`,
`ein_currency_code`, `ein_country_id`. There is no company-master endpoint, so name, status and address are `null`.

### employee — profile, optionally bundled with demographics / pay-info / compensation history

| Source | Canonical |
| --- | --- |
| profile `id`, `employee_id`, `username` | `source_id`, `employee_number`, `username` |
| `first_name`, `middle_name`, `last_name`, `nickname`, `salutation`, `suffix` | name fields |
| `address`, `mailing_address` (if `use_separate_mailing_address`) | `address` (`number`+`number_addition` → `house_number`), `mailing_address` |
| `phones.cell_phone/home_phone/work_phone`, `primary_email` | phones, `email` |
| `social_security`, `national_id_numbers[].value`, `pay_info.bank_account_number` | **withheld**; `ssn_last4` from SSN |
| `dates.birthday/hired/re_hired/started/seniority/terminated` | `birth_date`, `hire_date`, `rehire_date`, `start_date`, `seniority_date`, `termination_date` |
| `status` | `employment_status_raw` (values undocumented) |
| `managers[]` (lowest `index`) | `manager_id`; the others in extras `other_managers` |
| `cost_centers_info.defaults[]` | `org_assignments` (`cost_center_<index>`) |
| demographics `gender` MALE/FEMALE | `gender` (`UNDEFINED` raw only) |
| pay-info `default_job.id`, `employee_type.id`, `pay_type.id` | `job_code`, `employee_type_raw`, `compensation.pay_type_raw` (ids; meaning is tenant data) |
| compensation history, latest `effective_from`: `amount`+`amount_period`, `hourly_pay`, `currency` | `hourly_rate` (HOUR), `pay_period_amount` (PAY_PERIOD), `annual_salary` (YEAR), `other_rate`+`other_rate_period` (MONTH/WEEK) |
| `secondary_email`, `phones.preferred_phone` HOME/CELL/WORK/NONE | `secondary_email`, `preferred_phone` (home/mobile/work/none) |
| `dates.peo_hired`, `dates.retired`, `dates.deceased` | `peo_start_date`, `retirement_date`, `deceased_date` |
| pay-info `job_last_changed`, `eeo_classification`, `union.id`, `workers_comp_code.external_id`, `pay_grade.id` | `job_effective_date`, `eeo_job_category_raw`, `union_code_raw`, `workers_comp_code`, `org_assignments` (`pay_grade`) |
| pay-info `standard_work_day` (milliseconds), compensation `num_pp_in_year` | `compensation.standard_hours_per_day` (converted, flagged derived), `periods_per_year` |
| demographics `seasonal` | `is_seasonal` |
| pay-info `medical/dental/dependent_benefits/benefit_auto_enrollment` eligibility | `benefits.*_eligibility_raw` |
| rest (`external_id`, `timezone`, ethnicity/citizenship/visa, older compensation rows, …) | extras (grouped by endpoint name) |

### direct_deposit — `.../employees/{aid}/direct-deposits` → `{"deposits": [...]}`

`id`; `employee.account_id` (else `context.employee_id`); `name`; `type` (`DIRECT_DEPOSIT`/`RESERVE_LIVE_CHECK`)→`instruction_type_raw`;
`sequence`; `account_type` CHECKING/SAVINGS; `routing_number`; `account_number`→masked+hash;
`calc_method.type`: `FLAT_AMOUNT`→`fixed_amount` (+`amount`); `GROSS_EARNING`/`NET_PAY`/`DIRECT_DEPOSIT_PER_OF_REMAINDER`→`percentage`
(+`percentage`, basis in `percentage_basis_raw`); `FULL` raw only; `active`, `active_from`/`active_to`;
`pre_note_status`→`prenote_status_raw`; `payroll_frequency.type`→`frequency_raw`; `currency`; rest (YTD totals, …) → extras.

### deduction setup — `.../employees/{aid}/deductions` → `{"deductions": [...]}` (list items or detail)

`id`; `deduction.code`/`name`; `sequence`; `effective_from`/`effective_to`; `memo`; `annual_max_amount`,
`er_annual_max_amount`; `ee_frequency.type`/`er_frequency.type`; `ee_calc_method.method.type`→`calc_method_raw`,
`.amount`, `.percent`→`percentage`; `vendor.id`/`name`→`payee_code`/`payee_name`; rest (min/max calc, cost centers) → extras.

### payroll — `{"payrolls": [...]}`, `{"payroll", "pay_statements"}` or one pay statement

| Source | Canonical |
| --- | --- |
| run `id`, `name`, `start_date`, `end_date`, `pay_date`, `direct_deposit_date`, `status`, `type.display_name`, `ein.id` | `PayrollRun` (`period_start/end`, `pay_date`, `direct_deposit_date`, `status_raw`, `run_type_raw`, `ein_id`) |
| statement `id`, `employee.account_id`, `pay_date`, `pay_period_start/end`, `status`, `type.display_name` | `Paycheck` |
| `earnings[]` | `EarningLine`: `earning.code`/`external_id`, `earning.name`, `hours`, `quantity`→`units`, `base_rate`→`rate`, `ee_amount`→`amount`, `date_range_from/to` |
| `taxes[]` | `TaxLine`: `company_tax.code/name`; `ee_amount`/`ee_subject_wage` → employee line; `er_amount`/`er_subject_wage` → employer line |
| `deductions[]` | `DeductionLine`: `ee_amount`→`amount`, `er_amount`→`employer_amount` |
| gross | **derived** = sum of earning `ee_amount` (flagged in `derived_fields`); `net_pay`, `check_amount` are `null` |
| `payments[]`, `messages[]` | extras (meaning undocumented) |

Gap: no benefit lines, no net pay, no check number.


---

## Second coverage step (final correctness pass)

Fields promoted from `extras` to typed canonical fields. Each has a description in the contract; an unparseable value stays in `extras` with an
`invalid_date` / `invalid_decimal` issue.

| Provider | Source | Canonical |
| --- | --- | --- |
| PrismHR | `compensation.lastPayDate` | `compensation.last_pay_date` |
| PrismHR | `compensation.compReviewNextDate` | `compensation.next_review_date` |
| PrismHR | `compensation.compaRatio` ("comparative ratio", kept as given) | `compensation.compa_ratio` |
| PrismHR | `compensation.perfReviewLastDate`, `perfReviewLastRating`, `perfReviewLastTitle`, `perfReviewNextDate` | `compensation.last_performance_review_date`, `last_performance_review_rating_raw`, `last_performance_review_job_title`, `next_performance_review_date` |
| PrismHR | `compensation.paidThruDate` (contributions deducted through this date) | `benefits.paid_through_date` |
| PrismHR | `client.typeDate` | `employee_type_effective_date` |
| PrismHR | `client.unionDate` | `union_start_date` |
| PrismHR | `client.workPhoneExt` | `work_phone_extension` |
| isolved | `employmentCategoryEffectiveDate` | `employee_type_effective_date` |
| isolved | `acaEmploymentStatus` (free text, kept as given) | `aca_employment_status_raw` |
| UKG Ready | `compensation_history[latest].time` (milliseconds) with `time_period` WEEK | `compensation.standard_hours_per_week` (derived: ms / 3,600,000) |
| UKG Ready | the same with `time_period` PAY_PERIOD | `compensation.standard_hours_per_pay_period` (derived) |
| UKG Ready | `time` with `time_period` MONTH or YEAR | stays in `extras` (no canonical field for those periods) |
| UKG Ready | pay statement `payments[].ee_amount`, `type_name` | `paycheck.payments[].amount`, `description` (the meaning of a payment is undocumented: no net pay is derived) |

Not promoted on purpose: `quartile` (no description), `reportsTo` ("PTO Approver", not a manager), `probationDate`, protected demographics, documents.
