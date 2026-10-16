"""Cross-provider behaviour: service errors, tenant isolation, determinism, relationships."""

import copy
import json

import pytest

from normalization import service
from normalization.adapters import ADAPTERS
from normalization.canonical.models import NormalizationResult

# (provider, entity, fixture dir, fixture file, extra context) for every supported combination
CASES = [
    ("prismhr", "company", "prismhr", "client_master.json", {}),
    ("prismhr", "employee", "prismhr", "employee_get_employee.json", {}),
    ("prismhr", "direct_deposit", "prismhr", "employee_get_employee.json", {}),
    ("prismhr", "deduction", "prismhr", "employee_get_employee.json", {}),
    ("prismhr", "payroll", "prismhr", "payroll_vouchers.json", {}),
    ("isolved", "company", "isolved", "legals.json", {}),
    ("isolved", "employee", "isolved", "employees.json", {}),
    ("isolved", "direct_deposit", "isolved", "direct_deposits.json", {"employee_id": "90001"}),
    ("isolved", "deduction", "isolved", "deductions.json", {}),
    ("isolved", "payroll", "isolved", "payroll.json", {}),
    ("ukg-ready", "company", "ukg_ready", "eins.json", {}),
    ("ukg-ready", "employee", "ukg_ready", "employee_bundle.json", {}),
    ("ukg-ready", "direct_deposit", "ukg_ready", "direct_deposits.json", {"employee_id": "1"}),
    ("ukg-ready", "deduction", "ukg_ready", "deductions.json", {"employee_id": "1"}),
    ("ukg-ready", "payroll", "ukg_ready", "payroll.json", {}),
]
IDS = [f"{c[0]}-{c[1]}" for c in CASES]


def context(provider, tenant, **extra):
    base = {"prismhr": "ACME", "isolved": "4501", "ukg-ready": "CID1"}[provider]
    return {"tenant_id": tenant, "company_id": base, **extra}


@pytest.mark.parametrize(("provider", "entity", "d", "name", "extra"), CASES, ids=IDS)
class TestEveryCombination:
    def test_ok_and_json_round_trip(self, fixture, provider, entity, d, name, extra):
        result = service.normalize(
            provider, entity, fixture(d, name), context(provider, "t", **extra)
        )
        assert result.ok and result.records
        again = NormalizationResult.model_validate_json(result.model_dump_json())
        # extras hold raw source values (Decimal -> str in JSON), so compare the JSON itself
        assert again.model_dump_json() == result.model_dump_json()
        assert [r.record_type for r in again.records] == [r.record_type for r in result.records]

    def test_deterministic_and_does_not_mutate_input(
        self, fixture, provider, entity, d, name, extra
    ):
        payload = fixture(d, name)
        before = copy.deepcopy(payload)
        ctx = context(provider, "t", **extra)
        first = service.normalize(provider, entity, payload, ctx)
        second = service.normalize(provider, entity, payload, ctx)
        assert payload == before
        assert first.model_dump_json() == second.model_dump_json()

    def test_every_record_carries_identity_and_provenance(
        self, fixture, provider, entity, d, name, extra
    ):
        result = service.normalize(
            provider, entity, fixture(d, name), context(provider, "tt", **extra)
        )
        for r in result.records:
            assert r.provider.value == provider and r.tenant_id == "tt" and r.source_id
            assert r.provenance.source_endpoint

    def test_sensitive_values_never_appear_in_output(
        self, fixture, provider, entity, d, name, extra
    ):
        result = service.normalize(
            provider, entity, fixture(d, name), context(provider, "t", **extra)
        )
        out = result.model_dump_json()
        for secret in (
            "123-45-6789",
            "987-65-4321",
            "900-44-3333",
            "000123456789",
            "998877665544",
            "5550001111",
            "5550002222",
            "4400112233",
            "000999888777",
        ):
            assert secret not in out

    def test_garbage_payloads_are_errors_not_exceptions(self, provider, entity, d, name, extra):
        for bad in ("a string", 42, None, [1, "x"]):
            result = service.normalize(provider, entity, bad, context(provider, "t", **extra))
            assert not result.ok or not result.records
            assert result.records == [] or result.issues


class TestTenantIsolation:
    @pytest.mark.parametrize(("provider", "entity", "d", "name", "extra"), CASES, ids=IDS)
    def test_same_payload_for_two_tenants_has_distinct_identities(
        self, fixture, provider, entity, d, name, extra
    ):
        a = service.normalize(provider, entity, fixture(d, name), context(provider, "A", **extra))
        b = service.normalize(provider, entity, fixture(d, name), context(provider, "B", **extra))

        def identities(res):
            return {
                (r.provider, r.tenant_id, r.company_id, r.record_type, r.source_id)
                for r in res.records
            }

        assert identities(a).isdisjoint(identities(b))
        assert len(identities(a)) == len(a.records)  # no collisions inside one tenant

    def test_same_source_id_in_two_providers_stays_distinct(self, fixture):
        # both providers can legitimately issue the id "1"
        pr = service.normalize(
            "prismhr",
            "employee",
            {"employee": [{"id": "1"}]},
            {"tenant_id": "t", "company_id": "C"},
        )
        uk = service.normalize(
            "ukg-ready",
            "employee",
            {"employees": [{"id": 1}]},
            {"tenant_id": "t", "company_id": "C"},
        )
        assert pr.records[0].source_id == uk.records[0].source_id == "1"
        assert pr.records[0].provider != uk.records[0].provider


class TestServiceErrors:
    def test_unknown_provider_and_entity_raise(self):
        with pytest.raises(service.UnknownProviderOrEntity):
            service.normalize("workday", "employee", {}, {"tenant_id": "t"})
        with pytest.raises(service.UnknownProviderOrEntity):
            service.normalize("prismhr", "benefits", {}, {"tenant_id": "t"})

    def test_provider_aliases(self):
        assert service.resolve_provider("UKG").value == "ukg-ready"
        assert service.resolve_provider("ukg_ready").value == "ukg-ready"
        assert service.resolve_entity("direct-deposit").value == "direct_deposit"

    def test_missing_tenant_is_rejected(self):
        with pytest.raises(Exception, match="tenant_id"):
            service.normalize("prismhr", "employee", {"employee": []}, {"company_id": "C"})

    def test_empty_payload_is_reported(self):
        result = service.normalize(
            "prismhr", "employee", {"employee": []}, {"tenant_id": "t", "company_id": "C"}
        )
        assert [i.code for i in result.issues] == ["no_records"]

    def test_adapter_registry_matches_providers(self):
        assert set(ADAPTERS) == {"prismhr", "isolved", "ukg-ready"}

    def test_bad_values_become_issues_on_the_record_not_failures(self):
        payload = {
            "employee": [
                {
                    "id": "E1",
                    "compensation": {"hourlyPay": "lots", "payMethod": "H"},
                    "client": {"lastHireDate": "yesterday"},
                }
            ]
        }
        result = service.normalize(
            "prismhr", "employee", payload, {"tenant_id": "t", "company_id": "C"}
        )
        assert result.ok
        assert {i.code for i in result.issues} == {"invalid_decimal", "invalid_date"}
        (e,) = result.records
        assert e.compensation.hourly_rate is None and e.hire_date is None
        assert e.extras["compensation"]["hourlyPay"] == "lots"  # bad value kept, not lost
        assert all("lots" not in i.message and "yesterday" not in i.message for i in result.issues)

    def test_one_bad_record_does_not_hide_the_good_ones(self, fixture):
        payload = fixture("prismhr", "employee_get_employee.json")
        payload["employee"].insert(0, {"firstName": "No Id"})
        result = service.normalize(
            "prismhr", "employee", payload, {"tenant_id": "t", "company_id": "C"}
        )
        assert not result.ok
        assert [r.source_id for r in result.records] == ["E1001", "E1002"]


class TestRelationshipIntegrity:
    """Records from different calls must line up on (tenant, company, employee, run)."""

    def ids(self, result, field="source_id"):
        return {getattr(r, field) for r in result.records}

    def test_prismhr(self, fixture, prism_ctx):
        n = lambda e, f: service.normalize("prismhr", e, fixture("prismhr", f), prism_ctx)  # noqa: E731
        employees = n("employee", "employee_get_employee.json")
        deposits = n("direct_deposit", "employee_get_employee.json")
        company = n("company", "client_master.json")
        payroll = n("payroll", "payroll_vouchers.json")
        checks = [r for r in payroll.records if r.record_type == "paycheck"]
        runs = {r.source_id for r in payroll.records if r.record_type == "payroll_run"}
        assert self.ids(deposits, "employee_id") <= self.ids(employees)
        assert {c.employee_id for c in checks} <= self.ids(employees)
        assert {c.run_id for c in checks} <= runs
        every = [*employees.records, *deposits.records, *payroll.records]
        assert {r.company_id for r in every} == self.ids(company)

    def test_isolved(self, fixture, isolved_ctx):
        n = lambda e, f, **x: service.normalize(  # noqa: E731
            "isolved", e, fixture("isolved", f), {**isolved_ctx, **x}
        )
        employees = n("employee", "employees.json")
        deds = n("deduction", "deductions.json")
        payroll = n("payroll", "payroll.json")
        company = service.normalize(
            "isolved",
            "company",
            fixture("isolved", "legals.json"),
            {"tenant_id": isolved_ctx["tenant_id"]},
        )
        checks = [r for r in payroll.records if r.record_type == "paycheck"]
        runs = {r.source_id for r in payroll.records if r.record_type == "payroll_run"}
        assert self.ids(deds, "employee_id") <= self.ids(employees)
        assert {c.employee_id for c in checks} <= self.ids(employees)
        assert {c.run_id for c in checks} <= runs
        assert {r.company_id for r in [*employees.records, *payroll.records]} == self.ids(company)
        assert company.records[0].parent_company_id == "77"

    def test_ukg(self, fixture, ukg_ctx):
        n = lambda e, f: service.normalize("ukg-ready", e, fixture("ukg_ready", f), ukg_ctx)  # noqa: E731
        employees = n("employee", "employee_bundle.json")
        deposits = n("direct_deposit", "direct_deposits.json")
        payroll = n("payroll", "payroll.json")
        company = n("company", "eins.json")
        checks = [r for r in payroll.records if r.record_type == "paycheck"]
        runs = {r.source_id for r in payroll.records if r.record_type == "payroll_run"}
        assert self.ids(deposits, "employee_id") <= self.ids(employees)
        assert checks[0].employee_id in self.ids(employees)
        assert {c.run_id for c in checks} <= runs
        assert {r.company_id for r in [*employees.records, *payroll.records]} == self.ids(company)
        # the EIN named on the run is one of the company's legal entities
        run_rec = next(r for r in payroll.records if r.record_type == "payroll_run")
        assert run_rec.ein_id in {e.entity_id for e in company.records[0].legal_entities}


def test_output_is_plain_json(fixture, prism_ctx):
    result = service.normalize(
        "prismhr", "payroll", fixture("prismhr", "payroll_vouchers.json"), prism_ctx
    )
    text = result.model_dump_json()
    assert json.loads(text)["records"][1]["gross_pay"] == "3846.15"
