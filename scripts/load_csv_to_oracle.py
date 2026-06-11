from __future__ import annotations

import argparse
from itertools import chain
from pathlib import Path
import sys
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import anomaly_config
import anomaly_implementation as implementation
import anomaly_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load a configured CSV source into an Oracle input table.")
    parser.add_argument("--data-source-config", default="configs/data_source.yaml")
    parser.add_argument("--source-name", default=None)
    parser.add_argument("--connection-name", default=None)
    parser.add_argument("--owner", default=None)
    parser.add_argument("--table", required=True)
    parser.add_argument("--if-exists", choices=["replace", "truncate_insert", "append"], default="replace")
    parser.add_argument("--chunksize", type=int, default=5000)
    return parser.parse_args()


def resolve_connection(config: dict[str, Any], source: dict[str, Any], connection_name: str | None) -> dict[str, Any]:
    connections = config.get("connections", {})
    ref = connection_name or source.get("connection") or config.get("default_connection") or "default_oracle"
    if isinstance(ref, dict):
        return dict(ref)
    if str(ref) not in connections:
        raise KeyError(f"Connection not found in data source config: {ref}")
    return dict(connections[str(ref)])


def oracle_connect(connection: dict[str, Any]):
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("Oracle load requires oracledb. Install dependencies with: python -m pip install -r requirements.txt") from exc
    dsn = oracledb.makedsn(str(connection["host"]), int(connection["port"]), service_name=str(connection["service_name"]))
    return oracledb.connect(user=str(connection["user"]), password=str(connection["password"]), dsn=dsn)


def table_exists(cursor: Any, owner: str, table: str) -> bool:
    cursor.execute(
        "select count(*) from all_tables where owner = :owner_name and table_name = :table_name",
        {"owner_name": owner.upper(), "table_name": table.upper()},
    )
    return int(cursor.fetchone()[0]) > 0


def create_table(cursor: Any, owner: str, table: str, columns: list[str]) -> None:
    column_defs = ", ".join(f"{column} VARCHAR2(4000 CHAR)" for column in columns)
    cursor.execute(f"CREATE TABLE {owner}.{table} ({column_defs})")


def prepare_chunk(chunk: pd.DataFrame, column_map: dict[str, str]) -> pd.DataFrame:
    out = chunk.rename(columns=column_map)
    return out.astype(object).where(pd.notna(out), None)


def main() -> None:
    args = parse_args()
    config_path = Path(args.data_source_config)
    if not config_path.is_absolute():
        config_path = (PROJECT_ROOT / config_path).resolve()
    config = anomaly_config.load_yaml_config(config_path)
    source_name = args.source_name or config.get("active_source")
    source = dict(config.get("sources", {}).get(str(source_name), {}))
    if not source or str(source.get("type", "csv")).lower() != "csv":
        raise ValueError(f"CSV source not found: {source_name}")

    input_path = Path(str(source["path"]))
    if not input_path.is_absolute():
        input_path = (PROJECT_ROOT / input_path).resolve()
    encoding = str(source.get("encoding", "auto"))
    sep = str(source.get("sep", "auto"))
    if encoding == "auto" or sep == "auto":
        detected_encoding, detected_sep = anomaly_model.detect_read_options(input_path, encoding, sep)
        encoding = detected_encoding if encoding == "auto" else encoding
        sep = detected_sep if sep == "auto" else sep

    connection = resolve_connection(config, source, args.connection_name)
    owner = implementation.validate_oracle_identifier(args.owner or connection.get("owner"), "owner")
    table = implementation.validate_oracle_identifier(args.table, "table")

    reader = pd.read_csv(input_path, sep=sep, encoding=encoding, chunksize=args.chunksize)
    try:
        first_chunk = next(reader)
    except StopIteration as exc:
        raise ValueError(f"CSV is empty: {input_path}") from exc

    column_map = implementation.oracle_column_map([str(column) for column in first_chunk.columns])
    oracle_columns = [column_map[str(column)] for column in first_chunk.columns]
    placeholders = ", ".join(f":{idx + 1}" for idx in range(len(oracle_columns)))
    column_sql = ", ".join(oracle_columns)
    insert_sql = f"INSERT INTO {owner}.{table} ({column_sql}) VALUES ({placeholders})"

    total_rows = 0
    with oracle_connect(connection) as conn:
        cursor = conn.cursor()
        if args.if_exists == "replace" and table_exists(cursor, owner, table):
            cursor.execute(f"DROP TABLE {owner}.{table} PURGE")
        if not table_exists(cursor, owner, table):
            create_table(cursor, owner, table, oracle_columns)
        elif args.if_exists == "truncate_insert":
            cursor.execute(f"TRUNCATE TABLE {owner}.{table}")

        for chunk_no, chunk in enumerate(chain([first_chunk], reader), start=1):
            prepared = prepare_chunk(chunk, column_map)
            cursor.executemany(insert_sql, list(prepared.itertuples(index=False, name=None)))
            conn.commit()
            total_rows += len(prepared)
            print(f"chunk={chunk_no} rows_loaded={total_rows:,}", flush=True)

    print(
        {
            "source": str(input_path),
            "owner": owner,
            "table": table,
            "rows_loaded": total_rows,
            "column_map": column_map,
        }
    )


if __name__ == "__main__":
    main()
