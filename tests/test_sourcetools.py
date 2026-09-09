from pathlib import Path

import pytest

from haa.config import load_config
from haa.connectors.base import ConnectorError, PullResult, RemoteForm
from haa.connectors.credentials import Connection
from haa.core.telemetry import SessionTelemetry
from haa.core.tools.sourcetools import SOURCE_TOOL_NAMES, SourceToolbox


class StubConnector:
    def list_forms(self):
        return [RemoteForm(uid="a1", name="hh_survey", submissions=42)]

    def pull(self, form: str, dest_dir: Path) -> PullResult:
        path = dest_dir / "hh_survey.xlsx"
        path.write_bytes(b"fake")
        return PullResult(path=path, rows=42, form=RemoteForm("a1", "hh_survey", 42))

    def close(self) -> None:
        pass

    def __enter__(self) -> "StubConnector":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


@pytest.fixture()
def box(tmp_path: Path, monkeypatch) -> SourceToolbox:
    cfg = load_config(tmp_path)
    telemetry = SessionTelemetry(cfg.logs_dir / "t.jsonl")
    import haa.core.tools.sourcetools as st

    monkeypatch.setattr(
        st, "load_connection",
        lambda ws, name: (Connection(name, "kobo", "https://x"), "tok"),
    )
    monkeypatch.setattr(st, "make_connector", lambda conn, token: StubConnector())
    monkeypatch.setattr(
        st, "list_connections",
        lambda ws: [Connection("imc-kobo", "kobo", "https://x")],
    )
    return SourceToolbox(cfg, telemetry)


def test_tool_names_constant() -> None:
    assert SOURCE_TOOL_NAMES == [
        "mcp__sources__list_connections",
        "mcp__sources__list_remote_forms",
        "mcp__sources__pull_form",
    ]


def test_list_connections(box: SourceToolbox) -> None:
    out = box.list_connections()
    assert "imc-kobo" in out and "kobo" in out


def test_list_remote_forms(box: SourceToolbox) -> None:
    out = box.list_remote_forms("imc-kobo")
    assert "hh_survey" in out and "42" in out


def test_pull_form_summary_only(box: SourceToolbox) -> None:
    out = box.pull_form("imc-kobo", "hh_survey")
    assert "hh_survey.xlsx" in out and "42" in out
    assert "fake" not in out  # file content never leaks into the summary
    assert (box.config.data_dir / "hh_survey.xlsx").exists()


def test_pull_logs_telemetry(box: SourceToolbox) -> None:
    box.pull_form("imc-kobo", "hh_survey")
    log = box.telemetry.path.read_text(encoding="utf-8")
    assert '"kind": "pull"' in log and "hh_survey" in log


def test_connector_error_is_friendly(box: SourceToolbox, monkeypatch) -> None:
    import haa.core.tools.sourcetools as st

    def boom(conn, token):
        raise ConnectorError("Form 'x' not found. Available: hh_survey")

    monkeypatch.setattr(st, "make_connector", boom)
    out = box.pull_form("imc-kobo", "x")
    assert "not found" in out and "Traceback" not in out


def test_unknown_connection_friendly(box: SourceToolbox, monkeypatch) -> None:
    import haa.core.tools.sourcetools as st
    from haa.connectors.credentials import CredentialsError

    def missing(ws, name):
        raise CredentialsError("Unknown connection 'zzz'. Known: imc-kobo")

    monkeypatch.setattr(st, "load_connection", missing)
    assert "Unknown connection" in box.pull_form("zzz", "hh_survey")


def test_build_server_importable(box: SourceToolbox) -> None:
    from haa.core.tools.sourcetools import build_sources_server

    assert build_sources_server(box) is not None


class StubSharePoint:
    def __init__(self) -> None:
        self.refresh_events = [True]
        self.list_truncated = True

    def list_forms(self):
        return [RemoteForm(uid="i1", name="2026/june.xlsx", submissions=None)]

    def pull(self, form: str, dest_dir: Path) -> PullResult:
        path = dest_dir / "june.xlsx"
        path.write_bytes(b"x" * 2048)
        return PullResult(
            path=path, rows=0,
            form=RemoteForm("i1", "2026/june.xlsx", None), bytes=2048,
        )

    def close(self) -> None:
        pass

    def __enter__(self) -> "StubSharePoint":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


@pytest.fixture()
def sp_box(tmp_path: Path, monkeypatch) -> SourceToolbox:
    cfg = load_config(tmp_path)
    telemetry = SessionTelemetry(cfg.logs_dir / "t.jsonl")
    import haa.core.tools.sourcetools as st

    conn = Connection("imc-sp", "sharepoint", "https://x.sharepoint.com/sites/M",
                      tenant="t", client_id="c")
    monkeypatch.setattr(st, "load_connection", lambda ws, name: (conn, "tok"))
    monkeypatch.setattr(st, "make_connector", lambda conn, token: StubSharePoint())
    return SourceToolbox(cfg, telemetry)


def test_sharepoint_listing_wording_and_truncation(sp_box: SourceToolbox) -> None:
    out = sp_box.list_remote_forms("imc-sp")
    assert "Files on 'imc-sp'" in out and "- 2026/june.xlsx" in out
    assert "first 200" in out
    assert "submissions" not in out


def test_sharepoint_pull_reports_size_not_rows(sp_box: SourceToolbox) -> None:
    out = sp_box.pull_form("imc-sp", "2026/june.xlsx")
    assert "june.xlsx" in out and "2 KB" in out
    assert "submissions" not in out


def test_msauth_refresh_telemetry_logged(sp_box: SourceToolbox) -> None:
    sp_box.list_remote_forms("imc-sp")
    sp_box.pull_form("imc-sp", "2026/june.xlsx")
    log = sp_box.telemetry.path.read_text(encoding="utf-8")
    assert log.count('"kind": "msauth_refresh"') == 2


def test_connections_hint_mentions_sharepoint(tmp_path: Path, monkeypatch) -> None:
    cfg = load_config(tmp_path)
    import haa.core.tools.sourcetools as st

    monkeypatch.setattr(st, "list_connections", lambda ws: [])
    box = SourceToolbox(cfg, SessionTelemetry(cfg.logs_dir / "t.jsonl"))
    assert "sharepoint" in box.list_connections()
