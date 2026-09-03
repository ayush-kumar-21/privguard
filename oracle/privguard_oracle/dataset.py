"""
Deterministic SafeHealth dataset generator. `SPEC.md` §2.2.

Generates the demo dataset as CSV files. Both the oracle and (from Phase 2.9)
the Rust loader read those files, which is the point: determinism comes from
bytes on disk, not from two languages agreeing about a pseudo-random stream.
Reimplementing Python's Mersenne Twister in Rust to make the two match would be
a lot of work in exchange for a worse guarantee.

`random.Random(seed)` is documented as reproducible across Python versions for
the core generators, so regenerating with the same seed yields the same files.

    python -m privguard_oracle.dataset --out data/safehealth --patients 150
"""
from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

__all__ = ["GeneratorConfig", "generate", "write_csv", "load_visits", "TABLES"]

TABLES = (
    "regions",
    "hospitals",
    "departments",
    "staff",
    "patients",
    "visits",
    "diagnoses",
    "lab_results",
)

REGIONS = [("North", "TN"), ("South", "KL"), ("West", "MH")]
DEPT_NAMES = ["Cardiology", "Oncology", "Emergency", "Paediatrics", "Neurology"]
STAFF_ROLES = ["consultant", "resident", "nurse"]
SEXES = ["F", "M", "X"]
INSURANCE = ["public", "private", "self-pay"]
ADMISSION_TYPES = ["EMERGENCY", "ELECTIVE", "URGENT"]
ICD10 = ["I21.9", "E11.9", "J18.9", "N39.0", "C50.9"]

# The attack demo needs one patient carrying a diagnosis nobody else has, so a
# targeted query isolates a single privacy unit. Without a signal like this the
# repeat-and-average demonstration has nothing to recover.
ICD10_RARE = "Z99.9"

LAB_TESTS = [("GLU", "mg/dL", 70, 200), ("HGB", "g/dL", 8, 18), ("CRP", "mg/L", 0, 50)]

EPOCH = datetime(2024, 1, 1)


@dataclass(frozen=True)
class GeneratorConfig:
    patients: int = 150
    seed: int = 42
    #: Visits per patient are drawn from 1..max_visits_per_patient. This must
    #: exceed the contribution bound C, or bounding would never bind and the
    #: tests would not exercise it.
    max_visits_per_patient: int = 8


def generate(cfg: GeneratorConfig | None = None) -> dict[str, list[dict]]:
    """Build the whole dataset in memory as `{table_name: [row, ...]}`."""
    cfg = cfg or GeneratorConfig()
    rng = random.Random(cfg.seed)

    regions = [
        {"region_id": i, "region_name": name, "state_code": code}
        for i, (name, code) in enumerate(REGIONS, start=1)
    ]

    hospitals = [
        {
            "hospital_id": i,
            "region_id": rng.choice(regions)["region_id"],
            "hospital_name": f"SafeHealth {i:02d}",
            "bed_count": rng.randrange(60, 400),
        }
        for i in range(1, 5)
    ]

    departments = [
        {
            "dept_id": i,
            "hospital_id": rng.choice(hospitals)["hospital_id"],
            "dept_name": name,
            "specialty": name.lower(),
        }
        for i, name in enumerate(DEPT_NAMES, start=1)
    ]

    staff = [
        {
            "staff_id": i,
            "dept_id": rng.choice(departments)["dept_id"],
            "full_name": f"Staff {i:03d}",
            "staff_role": rng.choice(STAFF_ROLES),
            "hire_date": (date(2015, 1, 1) + timedelta(days=rng.randrange(3000))).isoformat(),
        }
        for i in range(1, 31)
    ]

    patients = [
        {
            "patient_id": i,
            "region_id": rng.choice(regions)["region_id"],
            "mrn": f"MRN{i:06d}",
            "birth_year": rng.randrange(1940, 2015),
            "sex": rng.choice(SEXES),
            "insurance_type": rng.choice(INSURANCE),
            "enrolled_at": (date(2020, 1, 1) + timedelta(days=rng.randrange(1400))).isoformat(),
        }
        for i in range(1, cfg.patients + 1)
    ]

    visits: list[dict] = []
    diagnoses: list[dict] = []
    labs: list[dict] = []
    visit_id = diagnosis_id = lab_id = 0

    # Exactly one patient carries the rare diagnosis — the attack-demo target.
    rare_patient = rng.choice(patients)["patient_id"]

    for p in patients:
        for _ in range(rng.randrange(1, cfg.max_visits_per_patient + 1)):
            visit_id += 1
            admit = EPOCH + timedelta(
                days=rng.randrange(700), hours=rng.randrange(24), minutes=rng.randrange(60)
            )
            stay_hours = rng.randrange(2, 240)
            visits.append(
                {
                    "visit_id": visit_id,
                    "patient_id": p["patient_id"],
                    "dept_id": rng.choice(departments)["dept_id"],
                    "attending_staff_id": rng.choice(staff)["staff_id"],
                    "admit_ts": admit.isoformat(sep=" "),
                    "discharge_ts": (admit + timedelta(hours=stay_hours)).isoformat(sep=" "),
                    # Heavy-tailed-ish: most visits cheap, a few very expensive.
                    # A long tail is what makes clamping visibly matter.
                    "total_charge": round(rng.lognormvariate(7.5, 0.9), 2),
                    "admission_type": rng.choice(ADMISSION_TYPES),
                }
            )

            n_dx = rng.randrange(1, 4)
            for k in range(n_dx):
                diagnosis_id += 1
                code = (
                    ICD10_RARE
                    if (p["patient_id"] == rare_patient and k == 0)
                    else rng.choice(ICD10)
                )
                diagnoses.append(
                    {
                        "diagnosis_id": diagnosis_id,
                        "visit_id": visit_id,
                        "icd10_code": code,
                        "is_primary": k == 0,
                        "severity": rng.randrange(1, 5),
                    }
                )

            for _ in range(rng.randrange(0, 3)):
                lab_id += 1
                code, unit, lo, hi = rng.choice(LAB_TESTS)
                labs.append(
                    {
                        "lab_id": lab_id,
                        "visit_id": visit_id,
                        "test_code": code,
                        "value_num": round(rng.uniform(lo, hi), 2),
                        "unit": unit,
                        "taken_at": (admit + timedelta(hours=rng.randrange(stay_hours + 1)))
                        .isoformat(sep=" "),
                    }
                )

    return {
        "regions": regions,
        "hospitals": hospitals,
        "departments": departments,
        "staff": staff,
        "patients": patients,
        "visits": visits,
        "diagnoses": diagnoses,
        "lab_results": labs,
    }


def write_csv(data: dict[str, list[dict]], out_dir: Path) -> list[Path]:
    """Write each table to `<out_dir>/<table>.csv`, returning the paths.

    `newline=""` is required on Windows or csv writes \\r\\r\\n, which the Rust
    loader would read as a trailing empty field.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for table in TABLES:
        rows = data[table]
        path = out_dir / f"{table}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        written.append(path)
    return written


def load_visits(csv_dir: Path) -> list[dict]:
    """Read `visits.csv` back with the types the DP layer expects.

    CSV has no types, so the numeric columns are converted here rather than at
    every use site — a string `total_charge` would silently sort and clamp
    lexicographically.
    """
    path = Path(csv_dir) / "visits.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        rows = []
        for row in csv.DictReader(fh):
            rows.append(
                {
                    "visit_id": int(row["visit_id"]),
                    "patient_id": int(row["patient_id"]),
                    "dept_id": int(row["dept_id"]),
                    "attending_staff_id": int(row["attending_staff_id"]),
                    "admit_ts": row["admit_ts"],
                    "discharge_ts": row["discharge_ts"],
                    "total_charge": float(row["total_charge"]),
                    "admission_type": row["admission_type"],
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the SafeHealth demo dataset.")
    ap.add_argument("--out", type=Path, default=Path("data/safehealth"))
    ap.add_argument("--patients", type=int, default=150)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    data = generate(GeneratorConfig(patients=args.patients, seed=args.seed))
    paths = write_csv(data, args.out)
    for p in paths:
        print(f"  {p}  ({len(data[p.stem]):>6} rows)")
    # ASCII only: this prints to a Windows console under run.bat, where a
    # non-cp1252 character comes out as a replacement glyph.
    print(f"\nSeed {args.seed}: regenerating with this seed reproduces these files byte for byte.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
