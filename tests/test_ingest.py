from conftest import drop
from portfolio.db import fetch_df
from portfolio.ingest import process_inbox


def test_same_file_under_a_new_name_is_skipped(store):
    drop(store, "transactions", "sample_transactions.xlsx", "first.xlsx")
    first = process_inbox()
    assert first["files"] == [("first.xlsx", "transactions", "loaded", 5)]

    drop(store, "transactions", "sample_transactions.xlsx", "renamed.xlsx")
    second = process_inbox()
    assert second["files"] == [("renamed.xlsx", "transactions", "duplicate", 0)]

    assert len(fetch_df("SELECT * FROM transactions")) == 5
    assert sorted(p.name for p in (store / "processed" / "transactions").iterdir()) == [
        "first.xlsx", "renamed.xlsx",
    ]


def test_unreadable_file_is_quarantined_and_loads_nothing(store):
    folder = store / "inbox" / "transactions"
    folder.mkdir(parents=True)
    (folder / "broken.xlsx").write_bytes(b"not a spreadsheet")

    result = process_inbox()

    assert result["files"] == []
    assert len(result["failed"]) == 1
    assert (store / "failed" / "transactions" / "broken.xlsx").exists()
    assert len(fetch_df("SELECT * FROM transactions")) == 0
    assert len(fetch_df("SELECT * FROM ingested_files")) == 0
