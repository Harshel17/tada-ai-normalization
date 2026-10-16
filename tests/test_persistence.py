"""M5: PostgreSQL persistence, idempotent ingestion, isolation, atomicity."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from normalization import service
from normalization.persistence import repository as repo
from normalization.persistence import tables as t
from normalization.persistence.db import transaction

pytestmark = pytest.mark.db
D = Decimal


def without_extras(node):
    """Reads drop source extras by design; compare everything else."""
    if isinstance(node, dict):
        return {k: ({} if k == "extras" else without_extras(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [without_extras(v) for v in node]
    return node


def normalized(provider, entity, payload, ctx):
    result = service.normalize(provider, entity, payload, ctx)
    assert result.ok, result.issues
    return result


def save(engine, result):
    with transaction(engine) as s:
        return repo.ingest(s, result)


def count(engine, model, **where):
    with transaction(engine) as s:
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return s.scalar(stmt)


@pytest.fixture
def prism_employees(fixture, prism_ctx):
    return normalized(
        "prismhr", "employee", fixture("prismhr", "employee_get_employee.json"), prism_ctx
    )


@pytest.fixture
def prism_payroll(fixture, prism_ctx):
    return normalized("prismhr", "payroll", fixture("prismhr", "payroll_vouchers.json"), prism_ctx)


class TestRoundTrip:
    def test_employee_survives_the_database(self, db, prism_employees):
        save(db, prism_employees)
        with transaction(db) as s:
            got = repo.get_employee(s, "prismhr", "tenant-prism", "ACME", "E1001")
        original = next(r for r in prism_employees.records if r.source_id == "E1001")
        expected = original.model_copy(update={"extras": {}})  # reads never return source extras
        assert got.model_dump() == expected.model_dump()
        assert got.compensation.pay_period_amount == D("3846.15")
        assert str(got.compensation.annual_salary) == "100000.00"  # scale preserved by NUMERIC

    def test_extras_and_provenance_are_stored_as_jsonb(self, db, prism_employees):
        save(db, prism_employees)
        with transaction(db) as s:
            row = s.scalars(select(t.EmployeeRow).where(t.EmployeeRow.source_id == "E1001")).one()
        assert row.extras["client"]["clientChecksum"] == "chk-client-1"
        assert row.provenance["source_path"] == "$.employee[0]"
        assert row.version == 1 and len(row.content_hash) == 64

    def test_all_entity_types_round_trip(self, db, fixture, prism_ctx, prism_payroll):
        data = fixture("prismhr", "employee_get_employee.json")
        for entity in ("company", "employee", "direct_deposit", "deduction"):
            payload = fixture("prismhr", "client_master.json") if entity == "company" else data
            save(db, normalized("prismhr", entity, payload, prism_ctx))
        save(db, prism_payroll)
        with transaction(db) as s:
            assert (
                repo.get_company(s, "prismhr", "tenant-prism", "ACME").legal_name
                == "Acme Widgets, Inc."
            )
            deposits = repo.list_direct_deposits(s, "prismhr", "tenant-prism", "ACME", "E1001")
            deds = repo.list_deductions(s, "prismhr", "tenant-prism", "ACME", "E1001")
            runs = repo.list_payroll_runs(s, "prismhr", "tenant-prism", "ACME")
        assert len(deposits) == 3 and len(deds) == 2 and len(runs) == 1

    def test_paycheck_lines_keep_exact_decimals(self, db, prism_payroll):
        save(db, prism_payroll)
        with transaction(db) as s:
            check = repo.get_paycheck(s, "prismhr", "tenant-prism", "ACME", "V-5001")
        assert check.gross_pay == D("3846.15") and check.net_pay == D("2410.37")
        assert check.earnings[0].rate == D("48.0769")
        assert str(check.earnings[0].hours) == "80.00"
        assert [t_.code for t_ in check.taxes] == ["FIT", "FICA", "MED", "FICA"]
        assert [d.code for d in check.deductions][:2] == ["401K", "MED125"]
        assert len(check.benefits) == 5 and len(check.payments) == 2
        assert check.payments[0].account_masked == "****6789"
        original = next(r for r in prism_payroll.records if r.source_id == "V-5001")
        assert without_extras(check.model_dump()) == without_extras(original.model_dump())

    def test_reads_never_return_source_extras(self, db, prism_employees):
        save(db, prism_employees)
        with transaction(db) as s:
            assert repo.get_employee(s, "prismhr", "tenant-prism", "ACME", "E1001").extras == {}


class TestEmployeeCoverageRoundTrip:
    """The promoted employee attributes (scalars, nested lists, JSONB) survive PostgreSQL intact."""

    @pytest.mark.parametrize(
        ("provider", "folder", "name", "ctx_key"),
        [
            ("prismhr", "prismhr", "employee_get_employee.json", "prism_ctx"),
            ("isolved", "isolved", "employees.json", "isolved_ctx"),
            ("ukg-ready", "ukg_ready", "employee_bundle.json", "ukg_ctx"),
        ],
    )
    def test_every_employee_comes_back_identical(
        self, db, fixture, request, provider, folder, name, ctx_key
    ):
        ctx = request.getfixturevalue(ctx_key)
        result = normalized(provider, "employee", fixture(folder, name), ctx)
        save(db, result)
        with transaction(db) as s:
            for original in result.records:
                got = repo.get_employee(
                    s, provider, ctx["tenant_id"], ctx["company_id"], original.source_id
                )
                assert without_extras(got.model_dump()) == without_extras(original.model_dump())

    def test_nested_decimals_keep_their_scale(self, db, prism_employees):
        save(db, prism_employees)
        with transaction(db) as s:
            e = repo.get_employee(s, "prismhr", "tenant-prism", "ACME", "E1001")
        fed = next(t for t in e.tax_withholdings if t.jurisdiction == "federal")
        assert str(fed.dependents_amount) == "2000.00" and str(fed.deductions_amount) == "4300.00"
        assert str(e.benefits.cafeteria_contributions[0].amount) == "150.00"
        assert str(e.compensation.alternate_rates[0].amount) == "55.00"

    def test_changing_a_nested_value_is_an_update_not_a_duplicate(self, db, fixture, prism_ctx):
        payload = fixture("prismhr", "employee_get_employee.json")
        save(db, normalized("prismhr", "employee", payload, prism_ctx))
        payload["employee"][0]["compensation"]["localTax"][0]["addlWithheld"] = "3.50"
        summary = save(db, normalized("prismhr", "employee", payload, prism_ctx))
        assert summary.counts["employee"] == {"inserted": 0, "updated": 1, "unchanged": 1}
        with transaction(db) as s:
            e = repo.get_employee(s, "prismhr", "tenant-prism", "ACME", "E1001")
        assert next(
            t for t in e.tax_withholdings if t.jurisdiction == "local"
        ).additional_amount == D("3.50")


class TestIdempotency:
    def test_reingesting_the_same_payload_changes_nothing(self, db, prism_employees):
        first = save(db, prism_employees)
        second = save(db, prism_employees)
        assert first.counts["employee"] == {"inserted": 2, "updated": 0, "unchanged": 0}
        assert second.counts["employee"] == {"inserted": 0, "updated": 0, "unchanged": 2}
        assert count(db, t.EmployeeRow) == 2
        with transaction(db) as s:
            versions = set(s.scalars(select(t.EmployeeRow.version)))
        assert versions == {1}

    def test_unchanged_rows_are_not_written(self, db, prism_employees):
        save(db, prism_employees)
        with transaction(db) as s:
            before = s.execute(select(t.EmployeeRow.last_changed_at, t.EmployeeRow.id)).all()
        save(db, prism_employees)
        with transaction(db) as s:
            after = s.execute(select(t.EmployeeRow.last_changed_at, t.EmployeeRow.id)).all()
        assert sorted(before) == sorted(after)

    def test_a_changed_source_updates_in_place_and_bumps_version(self, db, fixture, prism_ctx):
        payload = fixture("prismhr", "employee_get_employee.json")
        save(db, normalized("prismhr", "employee", payload, prism_ctx))
        payload["employee"][0]["client"]["jobCode"] = "ENG3"
        payload["employee"][0]["contactInformation"]["emailAddress"] = None  # field disappears
        summary = save(db, normalized("prismhr", "employee", payload, prism_ctx))
        assert summary.counts["employee"] == {"inserted": 0, "updated": 1, "unchanged": 1}
        with transaction(db) as s:
            e = repo.get_employee(s, "prismhr", "tenant-prism", "ACME", "E1001")
            row = s.scalars(select(t.EmployeeRow).where(t.EmployeeRow.source_id == "E1001")).one()
        assert e.job_code == "ENG3" and e.email is None  # replaced, not merged
        assert row.version == 2
        assert count(db, t.EmployeeRow) == 2

    def test_paycheck_lines_are_replaced_not_duplicated(self, db, fixture, prism_ctx):
        payload = fixture("prismhr", "payroll_vouchers.json")
        save(db, normalized("prismhr", "payroll", payload, prism_ctx))
        save(db, normalized("prismhr", "payroll", payload, prism_ctx))
        assert count(db, t.EarningLineRow) == 3 and count(db, t.TaxLineRow) == 4
        payload["payrollVouchers"][0]["employeeTax"].pop()  # a tax line disappears at the source
        save(db, normalized("prismhr", "payroll", payload, prism_ctx))
        assert count(db, t.TaxLineRow) == 3
        assert count(db, t.EarningLineRow) == 3
        assert count(db, t.PaycheckRow) == 2

    def test_concurrent_identical_ingests_do_not_duplicate(self, db, prism_employees):
        def go(_):
            return save(db, prism_employees).counts["employee"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(go, range(6)))
        assert count(db, t.EmployeeRow) == 2
        assert sum(o["inserted"] for o in outcomes) == 2  # exactly one writer inserted each row

    def test_every_call_is_audited(self, db, prism_employees):
        save(db, prism_employees)
        save(db, prism_employees)
        with transaction(db) as s:
            runs = s.scalars(select(t.IngestRunRow).order_by(t.IngestRunRow.created_at)).all()
        assert len(runs) == 2 and runs[0].payload_hash == runs[1].payload_hash
        assert runs[0].counts["employee"]["inserted"] == 2
        assert runs[1].counts["employee"]["unchanged"] == 2


class TestOrdering:
    def test_remaining_balance_deposit_is_listed_last(self, db, fixture, isolved_ctx):
        # isolved documents sequenceNumber 0 as "Net Pay"; sorting by number alone would put it first
        ctx = {**isolved_ctx, "employee_id": "90001"}
        save(
            db,
            normalized(
                "isolved", "direct_deposit", fixture("isolved", "direct_deposits.json"), ctx
            ),
        )
        with transaction(db) as s:
            got = repo.list_direct_deposits(s, "isolved", "tenant-isolved", "4501", "90001")
        assert [d.allocation_type.value for d in got] == [
            "fixed_amount",
            "percentage",
            "remaining_balance",
        ]


class TestIsolation:
    def test_same_payload_in_two_tenants_creates_two_sets_of_rows(self, db, fixture):
        payload = fixture("prismhr", "employee_get_employee.json")
        for tenant in ("tenant-a", "tenant-b"):
            save(
                db,
                normalized(
                    "prismhr", "employee", payload, {"tenant_id": tenant, "company_id": "ACME"}
                ),
            )
        assert count(db, t.EmployeeRow) == 4
        with transaction(db) as s:
            a = repo.list_employees(s, "prismhr", "tenant-a", "ACME")
            b = repo.list_employees(s, "prismhr", "tenant-b", "ACME")
            nobody = repo.list_employees(s, "prismhr", "tenant-c", "ACME")
        assert {e.tenant_id for e in a} == {"tenant-a"} and {e.tenant_id for e in b} == {"tenant-b"}
        assert nobody == []

    def test_updating_one_tenant_never_touches_another(self, db, fixture):
        payload = fixture("prismhr", "employee_get_employee.json")
        a = {"tenant_id": "tenant-a", "company_id": "ACME"}
        b = {"tenant_id": "tenant-b", "company_id": "ACME"}
        save(db, normalized("prismhr", "employee", payload, a))
        save(db, normalized("prismhr", "employee", payload, b))
        payload["employee"][0]["firstName"] = "Changed"
        save(db, normalized("prismhr", "employee", payload, a))
        with transaction(db) as s:
            assert (
                repo.get_employee(s, "prismhr", "tenant-a", "ACME", "E1001").first_name == "Changed"
            )
            assert (
                repo.get_employee(s, "prismhr", "tenant-b", "ACME", "E1001").first_name == "Jordan"
            )

    def test_same_company_and_employee_ids_in_two_companies(self, db, fixture):
        payload = fixture("prismhr", "employee_get_employee.json")
        for company in ("ACME", "OTHER"):
            save(
                db,
                normalized(
                    "prismhr", "employee", payload, {"tenant_id": "t", "company_id": company}
                ),
            )
        assert count(db, t.EmployeeRow) == 4

    def test_same_source_id_from_two_providers(self, db):
        save(
            db,
            normalized(
                "prismhr",
                "employee",
                {"employee": [{"id": "1", "firstName": "P"}]},
                {"tenant_id": "t", "company_id": "C"},
            ),
        )
        save(
            db,
            normalized(
                "ukg-ready",
                "employee",
                {"employees": [{"id": 1, "first_name": "U"}]},
                {"tenant_id": "t", "company_id": "C"},
            ),
        )
        with transaction(db) as s:
            names = {
                (e.provider.value, e.first_name)
                for e in [
                    *repo.list_employees(s, "prismhr", "t", "C"),
                    *repo.list_employees(s, "ukg-ready", "t", "C"),
                ]
            }
        assert names == {("prismhr", "P"), ("ukg-ready", "U")}

    def test_database_rejects_a_duplicate_identity(self, db, prism_employees):
        save(db, prism_employees)
        with pytest.raises(IntegrityError), transaction(db) as s:
            s.execute(
                text(
                    "INSERT INTO employees (id, provider, tenant_id, company_id, source_id, content_hash, version)"
                    " VALUES (gen_random_uuid(), 'prismhr', 'tenant-prism', 'ACME', 'E1001', 'x', 1)"
                )
            )

    def test_database_rejects_values_outside_canonical_enums(self, db):
        with pytest.raises(IntegrityError), transaction(db) as s:
            s.execute(
                text(
                    "INSERT INTO companies (id, provider, tenant_id, company_id, source_id, content_hash, version, status)"
                    " VALUES (gen_random_uuid(), 'prismhr', 't', 'c', 'c', 'x', 1, 'bogus')"
                )
            )
        with pytest.raises(IntegrityError), transaction(db) as s:
            s.execute(
                text(
                    "INSERT INTO companies (id, provider, tenant_id, company_id, source_id, content_hash, version)"
                    " VALUES (gen_random_uuid(), 'workday', 't', 'c', 'c', 'x', 1)"
                )
            )


class TestRelationships:
    def test_children_can_arrive_before_parents_and_are_linked_later(
        self, db, fixture, prism_ctx, prism_payroll
    ):
        save(db, prism_payroll)
        with transaction(db) as s:
            assert (
                s.scalar(
                    select(func.count())
                    .select_from(t.PaycheckRow)
                    .where(t.PaycheckRow.employee_pk.is_(None))
                )
                == 2
            )
        # now the parents arrive
        data = fixture("prismhr", "employee_get_employee.json")
        save(db, normalized("prismhr", "employee", data, prism_ctx))
        save(
            db,
            normalized("prismhr", "company", fixture("prismhr", "client_master.json"), prism_ctx),
        )
        with transaction(db) as s:
            rows = s.execute(
                select(
                    t.PaycheckRow.employee_id,
                    t.EmployeeRow.source_id,
                    t.PaycheckRow.run_pk,
                    t.PayrollRunRow.id,
                )
                .join(t.EmployeeRow, t.PaycheckRow.employee_pk == t.EmployeeRow.id)
                .join(t.PayrollRunRow, t.PaycheckRow.run_pk == t.PayrollRunRow.id)
            ).all()
            companies = s.execute(
                select(t.EmployeeRow.company_id, t.CompanyRow.company_id).join(
                    t.CompanyRow, t.EmployeeRow.company_pk == t.CompanyRow.id
                )
            ).all()
        assert len(rows) == 2 and all(r[0] == r[1] for r in rows)
        assert all(a == b for a, b in companies) and len(companies) == 2

    def test_links_never_cross_tenants(self, db, fixture, prism_payroll):
        save(db, prism_payroll)  # tenant-prism paychecks, no employees yet
        other = {"tenant_id": "someone-else", "company_id": "ACME"}
        save(
            db,
            normalized(
                "prismhr", "employee", fixture("prismhr", "employee_get_employee.json"), other
            ),
        )
        with transaction(db) as s:
            linked = s.scalar(
                select(func.count())
                .select_from(t.PaycheckRow)
                .where(t.PaycheckRow.employee_pk.is_not(None))
            )
        assert linked == 0

    def test_a_tenant_never_links_to_another_tenants_parents(self, db, fixture):
        """Tenant A owns employee E1001 and a run; tenant B has children that name the same ids."""
        a = {"tenant_id": "tenant-a", "company_id": "ACME"}
        b = {"tenant_id": "tenant-b", "company_id": "ACME"}
        data = fixture("prismhr", "employee_get_employee.json")
        save(db, normalized("prismhr", "employee", data, a))
        save(db, normalized("prismhr", "payroll", fixture("prismhr", "payroll_vouchers.json"), a))
        # tenant B ingests only children (no employees, no runs of its own)
        for entity in ("direct_deposit", "deduction"):
            save(db, normalized("prismhr", entity, data, b))
        vouchers = fixture("prismhr", "payroll_vouchers.json")
        save(db, normalized("prismhr", "payroll", vouchers, b))
        with transaction(db) as s:
            for table in (t.DirectDepositRow, t.DeductionSetupRow, t.PaycheckRow):
                linked = s.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.tenant_id == "tenant-b", table.employee_pk.is_not(None))
                )
                assert linked == 0, table.__tablename__
        # B's own run exists, so B's paychecks link to B's run, never to A's
        with transaction(db) as s:
            pairs = s.execute(
                select(t.PaycheckRow.tenant_id, t.PayrollRunRow.tenant_id).join(
                    t.PayrollRunRow, t.PaycheckRow.run_pk == t.PayrollRunRow.id
                )
            ).all()
        assert pairs and all(x == y for x, y in pairs)

    def test_deleting_an_employee_keeps_children_unlinked_not_deleted(self, db, fixture, prism_ctx):
        data = fixture("prismhr", "employee_get_employee.json")
        save(db, normalized("prismhr", "employee", data, prism_ctx))
        save(db, normalized("prismhr", "direct_deposit", data, prism_ctx))
        with transaction(db) as s:
            s.execute(text("DELETE FROM employees"))
        assert count(db, t.DirectDepositRow) == 3


class TestAtomicity:
    def test_results_with_errors_are_rejected_before_any_write(self, db, fixture, prism_ctx):
        payload = fixture("prismhr", "employee_get_employee.json")
        payload["employee"].append({"firstName": "No id"})
        result = service.normalize("prismhr", "employee", payload, prism_ctx)
        assert not result.ok
        with pytest.raises(repo.IngestRejected), transaction(db) as s:
            repo.ingest(s, result)
        assert count(db, t.EmployeeRow) == 0

    def test_a_failure_midway_rolls_everything_back(self, db, prism_payroll, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(repo, "_replace_lines", boom)
        with pytest.raises(RuntimeError), transaction(db) as s:
            repo.ingest(s, prism_payroll)
        assert count(db, t.PayrollRunRow) == 0 and count(db, t.PaycheckRow) == 0
        assert count(db, t.IngestRunRow) == 0


class TestSensitiveData:
    def test_no_full_account_or_ssn_in_any_table(self, db, fixture, prism_ctx, prism_payroll):
        data = fixture("prismhr", "employee_get_employee.json")
        for entity in ("employee", "direct_deposit", "deduction"):
            save(db, normalized("prismhr", entity, data, prism_ctx))
        save(db, prism_payroll)
        with transaction(db) as s:
            dump = ""
            for table in t.Base.metadata.tables:
                dump += " ".join(s.scalars(text(f"SELECT row_to_json(x)::text FROM {table} x")))
        for secret in ("000123456789", "998877665544", "123-45-6789", "123456789"):
            assert secret not in dump
        assert "****6789" in dump
