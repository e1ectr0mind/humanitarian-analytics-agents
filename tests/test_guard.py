from haa.core.sandbox.guard import filter_output


def test_redacts_phones() -> None:
    res = filter_output("call +380671234567 or 0509876543", row_cap=50, size_cap=32768)
    assert "+380671234567" not in res.text
    assert "0509876543" not in res.text
    assert res.text.count("[REDACTED]") == 2
    assert any("redact" in n.lower() for n in res.notices)


def test_redacts_gps_pairs() -> None:
    res = filter_output("point at 48.53421, 35.12345 ok", row_cap=50, size_cap=32768)
    assert "48.53421" not in res.text


def test_plain_aggregates_untouched() -> None:
    table = "oblast  count\nДонецька  512\nСумська  380"
    res = filter_output(table, row_cap=50, size_cap=32768)
    assert res.text == table
    assert res.notices == []


def test_line_cap() -> None:
    text = "\n".join(f"row {i}" for i in range(500))
    res = filter_output(text, row_cap=50, size_cap=32768)
    assert len(res.text.splitlines()) == 60  # row_cap + 10
    assert any("440 more lines" in n for n in res.notices)


def test_size_cap() -> None:
    res = filter_output("x" * 100_000, row_cap=1000, size_cap=1000)
    assert len(res.text.encode()) <= 1000
    assert any("truncated" in n.lower() for n in res.notices)
