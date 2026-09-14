import shutil
from pathlib import Path

import pytest
import yaml

from haa.demo import generate

DEMO_REGISTRY = {
    "indicators": [
        {
            "code": "1.1",
            "name": {"uk": "Охоплені домогосподарства", "en": "Households reached"},
            "definition": "Unique households with at least one service (by _uuid)",
            "target": {"value": 2500, "unit": "households"},
            "disaggregation": ["oblast", "head_sex"],
            "source": "beneficiaries",
            "measure": {
                "dataset": "beneficiaries",
                "aggregation": "count_unique",
                "field": "_uuid",
                "filter": None,
            },
        },
        {
            "code": "1.2",
            "name": {"uk": "Частка домогосподарств з жінкою на чолі", "en": "% female-headed"},
            "definition": "Share of reached households headed by a woman",
            "target": {"value": 55, "unit": "percent"},
            "disaggregation": ["oblast"],
            "source": "beneficiaries",
            "measure": {
                "dataset": "beneficiaries",
                "aggregation": "percent",
                "numerator": {"field": "_uuid", "filter": "head_sex == 'female'"},
                "denominator": {"field": "_uuid", "filter": None},
            },
        },
        {
            "code": "2.1",
            "name": {"uk": "Домогосподарства з грошовою допомогою", "en": "Households with cash"},
            "definition": "Households receiving cash assistance",
            "target": {"value": 1200, "unit": "households"},
            "disaggregation": ["oblast", "head_sex"],
            "source": "beneficiaries",
        },
    ]
}


@pytest.fixture(scope="session")
def demo_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    ws = tmp_path_factory.mktemp("demo_ws")
    (ws / "data").mkdir()
    (ws / "project_docs").mkdir()
    generate(ws)
    return ws


@pytest.fixture()
def report_workspace(tmp_path: Path, demo_workspace: Path) -> Path:
    """A private copy of the demo dataset plus DEMO_REGISTRY — safe to mutate."""
    (tmp_path / "data").mkdir()
    (tmp_path / "project_docs").mkdir()
    shutil.copy(demo_workspace / "data" / "beneficiaries.xlsx", tmp_path / "data")
    shutil.copy(demo_workspace / "project_docs" / "logframe.md", tmp_path / "project_docs")
    (tmp_path / "indicators.yaml").write_text(
        yaml.safe_dump(DEMO_REGISTRY, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return tmp_path
