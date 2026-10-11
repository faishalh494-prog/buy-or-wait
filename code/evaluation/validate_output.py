import csv, re
from decimal import Decimal
from code.data_loader import DataLoader
from code.main import COLUMNS
from code.evidence import amended_events
from code.financial.state_builder import build_user_financial_state
from code.financial.recurrence import detect_recurring_items
from code.financial.simulator import simulate_cashflow
from code.decision_engine import _changed_recurring
from datetime import date

def validate(path):
    loader = DataLoader(); loader.load_all()
    with open(path, newline="", encoding="utf-8") as f: rows = list(csv.DictReader(f))
    assert len(rows) == len(loader.requests), f"expected {len(loader.requests)} rows, got {len(rows)}"
    assert list(rows[0]) == COLUMNS
    ids = [r["request_id"] for r in rows]; assert set(ids) == set(loader.requests) and len(ids) == len(set(ids))
    statuses = {"affordable_now","affordable_with_plan","affordable_later","not_affordable"}; methods = {"full_payment","partial_payment","installments","wait","not_recommended"}
    for r in rows:
        req = loader.requests[r["request_id"]]; amount = Decimal(r["amount_safe_to_pay"])
        assert Decimal(0) <= amount <= req.requested_amount
        assert r["affordability_status"] in statuses and r["recommended_payment_method"] in methods
        if r["payment_plan"] != "none":
            entries = r["payment_plan"].split("|")
            assert all(re.fullmatch(r"20\d\d-\d\d-\d\d:[0-9]+(?:\.[0-9]+)?", x) for x in entries)
            payments = [(date.fromisoformat(x.split(":")[0]), Decimal(x.split(":")[1])) for x in entries]
            assert payments == sorted(payments), r["request_id"]
            assert payments[-1][0] <= req.desired_completion_date, r["request_id"]
            if r["recommended_payment_method"] == "installments":
                matching = []
                for option in loader.get_payment_options(req.request_id):
                    if option.payment_method != "installments" or option.payment_frequency_days is None:
                        continue
                    schedule = [(option.first_payment_date + __import__('datetime').timedelta(days=option.payment_frequency_days*i), option.payment_amount) for i in range(option.number_of_payments)]
                    if schedule == payments: matching.append(option)
                assert matching, r["request_id"]
            if r["recommended_payment_method"] == "partial_payment":
                assert req.allows_partial_payment and len(payments) == 2
                assert payments[0][0] == req.request_date and payments[0][1] == amount
                assert sum(x[1] for x in payments) == req.requested_amount
            changes = []
            if r["spending_changes_needed"] != "none":
                for action in r["spending_changes_needed"].split("|"):
                    bits = action.split(":")
                    changes.append((bits[1], Decimal(0) if bits[0] == "stop" else Decimal(bits[2]), action, Decimal(0)))
            state = build_user_financial_state(req.user_id, req.request_date, loader, amended_events(loader, req.user_id))
            recurring = _changed_recurring(detect_recurring_items(state), state.profile, changes)
            original = {x.source_event_id:x for x in detect_recurring_items(state)}
            assert len(changes) <= 3 and len({x[0] for x in changes}) == len(changes)
            for event_id, new_amount, action, _ in changes:
                item = original.get(event_id); assert item is not None and not state.profile.is_category_protected(item.category)
                if action.startswith("stop:"):
                    assert state.profile.is_category_stoppable(item.category) and item.flexibility.value in ("stoppable", "reducible_or_stoppable")
                else:
                    assert state.profile.is_category_reducible(item.category) and item.flexibility.value in ("reducible", "reducible_or_stoppable")
                    assert item.minimum_allowed_amount is not None and new_amount >= item.minimum_allowed_amount
            assert simulate_cashflow(state, recurring, candidate_payments=payments).balance_never_below_minimum, r["request_id"]
        assert r["spending_changes_needed"] == "none" or all(re.fullmatch(r"(?:stop:[^:|]+|reduce_to:[^:|]+:[0-9]+(?:\.[0-9]+)?)", x) for x in r["spending_changes_needed"].split("|"))
    return len(rows)

if __name__ == "__main__":
    import sys
    print(f"validated {validate(sys.argv[1] if len(sys.argv)>1 else 'output.csv')} rows")
