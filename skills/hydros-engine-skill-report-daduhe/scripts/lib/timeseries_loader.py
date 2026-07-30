from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
JSON_SUFFIXES = {".json"}
CSV_SUFFIXES = {".csv"}
STATION_SIDE_COLUMNS = {
    "water_level": ("front_water_level", "back_water_level"),
    "water_flow": ("front_water_flow", "back_water_flow"),
}
STATION_SIDE_LABELS = {"front": "前侧", "back": "后侧"}
POSITION_SIDE_MAP = {"up_stream": "front", "down_stream": "back"}


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
        return add_series_identity_columns(repair_dataframe_text_columns(pd.read_csv(file_path)))
    if suffix in EXCEL_SUFFIXES:
        return add_series_identity_columns(
            repair_dataframe_text_columns(
                pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
            )
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
    side = infer_station_series_side(normalized)
    normalized["series_side"] = side
    normalized["series_name"] = build_series_name(normalized.get("object_name"), side)
    return normalized


def is_present(value: Any) -> bool:
    if value is None:
        return False
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def infer_station_series_side(record: dict[str, Any]) -> str | None:
    """Infer the spatial side carried by a station or device metric row."""
    position_side = POSITION_SIDE_MAP.get(str(record.get("position_code") or ""))
    if position_side:
        return position_side
    if str(record.get("object_type") or "") != "GateStation":
        return None
    side_columns = STATION_SIDE_COLUMNS.get(str(record.get("metrics_code") or ""))
    if not side_columns:
        return None
    front_column, back_column = side_columns
    front_present = is_present(record.get(front_column))
    back_present = is_present(record.get(back_column))
    if front_present and not back_present:
        return "front"
    if back_present and not front_present:
        return "back"
    return None


def build_series_name(object_name: Any, side: str | None) -> str:
    name = str(object_name or "")
    label = STATION_SIDE_LABELS.get(side or "")
    return f"{name}（{label}）" if label else name


def add_series_identity_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add a side-aware series identity without changing object_name."""
    working = df.copy()
    if working.empty:
        working["series_side"] = pd.Series(dtype="object")
        working["series_name"] = pd.Series(dtype="object")
        return working

    working["series_side"] = None
    if {"object_type", "metrics_code"}.issubset(working.columns):
        if "position_code" in working.columns:
            for position_code, side in POSITION_SIDE_MAP.items():
                position_mask = working["position_code"].fillna("").astype(str).eq(position_code)
                working.loc[position_mask, "series_side"] = side
        for metric, (front_column, back_column) in STATION_SIDE_COLUMNS.items():
            if front_column not in working.columns or back_column not in working.columns:
                continue
            base_mask = (
                working["object_type"].astype(str).eq("GateStation")
                & working["metrics_code"].astype(str).eq(metric)
            )
            front_mask = base_mask & working[front_column].notna() & working[back_column].isna()
            back_mask = base_mask & working[back_column].notna() & working[front_column].isna()
            working.loc[front_mask & working["series_side"].isna(), "series_side"] = "front"
            working.loc[back_mask & working["series_side"].isna(), "series_side"] = "back"

    names = working.get("object_name", pd.Series("", index=working.index)).fillna("").astype(str)
    working["series_name"] = names
    for side, label in STATION_SIDE_LABELS.items():
        mask = working["series_side"].eq(side)
        working.loc[mask, "series_name"] = names[mask] + f"（{label}）"
    return working
