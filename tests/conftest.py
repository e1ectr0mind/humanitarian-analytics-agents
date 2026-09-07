from pathlib import Path

import pytest

from haa.demo import generate


@pytest.fixture(scope="session")
def demo_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    ws = tmp_path_factory.mktemp("demo_ws")
    (ws / "data").mkdir()
    (ws / "project_docs").mkdir()
    generate(ws)
    return ws
