"""Employee fields promoted from `extras` to structured canonical attributes.

Every mapping here is a documented field; nothing is inferred. Protected demographics (ethnicity,
disability, veteran status, citizenship, tobacco use) are deliberately NOT promoted, so they never
reach the read API (see DECISIONS.md section 5).
"""

from datetime import date
from decimal import Decimal

import pytest

from normalization import service

D = Decimal


def employees(provider, fixture_dir, name, fixture, ctx):
    result = service.normalize(provider, "employee", fixture(fixture_dir, name), ctx)
    assert result.ok, result.issues
    return {e.source_id: e for e in result.records}, result


@pytest.fixture
def prism(fixture, prism_ctx):
    return employees("prismhr", "prismhr", "employee_get_employee.json", fixture, prism_ctx)


@pytest.fixture
def isolved(fixture, isolved_ctx):
    return employees("isolved", "isolved", "employees.json", fixture, isolved_ctx)


@pytest.fixture
def ukg(fixture, ukg_ctx):
    return employees("ukg-ready", "ukg_ready", "employee_bundle.json", fixture, ukg_ctx)


class TestPrismHR:
    def test_personal_and_contacts(self, prism):
        e = prism[0]["E1001"]
        assert e.preferred_pronouns == "she/her" and e.preferred_language == "es"
        assert [
            (c.name, c.relationship, c.contact_type_raw, c.contact_info)
            for c in e.emergency_contacts
        ] == [("Sam Rivera", "spouse", "PHONE", "5125550177")]

    def test_alternate_mailing_and_w2_addresses(self, prism):
        e = prism[0]["E1001"]
        assert (e.mailing_address.line1, e.mailing_address.postal_code) == ("PO Box 77", "78702")
        assert (e.tax_form_address.line1, e.tax_form_address.city) == ("9 Tax Ln", "Dallas")
        assert e.tax_geo_code == "GEO123" and e.school_district_code == "ISD-5"
        assert e.address.line1 == "12 Main St"  # the residential address is unchanged

    def test_employment_attributes(self, prism):
        e = prism[0]["E1001"]
        assert e.leave_return_date == date(2025, 3, 1) and e.job_effective_date == date(2023, 9, 1)
        assert e.peo_start_date == date(2019, 6, 3)
        assert e.eligible_for_rehire is True
        assert (e.is_contractor, e.is_officer, e.is_business_owner) == (False, True, False)
        assert e.union_code_raw == "UN7" and e.timeclock_id == "CLK-9"

    def test_termination_details(self, prism):
        e = prism[0]["E1002"]
        assert e.eligible_for_rehire is False and e.termination_explanation == "Moved away"
        assert e.last_worked_date == date(2024, 9, 27)

    def test_blank_rehire_flag_is_unknown_not_false(self, fixture, prism_ctx):
        payload = {"employee": [{"id": "E9", "client": {"rehireOk": ""}}]}
        (e,) = service.normalize("prismhr", "employee", payload, prism_ctx).records
        assert e.eligible_for_rehire is None

    def test_compensation_details(self, prism):
        c = prism[0]["E1001"].compensation
        assert c.pay_frequency_raw == "BW" and c.last_change_date == date(2024, 1, 1)
        assert (c.last_change_amount, c.last_change_percent) == (D("5000.00"), D("5.26"))
        assert c.per_diem_amount == D("25.00")
        assert [(r.name, r.amount) for r in c.alternate_rates] == [("payRate2", D("55.00"))]

    def test_tax_withholding_federal_state_local(self, prism):
        taxes = {(t.jurisdiction, t.area): t for t in prism[0]["E1001"].tax_withholdings}
        fed = taxes[("federal", None)]
        assert (fed.filing_status_raw, fed.primary_allowances) == ("MJ", 2)
        assert (fed.dependents_amount, fed.other_income_amount, fed.deductions_amount) == (
            D("2000.00"),
            D("0"),
            D("4300.00"),
        )
        assert (fed.override_type_raw, fed.override_amount) == ("A", D("10.00"))
        assert fed.multiple_jobs is False and fed.form_filed is True and fed.form_year == "2024"
        state = taxes[("state", "TX")]
        assert (state.filing_status_raw, state.primary_allowances, state.exemption_amount) == (
            "S",
            1,
            D("0.00"),
        )
        assert state.certificate_filed is False
        local = taxes[("local", "DALLAS")]
        assert local.additional_amount == D("2.00") and local.certificate_filed is False
        assert (
            prism[0]["E1001"].fica_exempt is False and prism[0]["E1001"].non_resident_alien is False
        )

    def test_labor_allocation_split(self, prism):
        allocations = prism[0]["E1001"].labor_allocations
        assert [(a.location_code, a.percent) for a in allocations] == [
            ("AUS", D("60.00")),
            ("DAL", D("40.00")),
        ]
        assert (allocations[0].department_code, allocations[0].job_code) == ("PLAT", "ENG2")
        assert (allocations[1].division_code, allocations[1].project_code) == ("ENGR", "P-1")
        assert sum(a.percent for a in allocations) == D("100.00")

    def test_employee_without_tax_data_has_none(self, prism):
        assert prism[0]["E1002"].tax_withholdings == []

    def test_benefit_profile(self, prism):
        b = prism[0]["E1001"].benefits
        assert (b.benefit_group, b.retirement_benefit_group) == ("BG1", "RBG1")
        assert b.enrollment_completed is True and b.enrollment_in_progress is False
        assert b.enrollment_type_raw == "OE" and b.benefit_salary == D("100000.00")
        assert [(c.amount, c.effective_date) for c in b.cafeteria_contributions] == [
            (D("150.00"), date(2024, 1, 1))
        ]
        assert b.court_ordered_medical is False

    def test_protected_demographics_stay_in_extras(self, prism):
        e = prism[0]["E1001"]
        assert e.extras["person"]["ethnicCode"] == "2" and e.extras["person"]["veteran"] == "D"
        assert not hasattr(e, "ethnicity") and not hasattr(e, "veteran")


class TestIsolved:
    def test_personal_contact_and_addresses(self, isolved):
        e = isolved[0]["90001"]
        assert e.preferred_pronouns == "they/them" and e.fax == "2075550150"
        assert e.tax_geo_code == "GEO-ME-1" and e.school_district_code == "1234"
        assert e.mailing_address.line1 == "PO Box 12"
        assert e.tax_form_address.line1 == "PO Box 12"  # useForTaxForms is true

    def test_override_address_flags(self, fixture, isolved_ctx):
        def run(flags):
            emp = {"id": 1, "overrideAddress": {"address1": "X", "state": "ME", **flags}}
            (e,) = service.normalize("isolved", "employee", {"results": [emp]}, isolved_ctx).records
            return e

        only_tax = run({"useForPaychecks": False, "useForTaxForms": True})
        assert only_tax.mailing_address is None and only_tax.tax_form_address.line1 == "X"
        neither = run({"useForPaychecks": False, "useForTaxForms": False})
        assert neither.mailing_address is None and neither.tax_form_address is None
        assert neither.extras["overrideAddress"]["address1"] == "X"
        military = run({"militaryCity": "APO", "militaryState": "AE", "foreignPostalCode": "09001"})
        assert (military.mailing_address.city, military.mailing_address.postal_code) == (
            "APO",
            "09001",
        )

    def test_override_address_documented_inside_name_address(self, isolved_ctx):
        emp = {
            "id": 1,
            "nameAddress": {"overrideAddress": {"address1": "Nested PO", "city": "Bangor"}},
        }
        (e,) = service.normalize("isolved", "employee", {"results": [emp]}, isolved_ctx).records
        assert (e.mailing_address.line1, e.mailing_address.city) == ("Nested PO", "Bangor")

    def test_conflicting_pronouns_are_not_swallowed(self, isolved_ctx):
        emp = {
            "id": 1,
            "nameAddress": {"preferredPronouns": "she/her"},
            "personal": {"preferredPronouns": "he/him"},
        }
        result = service.normalize("isolved", "employee", {"results": [emp]}, isolved_ctx)
        (e,) = result.records
        assert (
            e.preferred_pronouns == "she/her"
            and e.extras["personal"]["preferredPronouns"] == "he/him"
        )
        assert any(i.code == "conflicting_fields" for i in result.issues)

    def test_employment_and_job_attributes(self, isolved):
        e = isolved[0]["90001"]
        assert (e.is_manager, e.is_supervisor, e.is_remote_worker) == (False, True, False)
        assert (e.is_seasonal, e.is_union_member, e.eligible_for_rehire) == (False, True, True)
        assert (e.job_group, e.soc_code, e.eeo_job_category_raw) == (
            "Cafe Staff",
            "35-3023",
            "Service Workers",
        )
        assert e.job_effective_date == date(2024, 6, 1) and e.full_time_equivalent == D("1.00")
        assert (e.workers_comp_code, e.timeclock_id) == ("9079", "TC-42")
        assert ("team", "5", "Morning Crew") in {
            (o.kind, o.code, o.description) for o in e.org_assignments
        }

    def test_alternate_rates_and_frequency(self, isolved):
        c = isolved[0]["90001"].compensation
        assert [(r.name, r.amount) for r in c.alternate_rates] == [("Overtime", D("28.875"))]
        assert c.pay_frequency_raw == "2"

    def test_tax_withholding_by_jurisdiction(self, isolved):
        taxes = {(t.jurisdiction, t.residency): t for t in isolved[0]["90001"].tax_withholdings}
        fed = taxes[("federal", None)]
        assert (fed.tax_category_id, fed.filing_status_raw, fed.primary_allowances) == (
            "FEDWT",
            "Single",
            1,
        )
        assert fed.additional_amount == D("5.00") and fed.dependents_amount == D("0")
        assert fed.multiple_jobs is False and fed.withholding_blocked is False
        state = taxes[("state", "resident")]
        assert (state.area, state.filing_status_raw) == ("ME", "S")
        assert ("state", "non_resident") not in taxes  # absent in the source -> not invented

    def test_binary_and_id_document_content_never_stored(self, isolved_ctx):
        emp = {
            "id": 1,
            "profilePicture": "QkFTRTY0UElDVFVSRQ==",
            "i9Documents": {
                "documentA": {
                    "documentNumber": "P123456789",
                    "documentContent": "QkFTRTY0RE9D",
                    "issuingAuthority": "State",
                }
            },
            "attachments": [{"documentFileName": "w4.pdf", "documentContent": "QkFTRTY0QVRU"}],
        }
        result = service.normalize("isolved", "employee", {"results": [emp]}, isolved_ctx)
        (e,) = result.records
        dumped = e.model_dump_json()
        for secret in ("QkFTRTY0", "P123456789"):
            assert secret not in dumped
        assert e.extras["i9Documents"]["documentA"]["issuingAuthority"] == "State"
        assert e.extras["attachments"][0]["documentFileName"] == "w4.pdf"
        assert len(e.provenance.redacted_paths) == 4


class TestUKG:
    def test_contact_and_dates(self, ukg):
        e = ukg[0]["7770001"]
        assert e.secondary_email == "mdiaz@example.test" and e.preferred_phone == "mobile"
        assert e.peo_start_date == date(2018, 3, 1) and e.retirement_date is None

    def test_undocumented_preferred_phone_stays_raw(self, ukg_ctx):
        payload = {"employee": {"id": 1, "phones": {"preferred_phone": "FAX"}}}
        (e,) = service.normalize("ukg-ready", "employee", payload, ukg_ctx).records
        assert e.preferred_phone is None and e.extras["phones"]["preferred_phone"] == "FAX"

    def test_pay_info_attributes(self, ukg):
        e = ukg[0]["7770001"]
        assert (
            e.job_effective_date == date(2024, 7, 1) and e.eeo_job_category_raw == "PROFESSIONALS"
        )
        assert (
            e.union_code_raw == "4" and e.workers_comp_code == "WC-8810" and e.is_seasonal is False
        )

    def test_work_day_is_converted_from_milliseconds_and_flagged(self, ukg):
        e = ukg[0]["7770001"]
        assert e.compensation.standard_hours_per_day == D("8")
        assert "compensation.standard_hours_per_day" in e.provenance.derived_fields
        assert e.compensation.periods_per_year == 26

    def test_work_day_without_compensation_history(self, ukg_ctx):
        payload = {"employee": {"id": 1}, "pay_info": {"standard_work_day": 14400000}}
        (e,) = service.normalize("ukg-ready", "employee", payload, ukg_ctx).records
        assert e.compensation.standard_hours_per_day == D("4")

    def test_benefit_eligibility(self, ukg):
        b = ukg[0]["7770001"].benefits
        assert (b.medical_eligibility_raw, b.dental_eligibility_raw) == ("ELIGIBLE", "PAYEE")
        assert b.dependent_benefits_eligibility_raw == "NOT_ELIGIBLE"
        assert b.auto_enrollment_eligibility_raw == "ELIGIBLE"

    def test_unsupported_groups_are_absent_not_invented(self, ukg):
        e = ukg[0]["7770001"]
        assert e.tax_withholdings == []  # tax info is a separate UKG endpoint that is not read
        assert e.emergency_contacts == []
