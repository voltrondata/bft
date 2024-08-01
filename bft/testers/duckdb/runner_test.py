from bft.testers.duckdb.runner import type_to_duckdb_type


def test_type_to_duckdb_type():
    assert type_to_duckdb_type("interval") == "INTERVAL"
    assert type_to_duckdb_type("decimal<37, 3>") == "DECIMAL(37, 3)"
    assert type_to_duckdb_type("non_existent") is None
