"""
Benchmark and regression snapshot utility for sample requests.
Compares sample runs directly against official benchmark expectations in dataset/sample_requests.csv.
"""
import csv
from pathlib import Path
from typing import Dict, Any, List

FIELDS = [
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan",
    "earliest_date_for_full_payment", "spending_changes_needed",
]


def create_snapshot(source: str = "dataset/sample_requests.csv", destination: str = None) -> Path:
    """Create a baseline snapshot from official benchmark expectations."""
    dst = Path(destination) if destination else Path(__file__).with_name("sample_regression.csv")
    with open(source, newline="", encoding="utf-8") as src, open(dst, "w", newline="", encoding="utf-8") as out:
        rows = csv.DictReader(src)
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "").strip() for field in FIELDS})
    return dst


def compare_against_benchmark(
    generated_path: str = "sample_output.csv",
    benchmark_path: str = "dataset/sample_requests.csv"
) -> Dict[str, Any]:
    """Compare a generated sample output against the official benchmark expectations."""
    with open(benchmark_path, newline="", encoding="utf-8") as f_bench:
        benchmark = {r["request_id"].strip(): r for r in csv.DictReader(f_bench)}

    with open(generated_path, newline="", encoding="utf-8") as f_gen:
        generated = {r["request_id"].strip(): r for r in csv.DictReader(f_gen)}

    exact_matches: List[str] = []
    field_mismatches: Dict[str, List[str]] = {}

    for req_id, exp_row in benchmark.items():
        if req_id not in generated:
            field_mismatches[req_id] = ["MISSING_FROM_OUTPUT"]
            continue

        gen_row = generated[req_id]
        diffs = []
        for field in FIELDS:
            exp_val = exp_row.get(field, "").strip()
            gen_val = gen_row.get(field, "").strip()
            if exp_val != gen_val:
                diffs.append(f"{field}: expected '{exp_val}' got '{gen_val}'")

        if not diffs:
            exact_matches.append(req_id)
        else:
            field_mismatches[req_id] = diffs

    return {
        "total_requests": len(benchmark),
        "exact_matches": len(exact_matches),
        "exact_match_ids": exact_matches,
        "mismatches": len(field_mismatches),
        "field_mismatches": field_mismatches,
    }


def create(source: str = "dataset/sample_requests.csv", destination: str = None):
    """Backward-compatible snapshot creation."""
    return create_snapshot(source=source, destination=destination)


if __name__ == "__main__":
    create_snapshot()
    gen_file = Path("sample_output.csv")
    if gen_file.exists():
        results = compare_against_benchmark()
        print(f"Benchmark comparison: {results['exact_matches']}/{results['total_requests']} exact matches.")
