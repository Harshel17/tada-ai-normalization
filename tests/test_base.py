"""M1: Src bookkeeping, parsers and canonical model rules."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from normalization.adapters.base import Collector, hash_account, last4, mask_account
from normalization.canonical.enums import Provider, Severity
from normalization.canonical.models import Employee, Paycheck, TaxLine


@pytest.fixture
def collector():
    return Collector(Provider.PRISMHR)


def src(collector, data):
    return collector.src(data, "$")


class TestParsers:
    def test_text_treats_null_empty_and_blank_as_none(self, collector):
        s = src(collector, {"a": None, "b": "", "c": "   ", "d": " x ", "e": 7})
        assert [s.text(k) for k in "abcde"] == [None, None, None, "x", "7"]

    def test_decimal_keeps_zero_and_scale(self, collector):
        s = src(collector, {"zero": 0, "zs": "0.00", "money": "12.50", "none": None, "empty": ""})
        assert s.dec("zero") == Decimal("0") and s.dec("zero") is not None
        assert str(s.dec("zs")) == "0.00"
        assert str(s.dec("money")) == "12.50"
        assert s.dec("none") is None and s.dec("empty") is None

    def test_decimal_never_goes_through_binary_float(self, collector):
        # 0.1 + 0.2 style artefacts must not appear when the JSON number is parsed as Decimal
        s = src(collector, {"a": Decimal("0.1"), "b": 0.1})
        assert s.dec("a") == Decimal("0.1")
        assert s.dec("b") == Decimal("0.1")  # float is stringified, not expanded

    def test_invalid_decimal_is_reported_and_kept_in_extras(self, collector):
        s = src(collector, {"amount": "abc", "ok": "1"})
        assert s.dec("amount") is None
        s.dec("ok")
        assert s.leftover() == {"amount": "abc"}
        assert [i.code for i in collector.issues] == ["invalid_decimal"]
        assert "abc" not in collector.issues[0].message  # values never go into messages

    @pytest.mark.parametrize("raw", [float("nan"), "NaN", "Infinity"])
    def test_non_finite_numbers_are_rejected(self, collector, raw):
        assert src(collector, {"x": raw}).dec("x") is None

    def test_day_accepts_date_and_datetime_strings(self, collector):
        s = src(collector, {"a": "2024-03-05", "b": "2024-03-05T00:00:00", "c": "nope", "d": ""})
        assert s.day("a") == date(2024, 3, 5)
        assert s.day("b") == date(2024, 3, 5)
        assert s.day("c") is None and s.day("d") is None
        assert [i.code for i in collector.issues] == ["invalid_date"]

    def test_boolean_understands_documented_forms(self, collector):
        s = src(collector, {"a": True, "b": "T", "c": "false", "d": "N", "e": "maybe", "f": None})
        assert [s.boolean(k) for k in "abcdef"] == [True, True, False, False, None, None]

    def test_integer_rejects_fractions(self, collector):
        s = src(collector, {"a": "3", "b": "3.5"})
        assert s.integer("a") == 3
        assert s.integer("b") is None


class TestLeftovers:
    def test_unread_keys_become_extras_and_nested_keys_are_tracked(self, collector):
        s = src(collector, {"a": 1, "b": {"x": 1, "y": 2}, "c": [{"k": 1, "z": 9}]})
        s.get("a")
        s.sub("b").get("x")
        s.items("c")[0].get("k")
        assert s.leftover() == {"b": {"y": 2}, "c": [{"z": 9}]}

    def test_secret_is_returned_but_never_kept(self, collector):
        s = src(collector, {"ssn": "123-45-6789", "name": "x"})
        assert s.secret("ssn") == "123-45-6789"
        s.get("name")
        assert s.leftover() == {}
        assert collector.redacted == ["$.ssn"]

    def test_links_are_navigation_not_data(self, collector):
        assert src(collector, {"links": [1], "_links": {"a": 1}, "x": 1}).leftover() == {"x": 1}

    def test_claimed_line_extras_are_not_repeated_on_the_parent(self, collector):
        s = src(collector, {"lines": [{"a": 1, "extra": 5}], "top": 1})
        line = s.items("lines")[0]
        line.get("a")
        assert line.leftover() == {"extra": 5}
        assert s.leftover() == {"top": 1}

    def test_wrong_type_is_reported_not_raised(self, collector):
        s = src(collector, {"obj": "text", "arr": {"a": 1}})
        assert s.sub("obj").keys() == []
        assert s.items("arr") == []
        assert {i.code for i in collector.issues} == {"unexpected_type"}


class TestSensitiveHelpers:
    def test_masking_keeps_only_last_four(self):
        assert mask_account("000123456789") == "****6789"
        assert mask_account("7788") == "****"  # four digits: nothing may be shown
        assert mask_account(None) is None
        assert last4("123-45-6789") == "6789"

    def test_account_hash_is_scoped_by_tenant(self):
        a = hash_account("tenant-a", "111", "999")
        assert a == hash_account("tenant-a", "111", "999")
        assert a != hash_account("tenant-b", "111", "999")
        assert hash_account("t", "1", None) is None


class TestCanonicalModels:
    base = {"provider": "prismhr", "tenant_id": "t", "company_id": "c", "source_id": "1"}

    def test_unknown_fields_are_rejected(self):
        with pytest.raises(ValidationError):
            Employee(**self.base, surprise="x")

    def test_money_is_decimal_and_serialises_losslessly(self):
        p = Paycheck(**self.base, employee_id="e", gross_pay="100.10", net_pay=Decimal("0.00"))
        dumped = p.model_dump(mode="json")
        assert dumped["gross_pay"] == "100.10"
        assert dumped["net_pay"] == "0.00"  # zero is a value, not null
        assert dumped["check_amount"] is None

    def test_tax_line_liability_may_be_unknown(self):
        assert TaxLine(line_no=1).liability is None

    def test_severity_enum(self):
        assert {s.value for s in Severity} == {"error", "warning", "info"}
