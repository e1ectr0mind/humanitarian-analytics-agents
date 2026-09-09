"""Live smoke tests against real Kobo/Ona servers.

Run manually: uv run pytest -m remote -v
Set HAA_TEST_KOBO_URL / HAA_TEST_KOBO_TOKEN (and the ONA pair) first.
"""

import os
from pathlib import Path

import pytest

from haa.connectors.kobo import KoboConnector
from haa.connectors.ona import OnaConnector

pytestmark = pytest.mark.remote


def _env(*names: str) -> list[str]:
    values = [os.environ.get(n, "") for n in names]
    if not all(values):
        pytest.skip(f"env not set: {', '.join(names)}")
    return values


def test_kobo_live_roundtrip(tmp_path: Path) -> None:
    url, token = _env("HAA_TEST_KOBO_URL", "HAA_TEST_KOBO_TOKEN")
    forms = KoboConnector(url, token).list_forms()
    assert isinstance(forms, list)
    with_data = [f for f in forms if (f.submissions or 0) > 0]
    if not with_data:
        pytest.skip("no forms with submissions on this account")
    result = KoboConnector(url, token).pull(with_data[0].uid, tmp_path)
    assert result.rows > 0 and result.path.exists()


def test_ona_live_roundtrip(tmp_path: Path) -> None:
    url, token = _env("HAA_TEST_ONA_URL", "HAA_TEST_ONA_TOKEN")
    forms = OnaConnector(url, token).list_forms()
    assert isinstance(forms, list)
    with_data = [f for f in forms if (f.submissions or 0) > 0]
    if not with_data:
        pytest.skip("no forms with submissions on this account")
    result = OnaConnector(url, token).pull(with_data[0].uid, tmp_path)
    assert result.path.exists()
    assert result.rows == with_data[0].submissions, (result.rows, with_data[0].submissions)


def test_sharepoint_live_roundtrip(tmp_path: Path) -> None:
    workspace, profile = _env("HAA_TEST_SP_WORKSPACE", "HAA_TEST_SP_PROFILE")
    from haa.connectors.credentials import load_connection, make_connector

    conn, token = load_connection(Path(workspace), profile)
    with make_connector(conn, token) as connector:
        files = connector.list_forms()
        assert isinstance(files, list)
        if not files:
            pytest.skip("no tabular files visible to this profile")
        result = connector.pull(files[0].name, tmp_path)
    assert result.path.exists()
    assert result.path.stat().st_size == result.bytes > 0
