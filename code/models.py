"""
Data models for the financial decision engine.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date, datetime
from typing import Optional, List
from enum import Enum


class EventType(str, Enum):
    EXPENSE = "expense"
    SUBSCRIPTION = "subscription"
    INCOME = "income"
    DEBT_PAYMENT = "debt_payment"
    INVESTMENT_PURCHASE = "investment_purchase"
    REFUND = "refund"
    INVESTMENT_VALUATION = "investment_valuation"
    INVESTMENT_SALE = "investment_sale"


class EventStatus(str, Enum):
    SETTLED = "settled"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNREALIZED = "unrealized"


class Direction(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"
    NON_CASH = "non_cash"


class Flexibility(str, Enum):
    FIXED = "fixed"
    REDUCIBLE = "reducible"
    STOPPABLE = "stoppable"
    REDUCIBLE_OR_STOPPABLE = "reducible_or_stoppable"


class AffordabilityStatus(str, Enum):
    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class PaymentMethod(str, Enum):
    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: EventType
    description: str
    category: str
    direction: Direction
    amount: Optional[Decimal] = None
    currency: str = "USD"
    event_date: Optional[date] = None
    settlement_date: Optional[date] = None
    status: EventStatus = EventStatus.SETTLED
    linked_event_id: Optional[str] = None
    flexibility: Flexibility = Flexibility.FIXED
    minimum_allowed_amount: Optional[Decimal] = None

    @property
    def is_income(self) -> bool:
        return self.direction == Direction.CREDIT

    @property
    def is_expense(self) -> bool:
        return self.direction == Direction.DEBIT

    @property
    def is_cash(self) -> bool:
        return self.direction != Direction.NON_CASH

    @property
    def is_recurring_candidate(self) -> bool:
        """Candidate for recurrence detection based on type/category."""
        return self.event_type in (EventType.EXPENSE, EventType.SUBSCRIPTION, EventType.INCOME, EventType.DEBT_PAYMENT)

    @property
    def is_fixed_recurring(self) -> bool:
        """Fixed recurring expenses like rent, utilities, subscriptions."""
        return (self.flexibility == Flexibility.FIXED and
                self.category in ('rent', 'utilities', 'music_subscription', 'cloud_storage', 'streaming',
                                  'delivery_membership', 'gym', 'insurance', 'debt_repayment', 'education'))

    @property
    def is_variable_recurring(self) -> bool:
        """Variable recurring expenses like groceries, transport, dining."""
        return (self.flexibility in (Flexibility.FIXED, Flexibility.REDUCIBLE, Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE) and
                self.category in ('groceries', 'transport', 'dining', 'shopping', 'entertainment', 'healthcare', 'family_support'))


@dataclass
class FinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: List[str]
    expense_categories_to_protect: List[str]
    expense_categories_user_is_willing_to_reduce: List[str]
    expense_categories_user_is_willing_to_stop: List[str]
    payment_methods_user_will_consider: List[str]
    max_installment_months: Optional[int]

    def is_category_protected(self, category: str) -> bool:
        return category in self.expense_categories_to_protect

    def is_category_reducible(self, category: str) -> bool:
        return category in self.expense_categories_user_is_willing_to_reduce

    def is_category_stoppable(self, category: str) -> bool:
        return category in self.expense_categories_user_is_willing_to_stop

    def considers_payment_method(self, method: str) -> bool:
        return method in self.payment_methods_user_will_consider


@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass
class ExchangeRate:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass
class Message:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: datetime
    source_type: str
    message_text: str


@dataclass
class ImageRecord:
    image_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]


@dataclass
class RecurringItem:
    category: str
    description: str
    direction: Direction
    amount: Decimal
    frequency_days: int  # 7 for weekly, 14 for bi-weekly, 30 for monthly
    day_of_month: Optional[int] = None
    last_date: Optional[date] = None
    source_event_id: Optional[str] = None
    flexibility: Flexibility = Flexibility.FIXED
    minimum_allowed_amount: Optional[Decimal] = None


@dataclass
class ForecastEvent:
    """A projected event in the 90-day forecast."""
    date: date
    category: str
    amount: Decimal
    is_income: bool
    is_recurring: bool
    source_event_id: Optional[str] = None
    is_optional_change: bool = False  # True if from spending_changes_needed


@dataclass
class SimulationResult:
    """Result of a single simulation run."""
    final_balance: Decimal
    min_balance_reached: Decimal
    balance_never_below_minimum: bool
    daily_balances: dict = field(default_factory=dict)
    violation_date: Optional[date] = None
    violation_amount: Optional[Decimal] = None


@dataclass
class M1AResult:
    """M1A core financial calculation results."""
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: Optional[date]
    simulation_debug: dict = field(default_factory=dict)