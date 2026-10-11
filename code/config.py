import os
from decimal import Decimal
from datetime import timedelta

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATASET_DIR = os.path.join(REPO_ROOT, "dataset")
OUTPUT_PATH = os.path.join(REPO_ROOT, "output.csv")
FORECAST_DAYS = 90
MONEY_PRECISION = 2

def forecast_end(request_date):
    return request_date + timedelta(days=FORECAST_DAYS)
