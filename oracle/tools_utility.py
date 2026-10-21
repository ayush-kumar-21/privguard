"""
Utility evaluation (Phase 5.2): answer error as a function of the zCDP budget rho.

For each aggregate and each rho, run the oracle under many independent noise seeds (one per
principal id) and report the mean absolute error and the mean relative error against the exact
answer. Writes `docs/eval/utility_vs_rho.csv` and `docs/eval/utility_vs_rho.md`.

    cd oracle && uv run python tools_utility.py
"""
from __future__ import annotations

import csv
import importlib.util
import json
import statistics
from pathlib import Path

from privguard_oracle.dp import execute
from privguard_oracle.mechanisms import derive_seed
from privguard_oracle.spec import ColumnPolicy, QuerySpec, TablePolicy

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "tests" / "golden"
OUT = ROOT.parent / "docs" / "eval"
SECRET = b"privguard-utility-eval-v1"
TRIALS = 200
RHOS = [0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0]

_spec = importlib.util.spec_from_file_location("golden_generate", GOLDEN / "generate.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

QUERIES = {
    "COUNT(*)": (QuerySpec(agg="COUNT", column=None), None),
    "SUM(total_charge)": (QuerySpec(agg="SUM", column="total_charge"), ColumnPolicy(**gen.CHARGE_POLICY)),
    "AVG(total_charge)": (QuerySpec(agg="AVG", column="total_charge"), ColumnPolicy(**gen.CHARGE_POLICY)),
}


def scalar(rows) -> float:
    return float(rows[0]["value"])


def main() -> None:
    data = json.loads((GOLDEN / "dataset.json").read_text(encoding="utf-8"))
    table = TablePolicy(**gen.DEFAULT_TABLE)
    out = []
    for name, (spec, cpol) in QUERIES.items():
        for rho in RHOS:
            errs, answers = [], []
            for principal in range(1, TRIALS + 1):
                seed = derive_seed(SECRET, spec.ast_hash(), principal, b"")
                res = execute(spec, data, rho=rho, seed=seed, table_policy=table, column_policy=cpol,
                              delta=gen.DELTA, delta_partition=gen.DELTA_PARTITION)
                answers.append(scalar(res.rows))
            out.append((name, rho, answers))
    # The reference is the bounded (clamped, contribution-limited) answer the mechanism noises:
    # the mean of many draws converges to it, so use the median of a very high-rho run as truth.
    truth = {}
    for name, (spec, cpol) in QUERIES.items():
        seed = derive_seed(SECRET, spec.ast_hash(), 10**6, b"")
        res = execute(spec, data, rho=1e6, seed=seed, table_policy=table, column_policy=cpol,
                      delta=gen.DELTA, delta_partition=gen.DELTA_PARTITION)
        truth[name] = scalar(res.rows)

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, rho, answers in out:
        t = truth[name]
        abs_err = statistics.fmean(abs(a - t) for a in answers)
        rel = abs_err / abs(t) if t else float("nan")
        rows.append((name, rho, t, abs_err, rel))
    with open(OUT / "utility_vs_rho.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["query", "rho", "bounded_truth", "mean_abs_error", "mean_rel_error"])
        w.writerows((q, r, f"{t:.4f}", f"{a:.4f}", f"{e:.6f}") for q, r, t, a, e in rows)

    lines = [
        "# Utility vs privacy budget",
        "",
        f"Mean error of the oracle's answers over {TRIALS} independent noise seeds, against the "
        "bounded (clamped, contribution-limited) answer. Dataset: the 211-visit golden set, "
        "C=5, G=3. Regenerate with `cd oracle && uv run python tools_utility.py`.",
        "",
        "| query | rho | mean abs error | mean rel error |",
        "|---|---:|---:|---:|",
    ]
    lines += [f"| {q} | {r} | {a:,.2f} | {e:.2%} |" for q, r, _, a, e in rows]
    (OUT / "utility_vs_rho.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
