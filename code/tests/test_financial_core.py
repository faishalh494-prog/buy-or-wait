"""
Focused unit tests for the deterministic financial core (Milestone M1A).
"""
import os
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from code.data_loader import DataLoader
from code.financial.state_builder import (
    build_user_financial_state,
    convert_event_currency,
    UserFinancialState,
)
from code.financial.recurrence import detect_recurring_items
from code.financial.simulator import (
    simulate_cashflow,
    compute_amount_safe_to_pay,
    compute_earliest_date_for_full_payment,
    run_m1a_core,
)
from code.models import (
    Direction,
    EventStatus,
    EventType,
    FinancialEvent,
    FinancialProfile,
    Flexibility,
    RecurringItem,
)


class TestFinancialCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = DataLoader()
        cls.loader.load_all()

    def test_current_balance_preservation(self):
        """Current balance must be preserved directly from profile without replaying settled events."""
        profile = self.loader.get_profile("user_01")
        self.assertIsNotNone(profile)
        req_date = date(2024, 3, 3)
        state = build_user_financial_state("user_01", req_date, self.loader)

        # Profile starting balance is 58481.1
        self.assertEqual(state.current_available_balance, profile.current_available_balance)
        self.assertEqual(state.current_available_balance, Decimal("58481.1"))

    def test_future_recurring_salary_projection(self):
        """Confirmed salary and recurring salary projection must match settlement dates and amounts."""
        req_date = date(2024, 3, 3)
        state = build_user_financial_state("user_01", req_date, self.loader)

        # Should have scheduled credit event_103 on 2024-03-15 for 23320
        self.assertEqual(len(state.scheduled_credits), 1)
        self.assertEqual(state.scheduled_credits[0].event_id, "event_103")
        self.assertEqual(state.scheduled_credits[0].amount, Decimal("23320"))
        self.assertEqual(state.scheduled_credits[0].settlement_date, date(2024, 3, 15))

        rec_items = detect_recurring_items(state)
        salary_rec = [r for r in rec_items if r.category == "salary" and r.direction == Direction.CREDIT]
        self.assertEqual(len(salary_rec), 1)
        self.assertEqual(salary_rec[0].amount, Decimal("23320"))
        self.assertEqual(salary_rec[0].day_of_month, 15)

        # In 90-day simulation, salaries should be credited on 2024-03-15, 2024-04-15, 2024-05-15
        sim = simulate_cashflow(state, rec_items, forecast_days=90)
        # Check that on 2024-03-15, the balance jumps by salary minus any expenses that day
        day_before = sim.daily_balances[date(2024, 3, 14)]
        day_salary = sim.daily_balances[date(2024, 3, 15)]
        self.assertGreater(day_salary, day_before)

    def test_recurring_expense_projection(self):
        """Recurring expenses (monthly bills and regular living expenses) must be detected and projected."""
        req_date = date(2024, 3, 3)
        state = build_user_financial_state("user_01", req_date, self.loader)
        rec_items = detect_recurring_items(state)

        cats = {r.category for r in rec_items if r.direction == Direction.DEBIT}
        expected_cats = {"rent", "utilities", "education", "debt_repayment", "music_subscription", "delivery_membership", "groceries", "transport", "dining"}
        self.assertTrue(expected_cats.issubset(cats), f"Missing categories: {expected_cats - cats}")

        # Rent should be on day 2 with exact amount 5148
        rent_item = [r for r in rec_items if r.category == "rent"][0]
        self.assertEqual(rent_item.amount, Decimal("5148"))
        self.assertEqual(rent_item.day_of_month, 2)

    def test_pending_debit_reservation(self):
        """Pending debits must be reserved and reduce available cash."""
        req_date = date(2024, 3, 3)
        state = build_user_financial_state("user_01", req_date, self.loader)

        # event_102 is pending fuel authorization of 567.6 settling on 2024-03-05 (future)
        self.assertEqual(len(state.pending_debits), 1)
        self.assertEqual(state.pending_debits[0].event_id, "event_102")
        self.assertEqual(state.pending_debits[0].amount, Decimal("567.6"))
        self.assertEqual(state.pending_debits[0].settlement_date, date(2024, 3, 5))

        # Test that overdue/today pending debits (settlement_date <= as_of_date) are deducted immediately
        # Mocking an event with settlement_date <= as_of_date:
        from unittest.mock import patch
        overdue_debit = FinancialEvent(
            event_id="event_overdue",
            user_id="user_01",
            event_type=EventType.EXPENSE,
            description="Overdue card authorization",
            category="groceries",
            direction=Direction.DEBIT,
            amount=Decimal("200.00"),
            currency="ZAR",
            event_date=date(2024, 3, 1),
            settlement_date=date(2024, 3, 2),  # Before req_date 2024-03-03
            status=EventStatus.PENDING,
            flexibility=Flexibility.FIXED,
        )
        with patch.object(self.loader, "get_events_for_user", return_value=[overdue_debit]):
            state_overdue = build_user_financial_state("user_01", req_date, self.loader)
            # Profile balance is 58481.1, overdue debit is 200.00 -> balance becomes 58281.1
            self.assertEqual(state_overdue.current_available_balance, Decimal("58481.1") - Decimal("200.00"))
            self.assertEqual(len(state_overdue.pending_debits), 0)

    def test_pending_credit_exclusion(self):
        """Pending credits (e.g. pending refunds) must NOT be counted as available money."""
        # Find a user with pending credit (e.g. user_77 has pending refund/shopping credit)
        pending_credits = [
            e for e in self.loader.events
            if e.status == EventStatus.PENDING and e.direction == Direction.CREDIT
        ]
        self.assertGreater(len(pending_credits), 0)
        sample_pc = pending_credits[0]

        state = build_user_financial_state(sample_pc.user_id, date(2025, 11, 1), self.loader)
        # Verify that sample_pc is NOT in scheduled_credits or pending_debits
        self.assertNotIn(sample_pc.event_id, [c.event_id for c in state.scheduled_credits])

        # Verify simulation does not receive this credit
        sim = simulate_cashflow(state, [], forecast_days=30)
        # The pending credit amount must not be added to daily balances
        self.assertNotIn(sample_pc.settlement_date, [dt for dt, bal in sim.daily_balances.items() if bal > state.current_available_balance])

    def test_failed_cancelled_event_exclusion(self):
        """Failed and cancelled events must be excluded from financial state."""
        # user_01 has event_100 (cancelled)
        ev100 = self.loader.get_event("event_100")
        self.assertIsNotNone(ev100)
        self.assertEqual(ev100.status, EventStatus.CANCELLED)

        state = build_user_financial_state("user_01", date(2024, 3, 3), self.loader)
        settled_ids = [e.event_id for e in state.settled_history]
        pending_ids = [e.event_id for e in state.pending_debits]
        scheduled_ids = [e.event_id for e in state.scheduled_debits + state.scheduled_credits]

        self.assertNotIn("event_100", settled_ids)
        self.assertNotIn("event_100", pending_ids)
        self.assertNotIn("event_100", scheduled_ids)

    def test_fx_conversion_using_supplied_rates(self):
        """Foreign currency events must be converted using fixed rates at settlement date."""
        # Find a foreign currency event
        fx_event = None
        for e in self.loader.events:
            prof = self.loader.get_profile(e.user_id)
            if prof and e.currency != prof.home_currency and e.settlement_date:
                fx_event = e
                break

        self.assertIsNotNone(fx_event)
        prof = self.loader.get_profile(fx_event.user_id)
        converted = convert_event_currency(fx_event, prof.home_currency, self.loader.exchange_rates)

        rate = self.loader.get_exchange_rate(fx_event.settlement_date, fx_event.currency, prof.home_currency)
        self.assertIsNotNone(rate)
        self.assertEqual(converted.currency, prof.home_currency)
        self.assertEqual(converted.amount, fx_event.amount * rate)

    def test_minimum_balance_guard(self):
        """Simulator must guard minimum_balance_to_keep and identify violations."""
        profile = self.loader.get_profile("user_01")
        state = build_user_financial_state("user_01", date(2024, 3, 3), self.loader)
        rec_items = detect_recurring_items(state)

        # If user attempts to pay an unaffordable large amount (e.g. 100,000)
        sim_huge = simulate_cashflow(
            state,
            rec_items,
            candidate_payments=[(date(2024, 3, 3), Decimal("100000"))],
        )
        self.assertFalse(sim_huge.balance_never_below_minimum)
        self.assertEqual(sim_huge.violation_date, date(2024, 3, 3))
        self.assertGreater(sim_huge.violation_amount, Decimal("0"))

        # Amount safe to pay must not allow dropping below minimum
        safe_amt = compute_amount_safe_to_pay(state, rec_items, requested_amount=Decimal("100000"))
        # Check simulation with safe_amt
        sim_safe = simulate_cashflow(
            state,
            rec_items,
            candidate_payments=[(date(2024, 3, 3), safe_amt)],
        )
        self.assertTrue(sim_safe.balance_never_below_minimum)

    def test_90_day_simulation_boundary(self):
        """Events beyond day 90 must not affect the 90-day simulation."""
        req_date = date(2024, 3, 3)
        state = build_user_financial_state("user_01", req_date, self.loader)
        rec_items = detect_recurring_items(state)

        # Baseline 90-day simulation
        sim_90 = simulate_cashflow(state, rec_items, forecast_days=90)
        self.assertIn(date(2024, 3, 3) + timedelta(days=90), sim_90.daily_balances)
        self.assertNotIn(date(2024, 3, 3) + timedelta(days=91), sim_90.daily_balances)

        # A payment on day 91 must not affect day 90 min balance
        payment_day_91 = date(2024, 3, 3) + timedelta(days=91)
        sim_with_late = simulate_cashflow(
            state,
            rec_items,
            forecast_days=90,
            candidate_payments=[(payment_day_91, Decimal("100000"))],
        )
        self.assertEqual(sim_90.min_balance_reached, sim_with_late.min_balance_reached)

    def test_blank_amount_not_interpreted_as_zero(self):
        """Events with blank amount in CSV must have amount=None, not Decimal(0)."""
        # Find events with blank amounts (the 16 image-linked events)
        blank_amount_events = [e for e in self.loader.events if e.amount is None]
        self.assertEqual(len(blank_amount_events), 16)
        for e in blank_amount_events:
            self.assertIsNone(e.amount)
            self.assertNotEqual(e.amount, Decimal("0"))

    def test_regression_request_01_user_01(self):
        """MANDATORY REGRESSION CHECK: request_01 / user_01 must produce amount_safe_to_pay == 25256."""
        s_req = self.loader.sample_requests["request_01"]
        req_date = date.fromisoformat(s_req["request_date"])
        req_amt = Decimal(s_req["requested_amount"])

        state = build_user_financial_state("user_01", req_date, self.loader)
        rec_items = detect_recurring_items(state)
        result = run_m1a_core(state, rec_items, req_amt)

        self.assertEqual(result.amount_safe_to_pay, Decimal("25256.00"))
        self.assertEqual(result.earliest_date_for_full_payment, req_date)

    def test_final_employer_payroll_does_not_become_recurring_income(self):
        """A final payroll is a settled historical fact, not forecast income."""
        state = build_user_financial_state("user_05", date(2025, 11, 6), self.loader)
        recurring = detect_recurring_items(state)
        self.assertFalse(any(item.direction == Direction.CREDIT for item in recurring))

    def test_same_day_unsettled_recurring_simulated(self):
        """Must-Fix #1: Unsettled recurring item on as_of must be simulated; settled must not be duplicated."""
        as_of = date(2024, 6, 15)
        profile = FinancialProfile(
            user_id="test_user", home_currency="USD",
            current_available_balance=Decimal("1000"), minimum_balance_to_keep=Decimal("200"),
            financial_priorities=[], expense_categories_to_protect=[],
            expense_categories_user_is_willing_to_reduce=[], expense_categories_user_is_willing_to_stop=[],
            payment_methods_user_will_consider=["full_payment"], max_installment_months=12,
        )
        # 1. Unsettled recurring item on as_of (day 15)
        state_unsettled = UserFinancialState(
            user_id="test_user",
            home_currency="USD",
            as_of_date=as_of,
            current_available_balance=Decimal("1000"),
            minimum_balance_to_keep=Decimal("200"),
            profile=profile,
            settled_history=[],
            pending_debits=[],
            scheduled_debits=[],
            scheduled_credits=[],
        )
        rec_item = RecurringItem(
            category="utilities",
            description="Electric bill",
            direction=Direction.DEBIT,
            amount=Decimal("150"),
            frequency_days=30,
            day_of_month=15,
            last_date=date(2024, 5, 15),
            source_event_id="ev_util_hist",
        )
        sim_unsettled = simulate_cashflow(state_unsettled, [rec_item], forecast_days=30)
        # Should simulate on day 0: balance becomes 1000 - 150 = 850
        self.assertEqual(sim_unsettled.daily_balances[as_of], Decimal("850"))

        # 2. If it already settled on as_of, it must NOT be duplicated
        settled_ev = FinancialEvent(
            event_id="ev_util_today",
            user_id="test_user",
            event_type=EventType.EXPENSE,
            description="Electric bill",
            category="utilities",
            direction=Direction.DEBIT,
            amount=Decimal("150"),
            settlement_date=as_of,
            status=EventStatus.SETTLED,
        )
        state_settled = UserFinancialState(
            user_id="test_user",
            home_currency="USD",
            as_of_date=as_of,
            current_available_balance=Decimal("850"),  # already reflects settled payment
            minimum_balance_to_keep=Decimal("200"),
            profile=profile,
            settled_history=[settled_ev],
            pending_debits=[],
            scheduled_debits=[],
            scheduled_credits=[],
        )
        sim_settled = simulate_cashflow(state_settled, [rec_item], forecast_days=30)
        # Should NOT deduct again on day 0: balance stays 850
        self.assertEqual(sim_settled.daily_balances[as_of], Decimal("850"))

    def test_distinct_same_category_obligations_both_survive(self):
        """Must-Fix #2: Two distinct obligations sharing category & date must both survive deduplication."""
        as_of = date(2024, 6, 1)
        target_date = date(2024, 6, 15)
        profile = FinancialProfile(
            user_id="test_user", home_currency="USD",
            current_available_balance=Decimal("500.00"), minimum_balance_to_keep=Decimal("100.00"),
            financial_priorities=[], expense_categories_to_protect=[],
            expense_categories_user_is_willing_to_reduce=[], expense_categories_user_is_willing_to_stop=[],
            payment_methods_user_will_consider=["full_payment"], max_installment_months=12,
        )
        # Explicit scheduled Netflix subscription
        sched_netflix = FinancialEvent(
            event_id="ev_netflix_sched",
            user_id="test_user",
            event_type=EventType.SUBSCRIPTION,
            description="Netflix Subscription",
            category="subscription",
            direction=Direction.DEBIT,
            amount=Decimal("15.00"),
            settlement_date=target_date,
            status=EventStatus.SCHEDULED,
        )
        # Recurring Spotify subscription (distinct obligation)
        rec_spotify = RecurringItem(
            category="subscription",
            description="Spotify Music",
            direction=Direction.DEBIT,
            amount=Decimal("10.00"),
            frequency_days=30,
            day_of_month=15,
            last_date=date(2024, 5, 15),
            source_event_id="ev_spotify_hist",
        )
        state = UserFinancialState(
            user_id="test_user",
            home_currency="USD",
            as_of_date=as_of,
            current_available_balance=Decimal("500.00"),
            minimum_balance_to_keep=Decimal("100.00"),
            profile=profile,
            settled_history=[],
            pending_debits=[],
            scheduled_debits=[sched_netflix],
            scheduled_credits=[],
        )
        sim = simulate_cashflow(state, [rec_spotify], forecast_days=30)
        # Both Netflix ($15) and Spotify ($10) must be deducted on target_date
        # Starting 500 - 15 - 10 = 475
        self.assertEqual(sim.daily_balances[target_date], Decimal("475.00"))

        # True duplicate test: if scheduled event is for Spotify, recurring Spotify is suppressed
        sched_spotify = FinancialEvent(
            event_id="ev_spotify_sched",
            user_id="test_user",
            event_type=EventType.SUBSCRIPTION,
            description="Spotify Music",
            category="subscription",
            direction=Direction.DEBIT,
            amount=Decimal("10.00"),
            settlement_date=target_date,
            status=EventStatus.SCHEDULED,
            linked_event_id="ev_spotify_hist",
        )
        state_dup = UserFinancialState(
            user_id="test_user",
            home_currency="USD",
            as_of_date=as_of,
            current_available_balance=Decimal("500.00"),
            minimum_balance_to_keep=Decimal("100.00"),
            profile=profile,
            settled_history=[],
            pending_debits=[],
            scheduled_debits=[sched_spotify],
            scheduled_credits=[],
        )
        sim_dup = simulate_cashflow(state_dup, [rec_spotify], forecast_days=30)
        # Only deducted once: 500 - 10 = 490
        self.assertEqual(sim_dup.daily_balances[target_date], Decimal("490.00"))

    def test_multiple_independent_salary_streams(self):
        """Must-Fix #3: Multiple independent recurring salary streams are preserved separately."""
        # user_13 has two distinct monthly salary streams
        state = build_user_financial_state("user_13", date(2024, 3, 7), self.loader)
        recurring = detect_recurring_items(state)
        salaries = [item for item in recurring if item.category == "salary"]
        self.assertGreaterEqual(len(salaries), 2)
        # Verify streams are separate and distinct
        amounts = [s.amount for s in salaries]
        self.assertNotEqual(amounts[0], amounts[1])

    def test_generic_income_not_recurring_salary(self):
        """Must-Fix #4: Irregular generic income is not misclassified as recurring salary."""
        as_of = date(2024, 6, 1)
        profile = FinancialProfile(
            user_id="test_user", home_currency="USD",
            current_available_balance=Decimal("1000.00"), minimum_balance_to_keep=Decimal("200.00"),
            financial_priorities=[], expense_categories_to_protect=[],
            expense_categories_user_is_willing_to_reduce=[], expense_categories_user_is_willing_to_stop=[],
            payment_methods_user_will_consider=["full_payment"], max_installment_months=12,
        )
        # Create history with irregular cashbacks / dividends categorized as income
        history = [
            FinancialEvent(
                event_id=f"ev_inc_{i}",
                user_id="test_user",
                event_type=EventType.INCOME,
                description=f"Irregular cashback reward {i}",
                category="cashback",
                direction=Direction.CREDIT,
                amount=Decimal("25.00"),
                settlement_date=date(2024, 1 + i, 10 + i * 3),
                status=EventStatus.SETTLED,
            )
            for i in range(3)
        ]
        state = UserFinancialState(
            user_id="test_user",
            home_currency="USD",
            as_of_date=as_of,
            current_available_balance=Decimal("1000.00"),
            minimum_balance_to_keep=Decimal("200.00"),
            profile=profile,
            settled_history=history,
            pending_debits=[],
            scheduled_debits=[],
            scheduled_credits=[],
        )
        recurring = detect_recurring_items(state)
        salary_streams = [item for item in recurring if item.category == "salary"]
        self.assertEqual(len(salary_streams), 0)

    def test_user_25_foreign_currency_salary_conversion(self):
        """Currency conversion: user_25 USD 1800 foreign cash event converts to ~28.5M IDR."""
        # Find raw USD 1800 salary event for user_25
        events = self.loader.get_events_for_user("user_25")
        usd_sal = next(e for e in events if e.currency == "USD" and e.amount == Decimal("1800"))
        converted = convert_event_currency(usd_sal, "IDR", self.loader.exchange_rates)
        self.assertEqual(converted.currency, "IDR")
        # Rate on settlement date ~15833.33 -> 28,499,994 IDR
        self.assertGreater(converted.amount, Decimal("28000000"))
        self.assertLess(converted.amount, Decimal("29000000"))


if __name__ == "__main__":
    unittest.main()
