"""The two schema constants must declare the same shape.

save_plan inserts into a2a_messages positionally with 11 unnamed values, so
column ORDER is load-bearing, not just column names.
"""

import re

from flaskapp.database import SCHEMA_POSTGRES, SCHEMA_SQLITE

TABLE = re.compile(
    r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.DOTALL
)


def columns(schema: str) -> dict[str, list[str]]:
    tables = {}
    for name, body in TABLE.findall(schema):
        names = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("--") or line.upper().startswith(
                ("PRIMARY KEY", "UNIQUE", "CHECK", "FOREIGN KEY")
            ):
                continue
            names.append(line.split()[0])
        tables[name] = names
    return tables


def test_both_schemas_declare_the_same_tables():
    assert set(columns(SCHEMA_SQLITE)) == set(columns(SCHEMA_POSTGRES))


def test_both_schemas_declare_the_same_columns_in_the_same_order():
    sqlite_tables, postgres_tables = columns(SCHEMA_SQLITE), columns(SCHEMA_POSTGRES)
    for table, names in sqlite_tables.items():
        assert names == postgres_tables[table], f"{table} column order differs"
