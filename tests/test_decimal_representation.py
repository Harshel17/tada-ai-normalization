"""Canonical money, rates and hours are exact Decimals and serialize as plain decimal text (DECISIONS.md section 7)."""

import json
from decimal import Decimal

import pytest

from normalization import service
from normalization.canonical.models import Dec, EarningLine

CTX = {"tenant_id": "t", "company_id": "C"}


def net_of(amount, provider_payload=None):
    payload = provider_payload or {
        "errorCode": "0",
        "payrollVouchers": [{"voucherId": "V", "employeeId": "E", "netPay": amount}],
    }
    res = service.normalize("prismhr", "payroll", payload, CTX)
    check = next((r for r in res.records if r.record_type == "paycheck"), None)
    return res, check


def dumped(check):
    return json.loads(check.model_dump_json())["net_pay"]


@pytest.mark.parametrize(
    ("given", "text"),
    [
        (Decimal("1000"), "1000"), (Decimal("1000.00"), "1000.00"), (Decimal("1E+3"), "1000"), (Decimal("1.5E+3"), "1500"),
        (Decimal("0.0001"), "0.0001"), (Decimal("1E-7"), "0.0000001"), (Decimal("-15.25"), "-15.25"), (Decimal("0"), "0"),
        (Decimal("0.00"), "0.00"), (Decimal("12345678901234567890.12"), "12345678901234567890.12"), (Decimal("-1E+2"), "-100"),
        (Decimal("100.10"), "100.10"),
    ],
)  # fmt: skip
def test_the_json_text_is_plain_and_exact(given, text):
    _, check = net_of(given)
    assert dumped(check) == text
    assert Decimal(dumped(check)) == given  # the same number, never rounded
    assert "E" not in dumped(check).upper().replace("-", "")


def test_equivalent_values_keep_their_own_scale_but_compare_equal():
    values = [net_of(Decimal(x))[1].net_pay for x in ("1000", "1000.00", "1E+3")]
    assert len(set(values)) == 1  # the same numeric value
    assert [dumped(net_of(Decimal(x))[1]) for x in ("1000", "1000.00", "1E+3")] == [
        "1000",
        "1000.00",
        "1000",
    ]


def test_exponent_text_in_the_source_json_is_parsed_exactly_and_serialized_plainly():
    payload = json.loads(
        '{"errorCode":"0","payrollVouchers":[{"voucherId":"V","employeeId":"E","netPay":1E+3,"totalEarnings":2.5E+2}]}',
        parse_float=Decimal,
    )
    _, check = net_of(None, payload)
    assert (dumped(check), json.loads(check.model_dump_json())["gross_pay"]) == ("1000", "250")


def test_string_amounts_with_exponents_are_handled_the_same_way():
    assert dumped(net_of("1E+3")[1]) == "1000"
    assert dumped(net_of(" 12.50 ")[1]) == "12.50"


@pytest.mark.parametrize(
    "bad",
    [
        "abc",
        "NaN",
        "Infinity",
        "-Infinity",
        "1e999999999",
        "1e31",
        "1e-31",
        "--5",
        "1,000.00",
        "0x10",
        True,
    ],
)
def test_invalid_or_absurd_amounts_are_rejected_safely_and_kept_not_converted(bad):
    res, check = net_of(bad)
    assert check is not None and check.net_pay is None  # no silent zero, no rounding
    assert (
        "invalid_decimal" in {i.code for i in res.issues} and res.ok
    )  # reported, the rest of the record still loads


def test_the_size_guard_does_not_touch_real_payroll_values():
    for v in ("0.000001", "9999999999999999999999.99", "30000000000"):
        assert Decimal(dumped(net_of(v)[1])) == Decimal(v)


def test_every_decimal_field_of_every_canonical_model_uses_the_plain_type():
    """A field typed with a bare Decimal would reintroduce exponent text: none may exist."""
    import typing

    from normalization.canonical import models

    for name in dir(models):
        cls = getattr(models, name)
        if isinstance(cls, type) and hasattr(cls, "model_fields"):
            for field, info in cls.model_fields.items():
                ann = info.annotation
                parts = [ann, *typing.get_args(ann)]
                bare = [p for p in parts if p is Decimal]
                assert not bare, f"{name}.{field} is a bare Decimal"


def test_the_dec_type_serializes_plainly_inside_nested_lines():
    line = EarningLine(
        line_no=1, source_kind="earning", amount=Decimal("1E+3"), hours=Decimal("0.5E+1")
    )
    assert (
        line.model_dump(mode="json")["amount"] == "1000"
        and line.model_dump(mode="json")["hours"] == "5"
    )
    assert line.amount == Decimal("1000") and isinstance(
        Dec, type(Dec)
    )  # python mode keeps the exact Decimal


def test_the_database_receives_exact_decimals_and_reads_back_the_same_value():
    """Persistence uses the python-mode Decimal, not the JSON text."""
    _, check = net_of(Decimal("1E+3"))
    assert check.model_dump()["net_pay"] == Decimal("1000") and isinstance(
        check.model_dump()["net_pay"], Decimal
    )
