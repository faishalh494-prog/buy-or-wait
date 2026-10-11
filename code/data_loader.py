"""
Data loader module for loading and indexing all challenge CSVs safely.
"""
import csv
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from code.models import (
    FinancialProfile,
    FinancialEvent,
    EventType,
    EventStatus,
    Direction,
    Flexibility,
    Request,
    PaymentOption,
    ExchangeRate,
    Message,
    ImageRecord,
)
from code.config import DATASET_DIR


class DataLoader:
    def __init__(self, dataset_dir: str = DATASET_DIR):
        self.dataset_dir = dataset_dir

        self.profiles: Dict[str, FinancialProfile] = {}
        self.events: List[FinancialEvent] = []
        self.events_by_id: Dict[str, FinancialEvent] = {}
        self.events_by_user: Dict[str, List[FinancialEvent]] = {}
        self.events_by_linked_id: Dict[str, List[FinancialEvent]] = {}

        self.requests: Dict[str, Request] = {}
        self.requests_by_user: Dict[str, List[Request]] = {}

        self.sample_requests: Dict[str, dict] = {}
        self.sample_requests_list: List[dict] = []

        self.payment_options: List[PaymentOption] = []
        self.payment_options_by_request: Dict[str, List[PaymentOption]] = {}

        self.exchange_rates: Dict[Tuple[date, str, str], Decimal] = {}

        self.messages: List[Message] = []
        self.messages_by_user: Dict[str, List[Message]] = {}
        self.messages_by_request: Dict[str, List[Message]] = {}
        self.messages_by_event: Dict[str, List[Message]] = {}

        self.images: List[ImageRecord] = []
        self.images_by_user: Dict[str, List[ImageRecord]] = {}
        self.images_by_request: Dict[str, List[ImageRecord]] = {}
        self.images_by_event: Dict[str, List[ImageRecord]] = {}

    def load_all(self):
        """Load and index all dataset files."""
        self.load_profiles()
        self.load_events()
        self.load_exchange_rates()
        self.load_requests()
        self.load_sample_requests()
        self.load_payment_options()
        self.load_messages()
        self.load_images()

    def _path(self, filename: str) -> str:
        return os.path.join(self.dataset_dir, filename)

    def load_profiles(self):
        filepath = self._path("financial_profiles.csv")
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row["user_id"].strip()
                max_inst = row["max_installment_months"].strip()
                profile = FinancialProfile(
                    user_id=uid,
                    home_currency=row["home_currency"].strip(),
                    current_available_balance=Decimal(row["current_available_balance"].strip()),
                    minimum_balance_to_keep=Decimal(row["minimum_balance_to_keep"].strip()),
                    financial_priorities=[p.strip() for p in row["financial_priorities"].split("|") if p.strip()],
                    expense_categories_to_protect=[p.strip() for p in row["expense_categories_to_protect"].split("|") if p.strip()],
                    expense_categories_user_is_willing_to_reduce=[p.strip() for p in row["expense_categories_user_is_willing_to_reduce"].split("|") if p.strip()],
                    expense_categories_user_is_willing_to_stop=[p.strip() for p in row["expense_categories_user_is_willing_to_stop"].split("|") if p.strip()],
                    payment_methods_user_will_consider=[p.strip() for p in row["payment_methods_user_will_consider"].split("|") if p.strip()],
                    max_installment_months=int(max_inst) if max_inst else None,
                )
                self.profiles[uid] = profile

    def load_events(self):
        filepath = self._path("financial_events.csv")
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                eid = row["event_id"].strip()
                uid = row["user_id"].strip()
                raw_amt = row["amount"].strip()
                amt = Decimal(raw_amt) if raw_amt else None  # Do NOT treat blank as zero

                raw_min_amt = row["minimum_allowed_amount"].strip()
                min_amt = Decimal(raw_min_amt) if raw_min_amt else None

                raw_edate = row["event_date"].strip()
                edate = date.fromisoformat(raw_edate) if raw_edate else None

                raw_sdate = row["settlement_date"].strip()
                sdate = date.fromisoformat(raw_sdate) if raw_sdate else None

                linked_id = row["linked_event_id"].strip() or None

                event = FinancialEvent(
                    event_id=eid,
                    user_id=uid,
                    event_type=EventType(row["event_type"].strip()),
                    description=row["description"].strip(),
                    category=row["category"].strip(),
                    direction=Direction(row["direction"].strip()),
                    amount=amt,
                    currency=row["currency"].strip(),
                    event_date=edate,
                    settlement_date=sdate,
                    status=EventStatus(row["status"].strip()),
                    linked_event_id=linked_id,
                    flexibility=Flexibility(row["flexibility"].strip()),
                    minimum_allowed_amount=min_amt,
                )
                self.events.append(event)
                self.events_by_id[eid] = event
                if uid not in self.events_by_user:
                    self.events_by_user[uid] = []
                self.events_by_user[uid].append(event)

                if linked_id:
                    if linked_id not in self.events_by_linked_id:
                        self.events_by_linked_id[linked_id] = []
                    self.events_by_linked_id[linked_id].append(event)

    def load_exchange_rates(self):
        filepath = self._path("exchange_rates.csv")
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                r_date = date.fromisoformat(row["rate_date"].strip())
                f_cur = row["from_currency"].strip()
                t_cur = row["to_currency"].strip()
                rate = Decimal(row["rate"].strip())
                self.exchange_rates[(r_date, f_cur, t_cur)] = rate

    def load_requests(self):
        filepath = self._path("requests.csv")
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rid = row["request_id"].strip()
                uid = row["user_id"].strip()
                req = Request(
                    request_id=rid,
                    user_id=uid,
                    request_date=date.fromisoformat(row["request_date"].strip()),
                    request_type=row["request_type"].strip(),
                    requested_amount=Decimal(row["requested_amount"].strip()),
                    desired_completion_date=date.fromisoformat(row["desired_completion_date"].strip()),
                    allows_partial_payment=row["allows_partial_payment"].strip().lower() in ("true", "1", "yes"),
                    request_text=row["request_text"].strip(),
                )
                self.requests[rid] = req
                if uid not in self.requests_by_user:
                    self.requests_by_user[uid] = []
                self.requests_by_user[uid].append(req)

    def load_sample_requests(self):
        filepath = self._path("sample_requests.csv")
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                clean_row = {k.strip(): v.strip() for k, v in row.items()}
                self.sample_requests[clean_row["request_id"]] = clean_row
                self.sample_requests_list.append(clean_row)

    def load_payment_options(self):
        filepath = self._path("request_payment_options.csv")
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                p_opt_id = row["payment_option_id"].strip()
                rid = row["request_id"].strip()
                freq_raw = row["payment_frequency_days"].strip()
                freq = int(freq_raw) if freq_raw else None
                opt = PaymentOption(
                    payment_option_id=p_opt_id,
                    request_id=rid,
                    payment_method=row["payment_method"].strip(),
                    payment_amount=Decimal(row["payment_amount"].strip()),
                    number_of_payments=int(row["number_of_payments"].strip()),
                    first_payment_date=date.fromisoformat(row["first_payment_date"].strip()),
                    payment_frequency_days=freq,
                    financing_fee=Decimal(row["financing_fee"].strip()),
                    total_payable_amount=Decimal(row["total_payable_amount"].strip()),
                )
                self.payment_options.append(opt)
                if rid not in self.payment_options_by_request:
                    self.payment_options_by_request[rid] = []
                self.payment_options_by_request[rid].append(opt)

    def load_messages(self):
        filepath = self._path("messages.csv")
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                mid = row["message_id"].strip()
                uid = row["user_id"].strip()
                rid = row["request_id"].strip() or None
                eid = row["related_event_id"].strip() or None
                raw_sent = row["sent_at"].strip()
                sent_at = datetime.fromisoformat(raw_sent) if raw_sent else None

                msg = Message(
                    message_id=mid,
                    user_id=uid,
                    request_id=rid,
                    related_event_id=eid,
                    sent_at=sent_at,
                    source_type=row["source_type"].strip(),
                    message_text=row["message_text"].strip(),
                )
                self.messages.append(msg)
                if uid not in self.messages_by_user:
                    self.messages_by_user[uid] = []
                self.messages_by_user[uid].append(msg)

                if rid:
                    if rid not in self.messages_by_request:
                        self.messages_by_request[rid] = []
                    self.messages_by_request[rid].append(msg)

                if eid:
                    if eid not in self.messages_by_event:
                        self.messages_by_event[eid] = []
                    self.messages_by_event[eid].append(msg)

    def load_images(self):
        filepath = self._path("images.csv")
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                img_id = row["image_id"].strip()
                uid = row["user_id"].strip()
                rid = row["request_id"].strip() or None
                eid = row["related_event_id"].strip() or None

                img = ImageRecord(
                    image_id=img_id,
                    user_id=uid,
                    request_id=rid,
                    related_event_id=eid,
                )
                self.images.append(img)
                if uid not in self.images_by_user:
                    self.images_by_user[uid] = []
                self.images_by_user[uid].append(img)

                if rid:
                    if rid not in self.images_by_request:
                        self.images_by_request[rid] = []
                    self.images_by_request[rid].append(img)

                if eid:
                    if eid not in self.images_by_event:
                        self.images_by_event[eid] = []
                    self.images_by_event[eid].append(img)

    # Fast accessors
    def get_profile(self, user_id: str) -> Optional[FinancialProfile]:
        return self.profiles.get(user_id)

    def get_events_for_user(self, user_id: str) -> List[FinancialEvent]:
        return self.events_by_user.get(user_id, [])

    def get_event(self, event_id: str) -> Optional[FinancialEvent]:
        return self.events_by_id.get(event_id)

    def get_linked_events(self, event_id: str) -> List[FinancialEvent]:
        return self.events_by_linked_id.get(event_id, [])

    def get_request(self, request_id: str) -> Optional[Request]:
        return self.requests.get(request_id)

    def get_payment_options(self, request_id: str) -> List[PaymentOption]:
        return self.payment_options_by_request.get(request_id, [])

    def get_exchange_rate(self, rate_date: date, from_cur: str, to_cur: str) -> Optional[Decimal]:
        if from_cur == to_cur:
            return Decimal(1)
        return self.exchange_rates.get((rate_date, from_cur, to_cur))
