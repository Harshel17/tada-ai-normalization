"""Canonical enums.

Rule: an enum member exists only when a provider document gives that code a meaning.
Everything else stays in a `*_raw` string next to a null canonical field.
"""

from enum import StrEnum


class Provider(StrEnum):
    PRISMHR = "prismhr"
    ISOLVED = "isolved"
    UKG_READY = "ukg-ready"


class EntityType(StrEnum):
    COMPANY = "company"
    EMPLOYEE = "employee"
    DIRECT_DEPOSIT = "direct_deposit"
    DEDUCTION = "deduction"  # deduction *setup* (what will be deducted), not paycheck lines
    PAYROLL = "payroll"  # payroll runs + paychecks (actual results)


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class CompanyStatus(StrEnum):
    """PrismHR ClientMaster.statusClass (documented A/P/I/C/T)."""

    ACTIVE = "active"
    PENDING = "pending"
    INACTIVE = "inactive"
    CANCELLING = "cancelling"
    TERMINATED = "terminated"


class EmploymentStatus(StrEnum):
    """PrismHR employee statusClass (documented A/L/T). Other providers: raw only."""

    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    TERMINATED = "terminated"


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"


class PayType(StrEnum):
    """PrismHR compensation.payMethod: S, H, C, D."""

    SALARY = "salary"
    HOURLY = "hourly"
    COMMISSION = "commission"
    DRIVER = "driver"


class RatePeriod(StrEnum):
    """Unit a base rate is expressed in. Documented by UKG compensation history."""

    HOUR = "hour"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    PAY_PERIOD = "pay_period"


class DepositAccountType(StrEnum):
    CHECKING = "checking"  # PrismHR 'C' is "checking account or pay card"
    SAVINGS = "savings"
    INSTANT_PAY = "instant_pay"  # PrismHR 'IP'


class DepositStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PRENOTE = "prenote"  # PrismHR 'P' (Pre-notification)


class AllocationType(StrEnum):
    FIXED_AMOUNT = "fixed_amount"
    PERCENTAGE = "percentage"
    REMAINING_BALANCE = "remaining_balance"


class DeductionStatus(StrEnum):
    """PrismHR scheduledDeduction.status: A, I, O."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ONE_TIME = "one_time"


class PaycheckType(StrEnum):
    """PrismHR payroll voucher type: R, V, S, A, J."""

    REGULAR = "regular"
    VACATION = "vacation"
    SUPPLEMENTAL = "supplemental"
    ARREARS = "arrears"
    ADJUSTMENT = "adjustment"


class Liability(StrEnum):
    EMPLOYEE = "employee"
    EMPLOYER = "employer"
