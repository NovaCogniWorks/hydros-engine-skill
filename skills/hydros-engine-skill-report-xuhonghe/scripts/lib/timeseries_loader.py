from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
JSON_SUFFIXES = {".json"}
CSV_SUFFIXES = {".csv"}


def is_excel_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in EXCEL_SUFFIXES


def load_timeseries_dataframe(path: str | Path, sheet_name: str | int = 0) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        return pd.read_csv(file_path)
    if suffix in EXCEL_SUFFIXES:
        return pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
    raise ValueError(f"不支持的结果文件格式: {file_path}")


def load_timeseries_records(path: str | Path) -> list[dict[str, Any]]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in JSON_SUFFIXES:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        return raw["result"]["data"]

    df = load_timeseries_dataframe(file_path)
    records = df.to_dict(orient="records")
    return [normalize_record(record) for record in records]


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    if "data_index" in normalized and normalized["data_index"] is not None:
        normalized["data_index"] = int(float(normalized["data_index"]))
    if "value" in normalized and normalized["value"] is not None:
        normalized["value"] = float(normalized["value"])
    return normalized
