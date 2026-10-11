"""
State builder module: Reconstructs the user's financial state at a given evaluation date.
"""
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from code.data_loader import DataLoader
from code.models import (
    FinancialProfile,
    FinancialEvent,
    EventStatus,
    Direction,
)


@dataclass
class UserFinancialState:
    user_id: str
    home_currency: str
    as_of_date: date
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    profile: FinancialProfile
    pending_debits: List[FinancialEvent]
    scheduled_debits: List[FinancialEvent]
    scheduled_credits: List[FinancialEvent]
    settled_history: List[FinancialEvent]


def convert_event_currency(
    event: FinancialEvent,
    home_currency: str,
    exchange_rates: Dict[Tuple[date, str, str], Decimal],
) -> FinancialEvent:
    """Convert an event's amount and minimum_allowed_amount to home_currency if foreign."""
    if event.currency == home_currency:
        return event

    rate_date = event.settlement_date or event.event_date
    rate = exchange_rates.get((rate_date, event.currency, home_currency))

    if rate is None:
        raise ValueError(
            f"Missing exchange rate for {event.currency} -> {home_currency} on {rate_date} (event {event.event_id})"
        )

    converted_amount = (event.amount * rate) if event.amount is not None else None
    converted_min = (event.minimum_allowed_amount * rate) if event.minimum_allowed_amount is not None else None

    return replace(
        event,
        amount=converted_amount,
        minimum_allowed_amount=converted_min,
        currency=home_currency,
    )


def build_user_financial_state(
    user_id: str,
    as_of_date: date,
    loader: DataLoader,
    events: Optional[List[FinancialEvent]] = None,
) -> UserFinancialState:
    """
    Build user's current financial state:
    - Preserves current_available_balance without replaying settled events.
    - Excludes failed/cancelled/non-cash/unrealized events.
    - Excludes pending credits.
    - Reserves pending debits.
    - Includes scheduled future debits and confirmed salary credits.
    - Converts foreign currencies using supplied exchange rates on settlement date.
    """
    profile = loader.get_profile(user_id)
    if not profile:
        raise ValueError(f"Profile not found for user {user_id}")

    home_curr = profile.home_currency
    raw_events = events if events is not None else loader.get_events_for_user(user_id)

    curr_balance = profile.current_available_balance
    min_balance = profile.minimum_balance_to_keep

    pending_debits: List[FinancialEvent] = []
    scheduled_debits: List[FinancialEvent] = []
    scheduled_credits: List[FinancialEvent] = []
    settled_history: List[FinancialEvent] = []

    for raw_e in raw_events:
        # Exclude non-cash, unrealized, failed, and cancelled events
        if raw_e.direction == Direction.NON_CASH:
            continue
        if raw_e.status in (EventStatus.FAILED, EventStatus.CANCELLED, EventStatus.UNREALIZED):
            continue

        # Convert to home currency if foreign
        e = convert_event_currency(raw_e, home_curr, loader.exchange_rates)

        if e.status == EventStatus.SETTLED:
            # Do NOT replay settled events into today's balance.
            # Record in history for recurrence analysis.
            settled_history.append(e)

        elif e.status == EventStatus.PENDING:
            if e.direction == Direction.CREDIT:
                # Rule: Do not count pending credits until they settle
                continue
            elif e.direction == Direction.DEBIT:
                # Pending debits must be reserved
                if e.amount is not None:
                    if e.settlement_date and e.settlement_date <= as_of_date:
                        # Overdue or settling today: deduct immediately from available balance
                        curr_balance -= e.amount
                    else:
                        # Settles in future: reserve by applying on settlement date in simulation
                        pending_debits.append(e)

        elif e.status == EventStatus.SCHEDULED:
            if e.direction == Direction.CREDIT:
                # Rule: Count confirmed salary on its settlement date
                if e.amount is not None:
                    if e.settlement_date and e.settlement_date > as_of_date:
                        scheduled_credits.append(e)
            elif e.direction == Direction.DEBIT:
                if e.amount is not None:
                    if e.settlement_date and e.settlement_date <= as_of_date:
                        curr_balance -= e.amount
                    else:
                        scheduled_debits.append(e)

    # Sort historical events chronologically
    settled_history.sort(key=lambda ev: ev.settlement_date or ev.event_date or date.min)

    return UserFinancialState(
        user_id=user_id,
        home_currency=home_curr,
        as_of_date=as_of_date,
        current_available_balance=curr_balance,
        minimum_balance_to_keep=min_balance,
        profile=profile,
        pending_debits=pending_debits,
        scheduled_debits=scheduled_debits,
        scheduled_credits=scheduled_credits,
        settled_history=settled_history,
    )
