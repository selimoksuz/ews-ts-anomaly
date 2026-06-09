from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def read_oracle_frame(oracle_config: dict[str, Any]) -> pd.DataFrame:
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("Oracle read requires oracledb. Install dependencies with: python -m pip install -r requirements.txt") from exc

    import anomaly_implementation as implementation

    connection_cfg = oracle_config.get("connection", {})
    source_cfg = oracle_config.get("input", {})
    info_dir = Path(str(connection_cfg.get("info_dir", implementation.DEFAULT_ORACLE_INFO_DIR)))
    config_file = str(connection_cfg.get("config_file", "ora_config.ini"))
    section = connection_cfg.get("section")
    selected_section, conn_cfg = implementation.resolve_oracle_connection_config(
        info_dir,
        config_file,
        section,
        connection_cfg,
    )

    query = source_cfg.get("query")
    if query:
        sql = str(query)
    else:
        owner = implementation.validate_oracle_identifier(source_cfg.get("owner") or connection_cfg.get("owner"), "owner")
        table = implementation.validate_oracle_identifier(source_cfg.get("table"), "input table")
        sql = f"select * from {owner}.{table}"

    dsn = implementation.oracle_dsn(conn_cfg)
    with oracledb.connect(user=conn_cfg["user"], password=conn_cfg["password"], dsn=dsn) as connection:
        frame = pd.read_sql(sql, connection)
    frame.attrs["oracle_section"] = selected_section
    return frame


def write_source_snapshot(frame: pd.DataFrame, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path
