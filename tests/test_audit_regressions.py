"""Each test pins a defect found by the end-of-project audit."""

from decimal import Decimal

from normalization import service

PRISM = {"tenant_id": "t", "company_id": "ACME"}
UKG = {"tenant_id": "t", "company_id": "C"}


def test_prism_client_status_code_and_unmapped_class_are_both_kept():
    ok = service.normalize(
        "prismhr",
        "company",
        {"clientMaster": {"id": "A", "status": "ACT", "statusClass": "A"}},
        PRISM,
    )
    (co,) = ok.records
    assert co.status_raw == "ACT" and co.status.value == "active"
    odd = service.normalize(
        "prismhr",
        "company",
        {"clientMaster": {"id": "A", "status": "X1", "statusClass": "Z"}},
        PRISM,
    )
    (co,) = odd.records
    assert co.status is None and co.extras["statusClass"] == "Z" and co.status_raw == "X1"


def test_prism_unmapped_employee_status_class_is_kept():
    payload = {"employee": [{"id": "E1", "client": {"statusClass": "Q", "employeeStatus": "ACT"}}]}
    (e,) = service.normalize("prismhr", "employee", payload, PRISM).records
    assert e.employment_status is None and e.extras["client"]["statusClass"] == "Q"


def test_prism_employee_ach_switch_survives_even_without_accounts():
    payload = {"employee": [{"id": "E1", "directDeposit": {"achStatus": "I", "achVoucher": []}}]}
    (e,) = service.normalize("prismhr", "employee", payload, PRISM).records
    assert e.extras["directDeposit"] == {"achStatus": "I"}


def test_prism_unknown_deposit_method_keeps_the_amount():
    payload = {
        "employee": [
            {
                "id": "E1",
                "directDeposit": {
                    "achVoucher": [
                        {
                            "transitNum": "1",
                            "accountNum": "123456789",
                            "method": "ZZ",
                            "amount": 12.5,
                        }
                    ]
                },
            }
        ]
    }
    (d,) = service.normalize("prismhr", "direct_deposit", payload, PRISM).records
    assert d.amount is None and d.percentage is None
    assert d.extras["amount"] == "12.5" or d.extras["amount"] == 12.5


def test_prism_voucher_keeps_earn_amount_and_a_differing_check_num():
    voucher = {
        "voucherId": "V",
        "employeeId": "E",
        "earnAmount": 10,
        "checkNumber": "1",
        "checkNum": "2",
    }
    result = service.normalize("prismhr", "payroll", {"payrollVouchers": [voucher]}, PRISM)
    (p,) = [r for r in result.records if r.record_type == "paycheck"]
    assert p.extras["earnAmount"] == 10 and p.extras["checkNum"] == "2"
    assert any(i.code == "conflicting_fields" for i in result.issues)


def test_unmapped_account_numbers_in_extras_are_scrubbed():
    """PrismHR hsa.hsaDepositSettings[].accountNum is not mapped, so it would sit in extras."""
    payload = {
        "employee": [
            {
                "id": "E1",
                "hsa": {
                    "hsaDepositSettings": [
                        {"transitNum": "111", "accountNum": "555443332221", "bankName": "B"}
                    ]
                },
            }
        ]
    }
    (e,) = service.normalize("prismhr", "employee", payload, PRISM).records
    assert "555443332221" not in e.model_dump_json()
    assert e.extras["hsa"]["hsaDepositSettings"][0]["accountNum"] == "[redacted]"
    assert e.extras["hsa"]["hsaDepositSettings"][0]["bankName"] == "B"
    assert any(p.endswith("accountNum") for p in e.provenance.redacted_paths)


def test_ukg_house_number_and_extra_managers_are_kept():
    profile = {
        "id": 1,
        "address": {
            "country": "NLD",
            "address_line_1": "Kade",
            "number": "12",
            "number_addition": "b",
        },
        "managers": [
            {"index": 2, "empl_ref": {"account_id": 20}},
            {"index": 1, "empl_ref": {"account_id": 10}},
        ],
    }
    (e,) = service.normalize("ukg-ready", "employee", {"employee": profile}, UKG).records
    assert e.address.line1 == "Kade" and e.address.house_number == "12 b"
    assert e.manager_id == "10"  # lowest index
    assert e.extras["other_managers"] == [{"index": 2, "empl_ref": {"account_id": 20}}]


def test_ukg_compensation_amount_is_not_dropped():
    def comp(row):
        payload = {"employee": {"id": 1}, "compensation_history": {"compensations": [row]}}
        (e,) = service.normalize("ukg-ready", "employee", payload, UKG).records
        return e

    both_differ = comp(
        {"effective_from": "2024-01-01", "amount": 40, "hourly_pay": 31.25, "amount_period": "HOUR"}
    )
    assert both_differ.compensation.hourly_rate == Decimal("31.25")
    assert both_differ.extras["compensation_history"][0]["amount"] == 40
    unknown_period = comp(
        {"effective_from": "2024-01-01", "amount": 99, "amount_period": "FORTNIGHT"}
    )
    assert unknown_period.compensation.hourly_rate is None
    assert unknown_period.extras["compensation_history"][0]["amount"] == 99
    assert unknown_period.extras["compensation_history"][0]["amount_period"] == "FORTNIGHT"


def test_ukg_compensation_of_another_employee_is_flagged():
    payload = {
        "employee": {"id": 1},
        "compensation_history": {
            "compensations": [
                {
                    "effective_from": "2024-01-01",
                    "amount": 1,
                    "amount_period": "YEAR",
                    "employee": {"account_id": 2},
                }
            ]
        },
    }
    result = service.normalize("ukg-ready", "employee", payload, UKG)
    assert any(i.code == "relationship_mismatch" for i in result.issues)


def test_ukg_national_id_type_is_kept_while_value_is_withheld():
    payload = {
        "employee": {
            "id": 1,
            "national_id_numbers": [{"type": "SIN", "value": "046454286", "primary": True}],
        }
    }
    (e,) = service.normalize("ukg-ready", "employee", payload, UKG).records
    assert "046454286" not in e.model_dump_json()
    assert e.extras["national_id_numbers"] == [{"type": "SIN", "primary": True}]


def test_isolved_override_address_not_used_for_paychecks_is_not_discarded():
    emp = {"id": 1, "overrideAddress": {"address1": "PO 1", "useForPaychecks": False}}
    (e,) = service.normalize(
        "isolved", "employee", {"results": [emp]}, {"tenant_id": "t", "company_id": "9"}
    ).records
    assert e.mailing_address is None
    assert e.extras["overrideAddress"] == {"address1": "PO 1", "useForPaychecks": False}
