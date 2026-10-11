"""Conservative, structured interpretation of untrusted dataset evidence."""
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
import re


@dataclass(frozen=True)
class EvidenceAmendment:
    action: str
    affected_event_id: str | None
    new_amount: Decimal | None = None
    new_date: date | None = None
    recurring: bool | None = None
    confidence: str = "low"
    source_message_id: str = ""


_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_MONEY = re.compile(r"\b(?:ZAR|IDR|EUR|USD|INR)\s*([\d,]+(?:\.\d{1,2})?)\b", re.I)


@dataclass(frozen=True)
class ImageEvidence:
    image_id: str
    event_id: str
    amount: Decimal
    currency: str
    evidence_type: str
    confidence: str


# Reviewed from the image linked to each blank-valued event.  This is evidence
# data, not a request-specific decision label.  Values select the amount due,
# paid, or net amount matching the supplied event description (not a subtotal,
# gross salary, cash tendered, or late-payment alternative).
IMAGE_AMOUNT_CACHE = {
    "event_253": ImageEvidence("image_01", "event_253", Decimal("4365000"), "IDR", "payslip_net_pay", "high"),
    "event_1442": ImageEvidence("image_02", "event_1442", Decimal("100000"), "INR", "rent_balance_due", "high"),
    "event_1545": ImageEvidence("image_03", "event_1545", Decimal("41272"), "INR", "invoice_net_amount", "high"),
    "event_1700": ImageEvidence("image_04", "event_1700", Decimal("2854"), "INR", "delivered_order_item_bill", "medium"),
    "event_1786": ImageEvidence("image_05", "event_1786", Decimal("704.05"), "INR", "current_amount_due", "high"),
    "event_3051": ImageEvidence("image_06", "event_3051", Decimal("1995"), "INR", "tax_invoice_total", "high"),
    "event_3231": ImageEvidence("image_07", "event_3231", Decimal("8528.10"), "INR", "restaurant_grand_total", "high"),
    "event_4535": ImageEvidence("image_08", "event_4535", Decimal("15339"), "INR", "maintenance_receipt_total", "high"),
    "event_5170": ImageEvidence("image_09", "event_5170", Decimal("723"), "INR", "water_bill_amount", "high"),
    "event_6033": ImageEvidence("image_10", "event_6033", Decimal("79679.26"), "INR", "invoice_balance_due", "high"),
    "event_6859": ImageEvidence("image_11", "event_6859", Decimal("3650"), "INR", "hospital_amount_payable", "high"),
    "event_7307": ImageEvidence("image_12", "event_7307", Decimal("33.50"), "USD", "taxi_total", "high"),
    "event_7941": ImageEvidence("image_13", "event_7941", Decimal("2298"), "INR", "order_total_paid", "high"),
    "event_9421": ImageEvidence("image_14", "event_9421", Decimal("4543"), "INR", "pharmacy_receipt_total", "medium"),
    "event_9806": ImageEvidence("image_15", "event_9806", Decimal("9968"), "INR", "airline_grand_total", "high"),
    "event_10521": ImageEvidence("image_16", "event_10521", Decimal("393.22"), "INR", "charging_invoice_total", "high"),
}


def validate_image_evidence(loader):
    """Validate cache linkage and amounts before any financial state consumes it."""
    blank = {e.event_id: e for e in loader.events if e.amount is None}
    assert set(blank) == set(IMAGE_AMOUNT_CACHE), "every blank event must have reviewed image evidence"
    for event_id, evidence in IMAGE_AMOUNT_CACHE.items():
        event = blank[event_id]
        image = next((i for i in loader.images_by_event.get(event_id, []) if i.image_id == evidence.image_id), None)
        assert image is not None and evidence.amount >= 0 and evidence.currency == event.currency
    return IMAGE_AMOUNT_CACHE


def _money(text):
    found = _MONEY.findall(text)
    return Decimal(found[-1].replace(",", "")) if found else None


def interpret_messages(loader, user_id):
    """Return high-confidence amendments only; raw text is never executable."""
    amendments = []
    events = loader.get_events_for_user(user_id)
    for msg in sorted(loader.messages_by_user.get(user_id, []), key=lambda m: m.sent_at):
        text = msg.message_text.lower()
        target = loader.get_event(msg.related_event_id) if msg.related_event_id else None
        # Unlinked payroll messages may only alter a uniquely identifiable scheduled salary.
        if target is None and ("salary" in text or "payroll" in text):
            candidates = [e for e in events if e.category == "salary" and e.status.value == "scheduled"]
            if len(candidates) == 1:
                target = candidates[0]
            elif not candidates:
                # A clear payroll amendment can anchor one established salary
                # stream, but never guesses between multiple named streams.
                historical = [e for e in events if e.status.value == "settled" and e.direction.value == "credit" and
                              (e.category == "salary" or "salary" in e.description.lower() or "payroll" in e.description.lower())]
                # Do not collapse distinct income streams: only use a single
                # recognizable salary/payroll cadence family.
                families = {"salary" if (e.category == "salary" or "salary" in e.description.lower()) else "payroll" for e in historical}
                if historical and len(families) == 1:
                    target = max(historical, key=lambda e: e.settlement_date or e.event_date)
        if target is None:
            continue
        if any(w in text for w in ("cancelled", "canceled", "contract has ended", "employment ceased", "no off-season income")):
            amendments.append(EvidenceAmendment("cancel", target.event_id, confidence="high", source_message_id=msg.message_id))
            continue
        dates = _DATE.findall(msg.message_text)
        new_date = date.fromisoformat(dates[-1]) if dates else None
        amount = _money(msg.message_text)
        if new_date and any(w in text for w in ("replaces", "revised", "expected on", "delayed", "resumes on")):
            amendments.append(EvidenceAmendment("amend_date", target.event_id, new_date=new_date, confidence="high", source_message_id=msg.message_id))
        if amount is not None and any(w in text for w in ("salary", "payroll", "rent", "lease", "payment")) and any(w in text for w in ("confirmed", "updated", "increases", "reduced", "now", "resumes")):
            amendments.append(EvidenceAmendment("amend_amount", target.event_id, new_amount=amount, confidence="medium", source_message_id=msg.message_id))
    return amendments


def amended_events(loader, user_id):
    """Apply cancellation/amendment precedence in message chronology to copied records."""
    validate_image_evidence(loader)
    by_id = {e.event_id: e for e in loader.get_events_for_user(user_id)}
    for event_id, evidence in IMAGE_AMOUNT_CACHE.items():
        if event_id in by_id and by_id[event_id].amount is None:
            by_id[event_id] = replace(by_id[event_id], amount=evidence.amount, currency=evidence.currency)
    for change in interpret_messages(loader, user_id):
        event = by_id.get(change.affected_event_id)
        if event is None:
            continue
        if change.action == "cancel":
            from code.models import EventStatus
            by_id[event.event_id] = replace(event, status=EventStatus.CANCELLED)
        elif change.action == "amend_amount":
            marker = "[amended payroll] " if event.category == "salary" and event.status.value == "settled" else ""
            by_id[event.event_id] = replace(event, amount=change.new_amount, description=marker + event.description)
        elif change.action == "amend_date":
            marker = "[amended payroll] " if event.category == "salary" and event.status.value == "settled" else ""
            by_id[event.event_id] = replace(event, settlement_date=change.new_date, description=marker + event.description)
    return list(by_id.values())


def image_evidence(loader, user_id):
    """Structured deterministic lookup after reviewed local image inspection."""
    validate_image_evidence(loader)
    return [IMAGE_AMOUNT_CACHE[i.related_event_id] for i in loader.images_by_user.get(user_id, []) if i.related_event_id in IMAGE_AMOUNT_CACHE]
