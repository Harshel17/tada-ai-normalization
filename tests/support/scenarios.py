"""The 15 provider x entity runs that the 14 synthetic fixture files drive.

One list, shared by the canonical-output snapshots and the detection tests, so a fixture can never be
described two different ways. `context` is what the provider payload does not contain itself.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    id: str
    provider: str
    entity: str
    folder: str  # directory under tests/fixtures
    file: str
    context: dict = field(default_factory=dict)  # beyond tenant_id


CASES = [
    Case(
        "prismhr-company",
        "prismhr",
        "company",
        "prismhr",
        "client_master.json",
        {"company_id": "ACME"},
    ),
    Case(
        "prismhr-employee",
        "prismhr",
        "employee",
        "prismhr",
        "employee_get_employee.json",
        {"company_id": "ACME"},
    ),
    Case(
        "prismhr-direct-deposit",
        "prismhr",
        "direct_deposit",
        "prismhr",
        "employee_get_employee.json",
        {"company_id": "ACME"},
    ),
    Case(
        "prismhr-deduction",
        "prismhr",
        "deduction",
        "prismhr",
        "employee_get_employee.json",
        {"company_id": "ACME"},
    ),
    Case(
        "prismhr-deduction-recurring",
        "prismhr",
        "deduction",
        "prismhr",
        "deductions_get_deductions.json",
        {"company_id": "ACME", "employee_id": "E1001"},
    ),
    Case(
        "prismhr-payroll",
        "prismhr",
        "payroll",
        "prismhr",
        "payroll_vouchers.json",
        {"company_id": "ACME"},
    ),
    Case("isolved-company", "isolved", "company", "isolved", "legals.json"),
    Case(
        "isolved-employee",
        "isolved",
        "employee",
        "isolved",
        "employees.json",
        {"company_id": "4501"},
    ),
    Case(
        "isolved-direct-deposit",
        "isolved",
        "direct_deposit",
        "isolved",
        "direct_deposits.json",
        {"company_id": "4501", "employee_id": "90001"},
    ),
    Case(
        "isolved-direct-deposit-embedded",
        "isolved",
        "direct_deposit",
        "isolved",
        "employees.json",
        {"company_id": "4501"},
    ),
    Case(
        "isolved-deduction",
        "isolved",
        "deduction",
        "isolved",
        "deductions.json",
        {"company_id": "4501"},
    ),
    Case(
        "isolved-payroll", "isolved", "payroll", "isolved", "payroll.json", {"company_id": "4501"}
    ),
    Case("ukg-company", "ukg-ready", "company", "ukg_ready", "eins.json", {"company_id": "CID1"}),
    Case(
        "ukg-employee",
        "ukg-ready",
        "employee",
        "ukg_ready",
        "employee_bundle.json",
        {"company_id": "CID1"},
    ),
    Case(
        "ukg-direct-deposit",
        "ukg-ready",
        "direct_deposit",
        "ukg_ready",
        "direct_deposits.json",
        {"company_id": "CID1", "employee_id": "7770001"},
    ),
    Case(
        "ukg-deduction",
        "ukg-ready",
        "deduction",
        "ukg_ready",
        "deductions.json",
        {"company_id": "CID1", "employee_id": "7770001"},
    ),
    Case(
        "ukg-payroll", "ukg-ready", "payroll", "ukg_ready", "payroll.json", {"company_id": "CID1"}
    ),
]
