"""
Recurrence detection module: Detects recurring income and expenses from historical evidence.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import statistics
from typing import Dict, List, Optional, Tuple

from code.financial.state_builder import UserFinancialState
from code.models import Direction, EventType, Flexibility, RecurringItem


def _next_month_date(base_year: int, base_month: int, target_day: int) -> date:
    """Create a date for the given year/month, clamping the day if month is shorter."""
    max_day = calendar.monthrange(base_year, base_month)[1]
    day = min(target_day, max_day)
    return date(base_year, base_month, day)


def detect_recurring_items(state: UserFinancialState) -> List[RecurringItem]:
    """
    Detect recurring income and recurring expenses from historical settled events and scheduled facts.
    Conservative:
    - Only detects recurrence when repeated historical evidence and timing consistency support it.
    - Uses confirmed scheduled salary as primary anchor for income when present.
    - Excludes one-off events (bonuses, refunds, non-cash, investment sales).
    """
    recurring: List[RecurringItem] = []
    settled = state.settled_history

    # 1. Detect salary / recurring income. Each explicit future salary is a
    # distinct stream; a last/final payroll is a fact, not recurrence evidence.
    confirmed_salary_events = [sc for sc in state.scheduled_credits
        if (sc.category == "salary" or "salary" in sc.description.lower() or "payroll" in sc.description.lower())
        and not any(w in sc.description.lower() for w in ("final", "last payroll", "severance"))]

    covered_days = set()
    covered_descs = set()

    # Look at historical salary/payroll events with explicit salary category or payroll semantics (Must-Fix #4)
    hist_salaries = [
        e for e in settled
        if e.direction == Direction.CREDIT and e.amount is not None
        and (
            e.category == "salary"
            or any(w in e.description.lower() for w in ("salary", "payroll", "wage", "stipend", "earnings", "contract payment", "invoice payment", "milestone payment", "retainer"))
        )
    ]
    # Filter out one-off bonuses, arrears, commissions, etc.
    regular_salaries = [
        e for e in hist_salaries
        if not any(w in e.description.lower() for w in [
            "bonus", "arrears", "one-time", "reimbursement", "final employer payroll",
            "final payroll", "last payroll", "employment ceased", "severance", "commission",
            "refund", "dividend", "lottery", "cashback"
        ])
    ]
    # A final payroll explicitly ends the preceding cadence. Earlier payrolls
    # remain historical evidence, but cannot justify invented future income.
    latest_salary = max(hist_salaries, key=lambda e: e.settlement_date or e.event_date) if hist_salaries else None
    if latest_salary and any(w in latest_salary.description.lower() for w in (
        "final employer payroll", "final payroll", "last payroll", "employment ceased", "severance",
    )):
        regular_salaries = []

    amended_salary_events = [e for e in regular_salaries if e.description.lower().startswith("[amended payroll]")]
    if amended_salary_events:
        anchor = max(amended_salary_events, key=lambda e: e.settlement_date or e.event_date)
        anchor_date = anchor.settlement_date or anchor.event_date
        recurring.append(RecurringItem(
            category="salary", description=anchor.description, direction=Direction.CREDIT,
            amount=anchor.amount, frequency_days=30, day_of_month=anchor_date.day,
            last_date=anchor_date, source_event_id=anchor.event_id, flexibility=Flexibility.FIXED))
        covered_days.add(anchor_date.day)
        covered_descs.add(anchor.description.lower())

    if confirmed_salary_events:
        for confirmed_salary_ev in confirmed_salary_events:
            if confirmed_salary_ev.amount is None or not confirmed_salary_ev.settlement_date:
                continue
            recurring.append(RecurringItem(
                category="salary", description=confirmed_salary_ev.description,
                direction=Direction.CREDIT, amount=confirmed_salary_ev.amount,
                frequency_days=30, day_of_month=confirmed_salary_ev.settlement_date.day,
                last_date=confirmed_salary_ev.settlement_date, source_event_id=confirmed_salary_ev.event_id,
                flexibility=Flexibility.FIXED))
            covered_days.add(confirmed_salary_ev.settlement_date.day)
            covered_descs.add(confirmed_salary_ev.description.lower())

    # Detect independent salary streams from history (Must-Fix #3)
    if regular_salaries:
        by_desc = defaultdict(list)
        for e in regular_salaries:
            by_desc[e.description.strip()].append(e)

        named_streams = [elist for elist in by_desc.values() if len(elist) >= 2]
        if len(named_streams) > 1 and all(len(el) >= 2 for el in named_streams):
            for elist in named_streams:
                dates = sorted([e.settlement_date or e.event_date for e in elist])
                deltas = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
                med_delta = statistics.median(deltas)
                if 25 <= med_delta <= 35:
                    target_day = int(round(statistics.median([d.day for d in dates])))
                    desc_key = elist[0].description.strip().lower()
                    if target_day not in covered_days and desc_key not in covered_descs:
                        amounts = [e.amount for e in elist]
                        base_amount = statistics.median(amounts)
                        recurring.append(RecurringItem(
                            category="salary", description=elist[0].description, direction=Direction.CREDIT,
                            amount=base_amount, frequency_days=30, day_of_month=target_day,
                            last_date=dates[-1], source_event_id=elist[-1].event_id, flexibility=Flexibility.FIXED
                        ))
                        covered_days.add(target_day)
                        covered_descs.add(desc_key)
        else:
            dates = sorted([e.settlement_date or e.event_date for e in regular_salaries])
            if len(dates) >= 2:
                deltas = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
                med_delta = statistics.median(deltas)
                if 25 <= med_delta <= 35:
                    target_day = int(round(statistics.median([d.day for d in dates])))
                    if target_day not in covered_days:
                        amounts = [e.amount for e in regular_salaries]
                        base_amount = statistics.median(amounts)
                        recurring.append(RecurringItem(
                            category="salary",
                            description="Recurring monthly salary",
                            direction=Direction.CREDIT,
                            amount=base_amount,
                            frequency_days=30,
                            day_of_month=target_day,
                            last_date=dates[-1],
                            source_event_id=regular_salaries[-1].event_id,
                            flexibility=Flexibility.FIXED,
                        ))
                        covered_days.add(target_day)
                elif 12 <= med_delta <= 16:
                    days = [d.day for d in dates]
                    d1_list = [d for d in days if d <= 15]
                    d2_list = [d for d in days if d > 15]
                    if d1_list and d2_list:
                        t1 = int(round(statistics.median(d1_list)))
                        t2 = int(round(statistics.median(d2_list)))
                        amts1 = [e.amount for e in regular_salaries if abs((e.settlement_date or e.event_date).day - t1) <= 3]
                        amts2 = [e.amount for e in regular_salaries if abs((e.settlement_date or e.event_date).day - t2) <= 3]
                        amt1 = statistics.median(amts1) if amts1 else statistics.median([e.amount for e in regular_salaries])
                        amt2 = statistics.median(amts2) if amts2 else amt1
                        if t1 not in covered_days:
                            recurring.append(RecurringItem(
                                category="salary", description="Recurring semi-monthly salary 1", direction=Direction.CREDIT,
                                amount=amt1, frequency_days=30, day_of_month=t1, last_date=dates[-1],
                                source_event_id=regular_salaries[-1].event_id, flexibility=Flexibility.FIXED
                            ))
                            covered_days.add(t1)
                        if t2 not in covered_days:
                            recurring.append(RecurringItem(
                                category="salary", description="Recurring semi-monthly salary 2", direction=Direction.CREDIT,
                                amount=amt2, frequency_days=30, day_of_month=t2, last_date=dates[-1],
                                source_event_id=regular_salaries[-1].event_id, flexibility=Flexibility.FIXED
                            ))
                            covered_days.add(t2)

    # 2. Detect monthly fixed / variable bills (rent, utilities, subscriptions, debt repayments, etc.)
    # Group by (category, description)
    bill_groups = defaultdict(list)
    living_cats = {"groceries", "transport", "dining"}

    for e in settled:
        if e.direction == Direction.DEBIT and e.amount is not None:
            if e.category not in living_cats:
                bill_groups[(e.category, e.description)].append(e)

    for (cat, desc), evs in bill_groups.items():
        if len(evs) < 2:
            continue
        dates = sorted([e.settlement_date or e.event_date for e in evs])
        deltas = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
        med_delta = statistics.median(deltas)

        if 24 <= med_delta <= 35:
            # Monthly recurring bill
            target_day = int(round(statistics.median([d.day for d in dates])))
            amounts = [e.amount for e in evs]
            # If all amounts are identical, use that amount
            if len(set(amounts)) == 1:
                amt = amounts[0]
            else:
                # For variable bills like utilities, take mean/median conservatively
                amt = statistics.median(amounts)

            # Check flexibility and minimum allowed amount
            flex = evs[-1].flexibility
            min_amt = evs[-1].minimum_allowed_amount

            recurring.append(RecurringItem(
                category=cat,
                description=desc,
                direction=Direction.DEBIT,
                amount=amt,
                frequency_days=30,
                day_of_month=target_day,
                last_date=dates[-1],
                source_event_id=evs[-1].event_id,
                flexibility=flex,
                minimum_allowed_amount=min_amt,
            ))

    # 3. Detect living expenses (groceries, transport, dining) by category
    for cat in living_cats:
        evs = [e for e in settled if e.category == cat and e.direction == Direction.DEBIT and e.amount is not None]
        if len(evs) < 2:
            continue
        dates = sorted([e.settlement_date or e.event_date for e in evs])
        deltas = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
        med_delta = statistics.median(deltas)

        # Standard interval (typically 7d for weekly, 14d for bi-weekly, 10d, 21d)
        freq_days = int(round(med_delta))
        if freq_days < 5 or freq_days > 40:
            continue

        amounts = [e.amount for e in evs]
        # Conservative estimate for living expenses: average of historical occurrences
        avg_amt = sum(amounts) / Decimal(len(amounts))
        # Round to 2 decimal places for clean accounting
        avg_amt = avg_amt.quantize(Decimal("0.01"))

        flex = evs[-1].flexibility
        min_amt = evs[-1].minimum_allowed_amount

        recurring.append(RecurringItem(
            category=cat,
            description=f"Recurring {cat}",
            direction=Direction.DEBIT,
            amount=avg_amt,
            frequency_days=freq_days,
            last_date=dates[-1],
            source_event_id=evs[-1].event_id,
            flexibility=flex,
            minimum_allowed_amount=min_amt,
        ))

    return recurring
