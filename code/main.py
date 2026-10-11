"""Runnable entry point: python -m code.main [--sample] [--output PATH]."""
import argparse, csv
from code.config import OUTPUT_PATH
from code.data_loader import DataLoader
from code.evidence import amended_events, image_evidence
from code.financial.state_builder import build_user_financial_state
from code.financial.recurrence import detect_recurring_items
from code.decision_engine import decide

COLUMNS = ["request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation"]

def generate(loader, requests):
    rows = []
    event_cache = {}
    for request in requests:
        if request.user_id not in event_cache:
            event_cache[request.user_id] = amended_events(loader, request.user_id)
            image_evidence(loader, request.user_id)  # explicit safe unresolved-image fallback/cache hook
        state = build_user_financial_state(request.user_id, request.request_date, loader, event_cache[request.user_id])
        recurring = detect_recurring_items(state)
        rows.append(decide(request, state, recurring, loader.get_payment_options(request.request_id)))
    return rows

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--output", default=OUTPUT_PATH)
    args = parser.parse_args()
    loader = DataLoader(); loader.load_all()
    requests = []
    if args.sample:
        from code.models import Request
        from datetime import date
        from decimal import Decimal
        for row in loader.sample_requests_list:
            requests.append(Request(row["request_id"], row["user_id"], date.fromisoformat(row["request_date"]), row["request_type"], Decimal(row["requested_amount"]), date.fromisoformat(row["desired_completion_date"]), row["allows_partial_payment"].lower() == "true", row["request_text"]))
    else:
        requests = list(loader.requests.values())
    rows = generate(loader, requests)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS); writer.writeheader(); writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.output}")

if __name__ == "__main__": main()
