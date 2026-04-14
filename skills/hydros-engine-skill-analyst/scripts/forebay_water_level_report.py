#!/usr/bin/env python3
"""
生成基于闸站断面映射的仿真-实测 HTML 对比报告。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


METRIC_SPECS = [
    {
        "key": "forebay_level",
        "label": "闸前水位",
        "side": "front",
        "section_label": "闸前断面",
        "metrics_code": "water_level",
        "history_column": "闸前水位",
        "unit": "m",
        "drop_nonpositive": True,
    },
    {
        "key": "afterbay_level",
        "label": "闸后水位",
        "side": "back",
        "section_label": "闸后断面",
        "metrics_code": "water_level",
        "history_column": "闸后水位",
        "unit": "m",
        "drop_nonpositive": True,
    },
    {
        "key": "station_flow",
        "label": "流量",
        "side": "front",
        "section_label": "闸前断面",
        "metrics_code": "water_flow",
        "history_column": "流量",
        "unit": "m³/s",
        "drop_nonpositive": False,
    },
]

COLUMN_ALIASES = {
    "闸前水位": ["闸前水位", "闸前水位(m)", "闸前水位（m）", "上游水位", "上游水位(m)", "上游水位（m）"],
    "闸后水位": ["闸后水位", "闸后水位(m)", "闸后水位（m）", "下游水位", "下游水位(m)", "下游水位（m）"],
    "流量": ["流量", "过闸流量", "流量(m3/s)", "流量(m³/s)", "流量（m³/s）"],
}

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "assets" / "validation-report-template"
LEGACY_OUTPUT_NAMES = [
    "validation.html",
    "mdm_gate_validation_payload.json",
    "mdm_gate_validation_metrics.csv",
]


def prepare_output_dirs(output_dir: Path) -> dict[str, Path]:
    paths = {
        "report": output_dir / "report",
        "data": output_dir / "data",
        "charts": output_dir / "charts",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def cleanup_legacy_outputs(output_dir: Path) -> None:
    for name in LEGACY_OUTPUT_NAMES:
        legacy_path = output_dir / name
        if legacy_path.exists() and legacy_path.is_file():
            legacy_path.unlink()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成闸站映射驱动的节制闸仿真-实测 HTML 对比报告")
    parser.add_argument("simulation_file")
    parser.add_argument("history_excel")
    parser.add_argument("output_dir")
    parser.add_argument("--mdm-map-json", required=True, help="闸站断面映射 JSON")
    parser.add_argument("--biz-start-time", default="2017-12-27 00:00:00")
    parser.add_argument("--output-step-size", type=int, default=7200, help="仿真输出步长，单位秒")
    parser.add_argument("--title", default="节制闸闸前/闸后水位与流量仿真-实测对比分析报告")
    return parser.parse_args(argv)


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, np.floating):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if isinstance(value, np.integer):
        return int(value)
    return value


def format_number(value: Any, digits: int = 3) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def html_escape(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def normalize_column(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)).replace("（", "(").replace("）", ")").lower()


def find_history_column(columns: list[Any], expected: str) -> tuple[str | None, str]:
    normalized = {normalize_column(col): str(col) for col in columns}
    for alias in COLUMN_ALIASES.get(expected, [expected]):
        key = normalize_column(alias)
        if key in normalized:
            method = "EXACT_COLUMN" if alias == expected else "ALIAS_COLUMN"
            return normalized[key], method
    return None, "OBS_COLUMN_MISSING"


def compute_metrics(sim: np.ndarray, obs: np.ndarray) -> dict[str, Any]:
    valid = ~(np.isnan(sim) | np.isnan(obs))
    sim = sim[valid]
    obs = obs[valid]
    if len(sim) < 2:
        return {"n": int(len(sim))}

    diff = sim - obs
    rmse = float(np.sqrt(np.mean(diff**2)))
    mae = float(np.mean(np.abs(diff)))
    max_dev = float(np.max(np.abs(diff)))
    bias = float(np.mean(diff))
    obs_mean = float(np.mean(obs))
    ss_res = float(np.sum(diff**2))
    ss_tot = float(np.sum((obs - obs_mean) ** 2))
    nse = 1 - ss_res / ss_tot if ss_tot > 1e-12 else float("nan")
    corr = float(np.corrcoef(sim, obs)[0, 1]) if np.std(sim) > 1e-12 and np.std(obs) > 1e-12 else float("nan")
    mape_mask = np.abs(obs) > 1e-6
    mape = float(np.mean(np.abs(diff[mape_mask]) / np.abs(obs[mape_mask])) * 100) if mape_mask.any() else float("nan")

    return {
        "n": int(len(sim)),
        "rmse": rmse,
        "mae": mae,
        "max_deviation": max_dev,
        "bias": bias,
        "nse": nse,
        "correlation": corr,
        "mape": mape,
        "sim_mean": float(np.mean(sim)),
        "obs_mean": obs_mean,
        "sim_min": float(np.min(sim)),
        "sim_max": float(np.max(sim)),
        "obs_min": float(np.min(obs)),
        "obs_max": float(np.max(obs)),
        "sim_std": float(np.std(sim)),
        "obs_std": float(np.std(obs)),
    }


def rating_from_nse(nse: float | None) -> str:
    if nse is None or math.isnan(nse):
        return "无法评估"
    if nse > 0.75:
        return "优秀"
    if nse > 0.50:
        return "良好"
    if nse > 0.25:
        return "一般"
    return "较差"


def quality_flags(metrics: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    sim_std = safe_float(metrics.get("sim_std"))
    obs_std = safe_float(metrics.get("obs_std"))
    sim_min = safe_float(metrics.get("sim_min"))
    sim_max = safe_float(metrics.get("sim_max"))
    obs_min = safe_float(metrics.get("obs_min"))
    obs_max = safe_float(metrics.get("obs_max"))
    sim_mean = safe_float(metrics.get("sim_mean"))

    if sim_std is not None and sim_std < 1e-6:
        flags.append("SIM_CONSTANT")
    elif sim_std is not None and sim_mean not in (None, 0) and abs(sim_std / sim_mean) < 0.001:
        flags.append("SIM_NEAR_CONSTANT")
    if obs_std is not None and obs_std < 1e-6:
        flags.append("OBS_CONSTANT")
    if sim_min == 0 and sim_max == 0:
        flags.append("SIM_ZERO")
    if obs_min == 0 and obs_max == 0:
        flags.append("OBS_ZERO")
    return flags or ["OK"]


def load_mdm_map(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return list(raw.get("gates", []))
    if isinstance(raw, list):
        return raw
    raise ValueError("MDM 映射 JSON 必须是 list 或包含 gates 的对象")


def load_simulation(simulation_file: Path, start_time: datetime, output_step_size: int) -> pd.DataFrame:
    sim = pd.read_excel(simulation_file, sheet_name=0, engine="openpyxl")
    required = {"object_id", "object_name", "metrics_code", "data_index", "value"}
    missing = sorted(required - set(sim.columns))
    if missing:
        raise ValueError(f"仿真结果缺少字段: {', '.join(missing)}")

    sim = sim[sim["metrics_code"].isin({"water_level", "water_flow"})].copy()
    sim["object_id"] = pd.to_numeric(sim["object_id"], errors="coerce")
    sim["data_index"] = pd.to_numeric(sim["data_index"], errors="coerce")
    sim["value"] = pd.to_numeric(sim["value"], errors="coerce")
    sim = sim.dropna(subset=["object_id", "data_index", "value", "object_name"])
    sim["object_id"] = sim["object_id"].astype(int)
    sim["data_index"] = sim["data_index"].astype(int)
    sim["aligned_time"] = sim["data_index"].apply(lambda idx: start_time + timedelta(seconds=int(idx) * output_step_size))
    return sim


def load_history_sheet(excel_path: Path, sheet_name: str) -> pd.DataFrame:
    hist = pd.read_excel(excel_path, sheet_name=sheet_name, engine="openpyxl")
    if "日期" not in hist.columns:
        raise ValueError(f"历史 Sheet `{sheet_name}` 缺少日期列")
    hist["日期"] = pd.to_datetime(hist["日期"], errors="coerce")
    return hist.dropna(subset=["日期"]).copy()


def resolve_sim_series(
    sim: pd.DataFrame,
    section: dict[str, Any],
    metrics_code: str,
) -> tuple[pd.DataFrame, str, str]:
    section_id = section.get("object_id")
    section_name = str(section.get("object_name", ""))
    if section_id is not None:
        hit = sim[(sim["object_id"] == int(section_id)) & (sim["metrics_code"] == metrics_code)].copy()
        if not hit.empty:
            return hit, "MDM_OBJECT_ID", "high"

    if section_name:
        hit = sim[(sim["object_name"] == section_name) & (sim["metrics_code"] == metrics_code)].copy()
        if not hit.empty:
            return hit, "MDM_OBJECT_NAME", "medium"

    return pd.DataFrame(), "SIM_SERIES_MISSING", "none"


def build_comparison(
    sim: pd.DataFrame,
    history_excel: Path,
    mdm_gates: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    series_payload: list[dict[str, Any]] = []
    sheet_cache: dict[str, pd.DataFrame] = {}

    for gate in mdm_gates:
        sheet_name = gate.get("history_sheet", "")
        mdm_status = gate.get("mdm_status", "OK")
        sections = gate.get("sections", {}) or {}
        if mdm_status != "OK":
            rows.append(make_failure_row(gate, None, mdm_status, gate.get("mdm_error", "")))
            continue

        if sheet_name not in sheet_cache:
            try:
                sheet_cache[sheet_name] = load_history_sheet(history_excel, sheet_name)
            except Exception as exc:  # noqa: BLE001
                rows.append(make_failure_row(gate, None, "OBS_SHEET_ERROR", str(exc)))
                continue
        hist_sheet = sheet_cache[sheet_name]

        for spec in METRIC_SPECS:
            section = sections.get(spec["side"])
            if not section:
                rows.append(make_failure_row(gate, spec, "MDM_SECTION_MISSING", f"缺少 {spec['section_label']}"))
                continue

            obs_col, obs_method = find_history_column(list(hist_sheet.columns), spec["history_column"])
            if obs_col is None:
                rows.append(make_failure_row(gate, spec, obs_method, f"缺少历史列 {spec['history_column']}", section))
                continue

            sim_series, sim_method, confidence = resolve_sim_series(sim, section, spec["metrics_code"])
            if sim_series.empty:
                rows.append(make_failure_row(gate, spec, sim_method, "仿真结果中未找到该断面的对应序列", section))
                continue

            hist = hist_sheet[["日期", obs_col]].copy()
            hist[obs_col] = pd.to_numeric(hist[obs_col], errors="coerce")
            hist = hist.dropna(subset=["日期", obs_col])
            hist = hist[(hist["日期"] >= start_time) & (hist["日期"] <= end_time)].copy()
            hist = hist.rename(columns={"日期": "aligned_time", obs_col: "observed"})

            sim_obj = sim_series[["aligned_time", "data_index", "object_id", "object_name", "value"]].copy()
            sim_obj = sim_obj.rename(columns={"value": "simulated"})
            merged = pd.merge(sim_obj, hist[["aligned_time", "observed"]], on="aligned_time", how="inner")
            merged = merged.sort_values("aligned_time")
            raw_n = len(merged)
            if spec["drop_nonpositive"]:
                merged = merged[merged["simulated"] > 0].copy()
            dropped_placeholder_n = raw_n - len(merged)

            metrics = compute_metrics(merged["simulated"].to_numpy(float), merged["observed"].to_numpy(float))
            flags = quality_flags(metrics)
            sim_object_name = str(sim_series["object_name"].iloc[0])
            sim_object_id = int(sim_series["object_id"].iloc[0])
            match_method = f"{sim_method}+{obs_method}"
            row = {
                "history_sheet": sheet_name,
                "gate_keyword": gate.get("query_keyword", ""),
                "station_id": (gate.get("station") or {}).get("object_id"),
                "station_name": (gate.get("station") or {}).get("object_name", ""),
                "metric_key": spec["key"],
                "metric_label": spec["label"],
                "unit": spec["unit"],
                "side": spec["side"],
                "section_label": spec["section_label"],
                "history_column": obs_col,
                "mdm_section_id": section.get("object_id"),
                "mdm_section_name": section.get("object_name", ""),
                "mdm_section_alias": section.get("object_alias_name", ""),
                "sim_object_id": sim_object_id,
                "sim_object_name": sim_object_name,
                "metrics_code": spec["metrics_code"],
                "match_method": match_method,
                "confidence": confidence,
                "status": "OK",
                "raw_n": raw_n,
                "n": metrics.get("n", 0),
                "dropped_placeholder_n": dropped_placeholder_n,
                "metrics": metrics,
                "flags": flags,
                "rating": rating_from_nse(safe_float(metrics.get("nse"))),
                "start_time": merged["aligned_time"].min().strftime("%Y-%m-%d %H:%M:%S") if not merged.empty else "",
                "end_time": merged["aligned_time"].max().strftime("%Y-%m-%d %H:%M:%S") if not merged.empty else "",
                "message": "",
            }
            rows.append(row)

            series_payload.append(
                {
                    "sheet": sheet_name,
                    "metric": spec["label"],
                    "unit": spec["unit"],
                    "side": spec["section_label"],
                    "simObject": sim_object_name,
                    "simObjectId": sim_object_id,
                    "mdmSectionId": section.get("object_id"),
                    "mdmSectionName": section.get("object_name", ""),
                    "historyColumn": obs_col,
                    "matchMethod": match_method_label(match_method),
                    "matchMethodLabel": match_method_label(match_method),
                    "rating": row["rating"],
                    "flags": [quality_category(row)],
                    "qualityText": quality_text(row),
                    "qualityCategory": quality_category(row),
                    "metrics": metrics,
                    "data": [
                        {
                            "time": rec.aligned_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "step": int(rec.data_index),
                            "simulated": round(float(rec.simulated), 4),
                            "observed": round(float(rec.observed), 4),
                            "error": round(float(rec.simulated - rec.observed), 4),
                        }
                        for rec in merged.itertuples(index=False)
                    ],
                }
            )

    return rows, series_payload


def make_failure_row(
    gate: dict[str, Any],
    spec: dict[str, Any] | None,
    status: str,
    message: str,
    section: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = spec or {}
    station = gate.get("station") or {}
    section = section or {}
    return {
        "history_sheet": gate.get("history_sheet", ""),
        "gate_keyword": gate.get("query_keyword", ""),
        "station_id": station.get("object_id"),
        "station_name": station.get("object_name", ""),
        "metric_key": spec.get("key", ""),
        "metric_label": spec.get("label", "-"),
        "unit": spec.get("unit", ""),
        "side": spec.get("side", ""),
        "section_label": spec.get("section_label", ""),
        "history_column": spec.get("history_column", ""),
        "mdm_section_id": section.get("object_id"),
        "mdm_section_name": section.get("object_name", ""),
        "mdm_section_alias": section.get("object_alias_name", ""),
        "sim_object_id": None,
        "sim_object_name": "",
        "metrics_code": spec.get("metrics_code", ""),
        "match_method": status,
        "confidence": "none",
        "status": status,
        "raw_n": 0,
        "n": 0,
        "dropped_placeholder_n": 0,
        "metrics": {},
        "flags": [status],
        "rating": "无法评估",
        "start_time": "",
        "end_time": "",
        "message": message,
    }


def metric_average(rows: list[dict[str, Any]], metric_key: str, metric_name: str) -> float:
    values = [
        row["metrics"].get(metric_name, np.nan)
        for row in rows
        if row["status"] == "OK" and row["metric_key"] == metric_key and row["n"] >= 2
    ]
    return float(np.mean(values)) if values else float("nan")


QUALITY_FLAG_LABELS = {
    "OK": "正常",
    "SIM_CONSTANT": "仿真值基本不变",
    "SIM_NEAR_CONSTANT": "仿真变化过小",
    "SIM_ZERO": "仿真值全为 0",
    "OBS_CONSTANT": "实测值基本不变",
    "OBS_NEAR_CONSTANT": "实测变化过小",
    "OBS_ZERO": "实测值全为 0",
}

STATUS_LABELS = {
    "OK": "已匹配",
    "SIM_SERIES_MISSING": "缺少仿真数据",
    "OBS_COLUMN_MISSING": "缺少实测列",
    "OBS_SHEET_ERROR": "实测表读取失败",
    "MDM_SECTION_MISSING": "缺少仿真断面",
    "ERROR": "匹配异常",
}

MATCH_METHOD_LABELS = {
    "MDM_OBJECT_ID": "断面编号一致",
    "MDM_OBJECT_NAME": "断面名称一致",
    "EXACT_COLUMN": "数据项一致",
    "ALIAS_COLUMN": "数据项相近",
    "SIM_SERIES_MISSING": "仿真结果中未找到该指标",
    "OBS_COLUMN_MISSING": "实测表中未找到该列",
    "MDM_SECTION_MISSING": "闸站映射缺少该断面",
}


def quality_flag_label(flag: Any) -> str:
    return QUALITY_FLAG_LABELS.get(str(flag), str(flag))


def quality_category(row: dict[str, Any]) -> str:
    flags = [str(flag) for flag in row.get("flags", []) if flag]
    for flag in flags:
        if flag != "OK":
            return quality_flag_label(flag)
    if row.get("dropped_placeholder_n"):
        return "已剔除异常零值"
    return "正常"


def quality_text(row: dict[str, Any]) -> str:
    flags = [str(flag) for flag in row.get("flags", []) if flag]
    labels = [quality_flag_label(flag) for flag in flags if flag != "OK"]
    if not labels:
        labels = ["正常"]
    if row.get("dropped_placeholder_n"):
        labels.append(f"已剔除异常零值 {row['dropped_placeholder_n']} 条")
    return "；".join(labels)


def status_label(status: Any) -> str:
    return STATUS_LABELS.get(str(status), str(status))


def match_method_label(method: Any) -> str:
    parts = [part for part in str(method).split("+") if part]
    if "MDM_OBJECT_ID" in parts and "EXACT_COLUMN" in parts:
        return "匹配成功"
    if "MDM_OBJECT_ID" in parts and "ALIAS_COLUMN" in parts:
        return "匹配成功（数据项相近）"
    if "MDM_OBJECT_NAME" in parts and "EXACT_COLUMN" in parts:
        return "名称一致"
    if "MDM_OBJECT_NAME" in parts and "ALIAS_COLUMN" in parts:
        return "名称相近"
    return "；".join(MATCH_METHOD_LABELS.get(part, part) for part in parts) if parts else "-"


def mapping_message(row: dict[str, Any]) -> str:
    status = row.get("status")
    if status == "OK":
        keyword = row.get("gate_keyword") or row.get("history_sheet") or "-"
        station = row.get("station_name") or "-"
        section_label = row.get("section_label") or "对应断面"
        metric = row.get("metric_label") or "指标"
        target = simulation_object_label(row)
        return f"实测表按“{keyword}”对应到仿真闸站“{station}”；{metric}取{section_label}对象“{target}”参与对比。"
    message = str(row.get("message") or "").replace("MDM", "仿真")
    return message or status_label(status)


def simulation_object_label(row: dict[str, Any]) -> str:
    name = row.get("sim_object_name") or row.get("mdm_section_name") or "-"
    object_id = row.get("mdm_section_id") or row.get("sim_object_id")
    if object_id:
        return f"{name}（{object_id}）"
    return str(name)


def render_validation_template(replacements: dict[str, str]) -> str:
    template_path = TEMPLATE_DIR / "index.html"
    template = template_path.read_text(encoding="utf-8")
    unresolved = []
    for key, value in replacements.items():
        placeholder = f"__{key}__"
        if placeholder not in template:
            unresolved.append(placeholder)
        template = template.replace(placeholder, value)

    known_placeholders = "|".join(re.escape(f"__{key}__") for key in replacements)
    leftovers = re.findall(known_placeholders, template) if known_placeholders else []
    if unresolved or leftovers:
        details = ", ".join(sorted(set(unresolved + leftovers)))
        raise ValueError(f"验证报告模板占位符处理失败: {details}")
    return template


def build_html(
    title: str,
    sim_file: Path,
    history_file: Path,
    mdm_map_file: Path,
    rows: list[dict[str, Any]],
    series_payload: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    output_step_size: int,
) -> str:
    ok_rows = [row for row in rows if row["status"] == "OK" and row["n"] >= 2]
    ok_gate_count = len({row["history_sheet"] for row in ok_rows})
    failed_rows = [row for row in rows if row["status"] != "OK"]
    avg_level_rmse = np.mean(
        [row["metrics"].get("rmse", np.nan) for row in ok_rows if row["metrics_code"] == "water_level"]
    ) if ok_rows else np.nan
    avg_flow_rmse = metric_average(ok_rows, "station_flow", "rmse")
    worst_rows = sorted(ok_rows, key=lambda row: row["metrics"].get("rmse", -1), reverse=True)[:5]
    best_rows = sorted(ok_rows, key=lambda row: row["metrics"].get("rmse", float("inf")))[:5]

    metric_rows = "\n".join(
        f"""
        <tr data-metric="{html_escape(row['metric_label'])}" data-rating="{html_escape(row['rating'])}" data-quality="{html_escape(quality_category(row))}">
          <td>{idx}</td>
          <td>{html_escape(row['history_sheet'])}</td>
          <td>{html_escape(row['metric_label'])}</td>
          <td>{html_escape(simulation_object_label(row))}</td>
          <td>{row['n']} / {row['raw_n']}</td>
          <td>{format_number(row['metrics'].get('rmse'))}</td>
          <td>{format_number(row['metrics'].get('mae'))}</td>
          <td>{format_number(row['metrics'].get('bias'))}</td>
          <td>{format_number(row['metrics'].get('nse'))}</td>
          <td>{format_number(row['metrics'].get('correlation'))}</td>
          <td>{html_escape(row['unit'])}</td>
          <td><span class="pill">{html_escape(row['rating'])}</span></td>
          <td>{html_escape(quality_text(row))}</td>
        </tr>
        """
        for idx, row in enumerate(ok_rows, 1)
    )

    mapping_rows = "\n".join(
        f"""
        <tr data-metric="{html_escape(row['metric_label'])}" data-status="{html_escape(status_label(row['status']))}">
          <td>{idx}</td>
          <td>{html_escape(row['history_sheet'])}</td>
          <td>{html_escape(row['gate_keyword'])}</td>
          <td>{html_escape(row['station_name'] or '-')}</td>
          <td>{html_escape(row['metric_label'])}</td>
          <td>{html_escape(simulation_object_label(row))}</td>
        </tr>
        """
        for idx, row in enumerate(rows, 1)
    )

    quality_rows = "\n".join(
        f"""
        <tr data-metric="{html_escape(row['metric_label'])}" data-quality="{html_escape(quality_category(row))}">
          <td>{html_escape(row['history_sheet'])}</td>
          <td>{html_escape(row['metric_label'])}</td>
          <td>{html_escape(simulation_object_label(row))}</td>
          <td>{format_number(row['metrics'].get('sim_std'), 5)}</td>
          <td>{format_number(row['metrics'].get('obs_std'), 5)}</td>
          <td>{html_escape(quality_text(row))}</td>
        </tr>
        """
        for row in ok_rows
    )

    payload = json_safe({
        "series": series_payload,
        "summary": {
            "count": len(rows),
            "okCount": len(ok_rows),
            "failedCount": len(failed_rows),
            "okGateCount": ok_gate_count,
            "avgLevelRmse": None if math.isnan(avg_level_rmse) else round(float(avg_level_rmse), 4),
            "avgFlowRmse": None if math.isnan(avg_flow_rmse) else round(float(avg_flow_rmse), 4),
            "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "stepHours": output_step_size / 3600,
        },
    })

    best_text = "；".join(
        f"{row['history_sheet']} {row['metric_label']} RMSE={format_number(row['metrics'].get('rmse'))}{row['unit']}"
        for row in best_rows
    )
    worst_text = "；".join(
        f"{row['history_sheet']} {row['metric_label']} RMSE={format_number(row['metrics'].get('rmse'))}{row['unit']}"
        for row in worst_rows
    )

    hero_description = (
        "本报告以当前仿真结果和闸站断面映射为入口，先用闸前/闸后断面编号精确匹配仿真 Excel "
        "中的 <strong>water_level</strong> 与 <strong>water_flow</strong>，再用闸站名、闸门名和别名"
        "从历史 Excel 全量 Sheet 中反查观测表。历史侧在已命中的 Sheet 内优先精确读取 <strong>闸前水位</strong>、"
        "<strong>闸后水位</strong>、<strong>流量</strong> 三列。仿真时间按 "
        f"{start_time:%Y-%m-%d %H:%M:%S} 与 {output_step_size / 3600:g} 小时输出步长构造。"
    )
    source_info = (
        f"仿真文件：{html_escape(sim_file)}<br>"
        f"历史文件：{html_escape(history_file)}<br>"
        f"闸站映射文件：{html_escape(mdm_map_file)}"
    )
    metric_cards = "\n".join(
        f'<article class="card"><small>{label}</small><strong>{value}</strong></article>'
        for label, value in [
            ("成功指标组", str(len(ok_rows))),
            ("成功闸站数", str(ok_gate_count)),
            ("水位平均 RMSE", f"{format_number(avg_level_rmse)} m"),
            ("流量平均 RMSE", f"{format_number(avg_flow_rmse)} m³/s"),
        ]
    )

    return render_validation_template(
        {
            "REPORT_TITLE": html_escape(title),
            "HERO_DESCRIPTION": hero_description,
            "SOURCE_INFO": source_info,
            "METRIC_CARDS": metric_cards,
            "BEST_TEXT": html_escape(best_text or "无"),
            "WORST_TEXT": html_escape(worst_text or "无"),
            "METRIC_ROWS": metric_rows,
            "MAPPING_ROWS": mapping_rows,
            "QUALITY_ROWS": quality_rows,
            "INLINE_PAYLOAD_JSON": json.dumps(payload, ensure_ascii=False),
        }
    )


def main() -> None:
    args = parse_args(sys.argv[1:])
    sim_path = Path(args.simulation_file).resolve()
    history_path = Path(args.history_excel).resolve()
    mdm_map_path = Path(args.mdm_map_json).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = prepare_output_dirs(output_dir)
    cleanup_legacy_outputs(output_dir)

    start_time = datetime.strptime(args.biz_start_time, "%Y-%m-%d %H:%M:%S")
    sim = load_simulation(sim_path, start_time, args.output_step_size)
    max_index = int(sim["data_index"].max())
    end_time = start_time + timedelta(seconds=max_index * args.output_step_size)
    mdm_gates = load_mdm_map(mdm_map_path)

    rows, series_payload = build_comparison(sim, history_path, mdm_gates, start_time, end_time)
    rows = json_safe(rows)
    series_payload = json_safe(series_payload)

    payload_path = paths["data"] / "mdm_gate_validation_payload.json"
    payload_path.write_text(
        json.dumps({"rows": rows, "series": series_payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    html = build_html(
        args.title,
        sim_path,
        history_path,
        mdm_map_path,
        rows,
        series_payload,
        start_time,
        end_time,
        args.output_step_size,
    )
    validation_path = paths["report"] / "validation.html"
    validation_path.write_text(html, encoding="utf-8")

    csv_rows = []
    for row in rows:
        metrics = row.get("metrics") or {}
        csv_rows.append(
            {
                "history_sheet": row["history_sheet"],
                "gate_keyword": row["gate_keyword"],
                "station_id": row["station_id"],
                "station_name": row["station_name"],
                "metric_label": row["metric_label"],
                "history_column": row["history_column"],
                "side": row["side"],
                "section_label": row["section_label"],
                "mdm_section_id": row["mdm_section_id"],
                "mdm_section_name": row["mdm_section_name"],
                "sim_object_id": row["sim_object_id"],
                "sim_object_name": row["sim_object_name"],
                "metrics_code": row["metrics_code"],
                "match_method": row["match_method"],
                "confidence": row["confidence"],
                "status": row["status"],
                "message": row["message"],
                "raw_n": row["raw_n"],
                "n": row["n"],
                "dropped_placeholder_n": row["dropped_placeholder_n"],
                "rmse": metrics.get("rmse"),
                "mae": metrics.get("mae"),
                "max_deviation": metrics.get("max_deviation"),
                "bias": metrics.get("bias"),
                "nse": metrics.get("nse"),
                "correlation": metrics.get("correlation"),
                "rating": row["rating"],
                "flags": ",".join(row["flags"]),
            }
        )
    metrics_path = paths["data"] / "mdm_gate_validation_metrics.csv"
    pd.DataFrame(csv_rows).to_csv(metrics_path, index=False, encoding="utf-8-sig")

    print(f"HTML 报告: {validation_path}")
    print(f"指标 CSV: {metrics_path}")
    print(f"Payload: {payload_path}")


if __name__ == "__main__":
    main()
