"""Deterministic synthetic Kobo-style demo dataset + fake project logframe.

Everything here is FAKE: names, phones, GPS points. The dataset exists so the
project can be demoed and tested without any real beneficiary data, and so the
PII boundary is visibly exercised (spec §7).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

OBLASTS = [
    "Донецька", "Луганська", "Харківська", "Запорізька",
    "Херсонська", "Дніпропетровська", "Сумська", "Миколаївська",
]
SERVICES = ["health", "mhpss", "cash", "nfi", "protection"]

LOGFRAME = """# Project ABC-123 — Emergency Multi-Sectoral Assistance (DEMO, fake data)

## Logframe indicators

| Indicator | Definition | Target |
|---|---|---|
| Indicator 1.1 | # of unique households reached with at least one service | 2500 |
| Indicator 1.2 | % of reached households that are female-headed | 55% |
| Indicator 2.1 | # of households receiving cash assistance | 1200 |

## Notes

- Reporting period: June–August 2026.
- A household counts as "reached" once per unique submission (`_uuid`).
- Disaggregate all indicators by oblast and by sex of head of household (SADD).
"""


def generate(workspace: Path, rows: int = 3000, seed: int = 42) -> Path:
    rng = np.random.default_rng(seed)
    fake = Faker("uk_UA")
    Faker.seed(seed)

    dates = pd.to_datetime("2026-06-01") + pd.to_timedelta(
        rng.integers(0, 92, rows), unit="D"
    )
    df = pd.DataFrame(
        {
            "_uuid": [str(uuid.UUID(int=int(i))) for i in rng.integers(0, 2**63, rows)],
            "submission_date": dates.strftime("%Y-%m-%d"),
            "oblast": rng.choice(OBLASTS, rows),
            "raion": [f"Район-{i}" for i in rng.integers(1, 6, rows)],
            "hromada": [f"Громада-{i}" for i in rng.integers(1, 25, rows)],
            "settlement_type": rng.choice(["urban", "rural"], rows, p=[0.6, 0.4]),
            "hh_size": rng.integers(1, 9, rows),
            "head_sex": rng.choice(["female", "male"], rows, p=[0.58, 0.42]).astype(object),
            "head_age": rng.integers(18, 90, rows),
            "disability_in_hh": rng.choice(["yes", "no"], rows, p=[0.25, 0.75]),
            "displacement_status": rng.choice(
                ["idp", "host_community", "returnee"], rows, p=[0.45, 0.4, 0.15]
            ),
            "services_received": [
                " ".join(sorted(rng.choice(SERVICES, size=rng.integers(1, 4), replace=False)))
                for _ in range(rows)
            ],
            "satisfaction": rng.integers(1, 6, rows),
            "resp_name": [fake.name() for _ in range(rows)],
            "resp_phone": [
                f"+380{rng.integers(50, 99)}{rng.integers(1000000, 9999999)}"
                for _ in range(rows)
            ],
            "gps_lat": np.round(rng.uniform(46.0, 50.5, rows), 6),
            "gps_lon": np.round(rng.uniform(29.5, 38.5, rows), 6),
            "enumerator": rng.choice([fake.name() for _ in range(12)], rows),
            "comment": [
                fake.sentence(nb_words=8) if rng.random() < 0.3 else "" for _ in range(rows)
            ],
        }
    )

    # Intentional data-quality defects (spec §7).
    missing_sex = rng.choice(rows, size=int(rows * 0.03), replace=False)
    df.loc[missing_sex, "head_sex"] = None
    df.loc[rng.choice(rows, size=15, replace=False), "head_age"] = 999
    future = rng.choice(rows, size=10, replace=False)
    df.loc[future, "submission_date"] = "2027-01-15"
    kharkiv = df.index[df["oblast"] == "Харківська"]
    df.loc[kharkiv[: max(1, len(kharkiv) // 20)], "oblast"] = "Харківська обл."
    dupes = df.sample(30, random_state=seed)
    df = pd.concat([df, dupes], ignore_index=True)

    out = workspace / "data" / "beneficiaries.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out, index=False)

    docs = workspace / "project_docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "logframe.md").write_text(LOGFRAME, encoding="utf-8")
    return out


def main() -> None:  # pragma: no cover - thin wrapper
    import argparse

    p = argparse.ArgumentParser(description="Generate the synthetic demo workspace")
    p.add_argument("--workspace", type=Path, default=Path("workspace"))
    args = p.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    path = generate(args.workspace)
    print(f"Demo data written to {path}")


if __name__ == "__main__":  # pragma: no cover
    main()
