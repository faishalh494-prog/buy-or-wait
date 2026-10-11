"""Deterministic candidate generation, validation, and ranking."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
from code.financial.simulator import simulate_cashflow, run_m1a_core
from code.models import Direction, Flexibility


@dataclass
class Candidate:
    method: str
    status: str
    payments: list
    changes: list
    option_id: str = ""
    total: Decimal = Decimal(0)


def _fmt(n):
    n = n.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(int(n)) if n == n.to_integral() else format(n, "f")


def _simulate(state, recurring, payments):
    return simulate_cashflow(state, recurring, candidate_payments=payments).balance_never_below_minimum


def _changed_recurring(recurring, profile, changes):
    result = []
    mapping = {c[0]: c for c in changes}
    for item in recurring:
        change = mapping.get(item.source_event_id)
        if not change:
            result.append(item); continue
        from dataclasses import replace
        result.append(replace(item, amount=change[1]))
    return result


def spending_change_candidates(recurring, profile):
    """Least disruptive eligible reductions/stops, up to three distinct source events."""
    choices = []
    for item in recurring:
        if item.direction != Direction.DEBIT or not item.source_event_id or profile.is_category_protected(item.category):
            continue
        if item.flexibility in (Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE) and profile.is_category_reducible(item.category):
            floor = item.minimum_allowed_amount if item.minimum_allowed_amount is not None else item.amount
            if floor < item.amount:
                choices.append((item.source_event_id, floor, f"reduce_to:{item.source_event_id}:{_fmt(floor)}", item.amount-floor))
        if item.flexibility in (Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE) and profile.is_category_stoppable(item.category):
            choices.append((item.source_event_id, Decimal(0), f"stop:{item.source_event_id}", item.amount))
    candidates = []
    for count in range(1, min(3, len(choices)) + 1):
        for group in combinations(choices, count):
            ids = [x[0] for x in group]
            if len(set(ids)) == count:
                candidates.append(list(group))
    return sorted(candidates, key=lambda c: (len(c), sum(x[3] for x in c)))


def decide(request, state, recurring, options):
    profile = state.profile
    core = run_m1a_core(state, recurring, request.requested_amount)
    earliest = core.earliest_date_for_full_payment
    candidates = []
    if profile.considers_payment_method("full_payment") and _simulate(state, recurring, [(request.request_date, request.requested_amount)]):
        candidates.append(Candidate("full_payment", "affordable_now", [(request.request_date, request.requested_amount)], [], total=request.requested_amount))
    for option in options:
        if option.payment_method != "installments" or not profile.considers_payment_method("installments"):
            continue
        if profile.max_installment_months is not None and option.number_of_payments > profile.max_installment_months:
            continue
        if option.payment_frequency_days is None or option.number_of_payments < 2:
            continue
        payments = [(option.first_payment_date + timedelta(days=option.payment_frequency_days*i), option.payment_amount) for i in range(option.number_of_payments)]
        if payments[-1][0] > request.desired_completion_date or payments[0][0] < request.request_date:
            continue
        if sum(x[1] for x in payments) != option.total_payable_amount or not _simulate(state, recurring, payments):
            continue
        candidates.append(Candidate("installments", "affordable_with_plan", payments, [], option.payment_option_id, option.total_payable_amount))
    if (request.allows_partial_payment and profile.considers_payment_method("partial_payment") and
            Decimal(0) < core.amount_safe_to_pay < request.requested_amount and earliest and earliest <= request.desired_completion_date):
        pays = [(request.request_date, core.amount_safe_to_pay), (earliest, request.requested_amount-core.amount_safe_to_pay)]
        if _simulate(state, recurring, pays):
            candidates.append(Candidate("partial_payment", "affordable_with_plan", pays, [], total=request.requested_amount))
    # Spending changes are only used to make an otherwise-invalid immediate full payment valid.
    for changes in spending_change_candidates(recurring, profile):
        adjusted = _changed_recurring(recurring, profile, changes)
        if profile.considers_payment_method("full_payment") and _simulate(state, adjusted, [(request.request_date, request.requested_amount)]):
            candidates.append(Candidate("full_payment", "affordable_with_plan", [(request.request_date, request.requested_amount)], changes, total=request.requested_amount))
            break
    if candidates:
        candidates.sort(key=lambda c: (bool(c.changes), c.total, c.payments[0][0], len(c.payments), c.option_id))
        choice = candidates[0]
    elif earliest and earliest <= request.desired_completion_date and profile.considers_payment_method("full_payment"):
        # A wait recommendation still commits to the simulated later full payment.
        choice = Candidate("wait", "affordable_later", [(earliest, request.requested_amount)], [], total=request.requested_amount)
    else:
        choice = Candidate("not_recommended", "not_affordable", [], [], total=Decimal(0))
    change_text = "|".join(x[2] for x in choice.changes) if choice.changes else "none"
    plan = "|".join(f"{d.isoformat()}:{_fmt(a)}" for d,a in choice.payments) if choice.payments else "none"
    earliest_text = earliest.isoformat() if earliest else ""
    if choice.method == "full_payment":
        explanation = "The full payment passes the 90-day minimum-balance forecast."
    elif choice.method == "installments": explanation = "The supplied installment schedule passes the 90-day minimum-balance forecast."
    elif choice.method == "partial_payment": explanation = "The two permitted payments pass the 90-day minimum-balance forecast."
    elif choice.method == "wait": explanation = "Waiting until the projected safe full-payment date preserves the minimum balance."
    else: explanation = "No eligible payment plan is safe within the 90-day forecast and completion date."
    return {"request_id": request.request_id, "amount_safe_to_pay": _fmt(core.amount_safe_to_pay), "affordability_status": choice.status,
            "recommended_payment_method": choice.method, "payment_plan": plan, "earliest_date_for_full_payment": earliest_text,
            "spending_changes_needed": change_text, "decision_explanation": explanation}
