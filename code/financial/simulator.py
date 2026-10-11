"""
90-day simulation engine for projecting cashflow and evaluating financial safety.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from code.financial.recurrence import _next_month_date
from code.financial.state_builder import UserFinancialState
from code.models import Direction, RecurringItem, SimulationResult, M1AResult, FinancialEvent


def _is_same_obligation(ev: FinancialEvent, item: RecurringItem, target_date: date) -> bool:
    """
    Determine if an explicit event represents the same underlying financial obligation
    as a recurring item instance on target_date.
    Uses linkage, lifecycle, direction, category, and description identity (Must-Fix #2).
    """
    if ev.direction != item.direction:
        return False
    if ev.category != item.category:
        return False

    ev_date = ev.settlement_date or ev.event_date
    if not ev_date:
        return False

    # Date check: explicit event date must align with target_date cycle
    if item.day_of_month is not None:
        # Monthly recurring item: check same month/year or within 5 days
        if not (ev_date.year == target_date.year and ev_date.month == target_date.month):
            if abs((ev_date - target_date).days) > 5:
                return False
    else:
        # Interval-based item: must be within 1 day
        if abs((ev_date - target_date).days) > 1:
            return False

    # Check linkage
    if ev.linked_event_id and item.source_event_id and ev.linked_event_id == item.source_event_id:
        return True
    if ev.event_id and item.source_event_id and ev.event_id == item.source_event_id:
        return True

    # Check description identity
    ev_desc = (ev.description or "").strip().lower()
    item_desc = (item.description or "").strip().lower()

    if ev_desc and item_desc:
        if ev_desc == item_desc or ev_desc in item_desc or item_desc in ev_desc:
            return True
        # If both have distinct descriptions and neither contains the other,
        # they represent distinct obligations in the same category (e.g., Netflix vs Spotify)
        return False

    # If description is absent or generic, category + date match indicates same obligation
    return True


def simulate_cashflow(
    state: UserFinancialState,
    recurring_items: List[RecurringItem],
    forecast_days: int = 90,
    candidate_payments: Optional[List[Tuple[date, Decimal]]] = None,
) -> SimulationResult:
    """
    Simulate the next 90 days of cashflow from current available balance.
    - Projects confirmed scheduled future events.
    - Projects supported recurring inflows and outflows.
    - Applies candidate payments (if testing a payment plan or purchase).
    - Enforces minimum_balance_to_keep at every day.
    """
    as_of = state.as_of_date
    end_date = as_of + timedelta(days=forecast_days)
    daily_flows: Dict[date, Decimal] = defaultdict(Decimal)

    # 1. Place explicit known future events from state
    # Keep track of explicit events to avoid double-counting with recurring items (Must-Fix #2)
    explicit_events: List[FinancialEvent] = []

    for pe in state.pending_debits:
        if pe.amount is not None:
            dt = pe.settlement_date or as_of
            if dt <= as_of:
                daily_flows[as_of] -= pe.amount
                explicit_events.append(pe)
            elif dt <= end_date:
                daily_flows[dt] -= pe.amount
                explicit_events.append(pe)

    for se in state.scheduled_debits:
        if se.settlement_date and as_of <= se.settlement_date <= end_date and se.amount is not None:
            daily_flows[se.settlement_date] -= se.amount
            explicit_events.append(se)

    for sc in state.scheduled_credits:
        if sc.settlement_date and as_of <= sc.settlement_date <= end_date and sc.amount is not None:
            daily_flows[sc.settlement_date] += sc.amount
            explicit_events.append(sc)

    # 2. Project recurring items
    for item in recurring_items:
        if item.day_of_month is not None:
            # Monthly recurring item
            curr_y = as_of.year
            curr_m = as_of.month

            for month_offset in range(5):
                target_m = curr_m + month_offset
                target_y = curr_y + (target_m - 1) // 12
                target_m = ((target_m - 1) % 12) + 1

                target_date = _next_month_date(target_y, target_m, item.day_of_month)
                if as_of <= target_date <= end_date:
                    # Check if this item is covered by an explicit event (Must-Fix #2)
                    if any(_is_same_obligation(ev, item, target_date) for ev in explicit_events):
                        continue

                    # If target_date == as_of, check if already settled in history (Must-Fix #1)
                    if target_date == as_of:
                        already_settled = any(
                            _is_same_obligation(ev, item, as_of)
                            for ev in state.settled_history
                            if (ev.settlement_date or ev.event_date) == as_of
                        )
                        if already_settled:
                            continue

                    if item.direction == Direction.CREDIT:
                        daily_flows[target_date] += item.amount
                    else:
                        daily_flows[target_date] -= item.amount

        else:
            # Interval-based item (weekly/bi-weekly living expenses: groceries, transport, dining)
            if item.last_date:
                curr_d = item.last_date + timedelta(days=item.frequency_days)
                while curr_d <= end_date:
                    if curr_d >= as_of:
                        covered = any(_is_same_obligation(ev, item, curr_d) for ev in explicit_events)
                        settled_today = False
                        if curr_d == as_of:
                            settled_today = any(
                                _is_same_obligation(ev, item, as_of)
                                for ev in state.settled_history
                                if (ev.settlement_date or ev.event_date) == as_of
                            )
                        if not covered and not settled_today:
                            if item.direction == Direction.CREDIT:
                                daily_flows[curr_d] += item.amount
                            else:
                                daily_flows[curr_d] -= item.amount
                    curr_d += timedelta(days=item.frequency_days)

    # 3. Add candidate payments (if evaluating a proposed purchase or plan)
    if candidate_payments:
        for p_date, p_amt in candidate_payments:
            if as_of <= p_date <= end_date:
                daily_flows[p_date] -= p_amt

    # 4. Step day-by-day and evaluate balance
    balance = state.current_available_balance
    min_seen = balance
    violation_date: Optional[date] = None
    violation_amt: Optional[Decimal] = None
    daily_balances: Dict[date, Decimal] = {as_of: balance}

    # Check day 0 immediately if payment was today
    if as_of in daily_flows:
        balance += daily_flows[as_of]
        daily_balances[as_of] = balance
        if balance < min_seen:
            min_seen = balance
        if balance < state.minimum_balance_to_keep:
            violation_date = as_of
            violation_amt = state.minimum_balance_to_keep - balance

    for offset in range(1, forecast_days + 1):
        curr_dt = as_of + timedelta(days=offset)
        if curr_dt in daily_flows:
            balance += daily_flows[curr_dt]
        daily_balances[curr_dt] = balance

        if balance < min_seen:
            min_seen = balance

        if balance < state.minimum_balance_to_keep and violation_date is None:
            violation_date = curr_dt
            violation_amt = state.minimum_balance_to_keep - balance

    return SimulationResult(
        final_balance=balance,
        min_balance_reached=min_seen,
        balance_never_below_minimum=(min_seen >= state.minimum_balance_to_keep),
        daily_balances=daily_balances,
        violation_date=violation_date,
        violation_amount=violation_amt,
    )


def compute_amount_safe_to_pay(
    state: UserFinancialState,
    recurring_items: List[RecurringItem],
    requested_amount: Decimal,
    forecast_days: int = 90,
) -> Decimal:
    """
    Calculate amount_safe_to_pay:
    The largest amount the user can safely pay on request_date before optional spending changes,
    while maintaining their minimum balance throughout the 90-day forecast.
    Capped at requested_amount, and >= 0.
    """
    baseline = simulate_cashflow(state, recurring_items, forecast_days=forecast_days)
    max_safe = baseline.min_balance_reached - state.minimum_balance_to_keep

    if max_safe <= Decimal(0):
        return Decimal(0)

    safe = min(requested_amount, max_safe)
    # Round to 2 decimal places
    return safe.quantize(Decimal("0.01"))


def compute_earliest_date_for_full_payment(
    state: UserFinancialState,
    recurring_items: List[RecurringItem],
    requested_amount: Decimal,
    forecast_days: int = 90,
) -> Optional[date]:
    """
    Calculate earliest_date_for_full_payment:
    The first conservative projected date for one safe full payment without spending changes.
    Equals request_date if affordable now.
    Returns None if no date within the forecast period is safe.
    """
    as_of = state.as_of_date

    # 1. Check if safe on request_date
    res_today = simulate_cashflow(
        state,
        recurring_items,
        forecast_days=forecast_days,
        candidate_payments=[(as_of, requested_amount)],
    )
    if res_today.balance_never_below_minimum:
        return as_of

    # 2. Scan forward through each subsequent day
    for offset in range(1, forecast_days + 1):
        test_date = as_of + timedelta(days=offset)
        res = simulate_cashflow(
            state,
            recurring_items,
            forecast_days=forecast_days,
            candidate_payments=[(test_date, requested_amount)],
        )
        if res.balance_never_below_minimum:
            return test_date

    return None


def run_m1a_core(
    state: UserFinancialState,
    recurring_items: List[RecurringItem],
    requested_amount: Decimal,
    forecast_days: int = 90,
) -> M1AResult:
    """
    Run M1A core financial calculations for a request.
    """
    safe_amt = compute_amount_safe_to_pay(
        state,
        recurring_items,
        requested_amount,
        forecast_days=forecast_days,
    )
    earliest_date = compute_earliest_date_for_full_payment(
        state,
        recurring_items,
        requested_amount,
        forecast_days=forecast_days,
    )

    return M1AResult(
        amount_safe_to_pay=safe_amt,
        earliest_date_for_full_payment=earliest_date,
    )
