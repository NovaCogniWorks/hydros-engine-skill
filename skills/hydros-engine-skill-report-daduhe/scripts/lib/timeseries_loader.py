from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
JSON_SUFFIXES = {".json"}
CSV_SUFFIXES = {".csv"}


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def mojibake_score(text: str) -> int:
    suspicious_tokens = ("Ã", "Â", "æ", "ç", "å", "é", "è", "ä", "ï", "ö", "ü")
    return sum(text.count(token) for token in suspicious_tokens)


def repair_latin1_utf8_once(text: str) -> str:
    try:
        return text.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def repair_mojibake_text(text: str) -> str:
    if not text:
        return text

    current = text
    for _ in range(3):
        repaired = repair_latin1_utf8_once(current)
        if repaired == current:
            break
        if has_cjk(repaired) or mojibake_score(repaired) < mojibake_score(current):
            current = repaired
            continue
        break
    return current


def repair_dataframe_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    text_columns = df.select_dtypes(include=["object", "string"]).columns
    for column in text_columns:
        df[column] = df[column].map(
            lambda value: repair_mojibake_text(value) if isinstance(value, str) else value
        )
    return df


def is_excel_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in EXCEL_SUFFIXES


def load_timeseries_dataframe(path: str | Path, sheet_name: str | int = 0) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        return repair_dataframe_text_columns(pd.read_csv(file_path))
    if suffix in EXCEL_SUFFIXES:
        return repair_dataframe_text_columns(
            pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
        )
    raise ValueError(f"Unsupported timeseries file format: {file_path}")


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
