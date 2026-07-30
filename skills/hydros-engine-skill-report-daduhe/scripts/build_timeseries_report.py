#!/usr/bin/env python3
"""
从 Hydros 时序结果文件生成 HTML + Markdown 分析报告。

用法:
    python build_timeseries_report.py <timeseries_file> [output_dir]
        [--total-steps N] [--sim-step-size SECONDS] [--output-step-size SECONDS]
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from build_longitudinal_profile import build_dataset as build_longitudinal_dataset
from build_longitudinal_profile import extract_block_value
from build_longitudinal_profile import extract_nested_block
from build_longitudinal_profile import parse_cross_section_children
from build_longitudinal_profile import parse_cross_sections
from build_longitudinal_profile import save_profile_png
from build_longitudinal_profile import split_object_blocks
from lib.timeseries_loader import load_timeseries_dataframe
from lib.url_utils import normalize_remote_url

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT.parent.parent
TEMPLATE_HTML = ROOT / "assets" / "hydros-report-template" / "index.html"
CHART_SCRIPT = ROOT / "scripts" / "generate_charts.py"
WATER_LEVEL_DROP_WARN_RATE_M_PER_H = 0.15
WATER_LEVEL_DROP_CONTROL_RATE_M_PER_H = 0.3
GATE_OPENING_MIN_EFFECTIVE_CHANGE_M = 0.03
TURBINE_OUTPUT_REQUIRED_SCENARIOS = {"200060"}


@dataclass
class RuntimeConfig:
    total_steps: int | None
    sim_step_size: int | None
    output_step_size: int | None
    sampled_steps: list[int]
    csv_step_interval: int | None
    expected_sample_count: int | None
    axis_mode: str
    axis_label: str
    axis_note: str
    sample_step_note: str
    has_unreliable_time_axis: bool


def format_seconds_text(total_seconds: int | float | None) -> str | None:
    if total_seconds is None:
        return None
    return f"{int(total_seconds)} 秒（{format_duration_text(total_seconds)}）"


def run_command(args: list[str]) -> None:
    subprocess.run(args, check=True)


def prepare_output_dirs(output_dir: Path) -> dict[str, Path]:
    paths = {
        "report": output_dir / "report",
        "charts": output_dir / "charts",
        "data": output_dir / "data",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def resolve_task_output_dir(csv_path: Path, df: pd.DataFrame, explicit_output_dir: str | None) -> Path:
    if explicit_output_dir:
        return Path(explicit_output_dir).resolve()

    task_id = None
    if "biz_scenario_instance_id" in df.columns and not df["biz_scenario_instance_id"].dropna().empty:
        task_id = str(df["biz_scenario_instance_id"].dropna().iloc[0]).strip()
    safe_task_id = task_id or csv_path.stem
    return PROJECT_ROOT / "output" / safe_task_id


def load_dataframe(csv_path: Path) -> pd.DataFrame:
    df = load_timeseries_dataframe(csv_path)
    required_columns = {
        "biz_scenario_id",
        "data_index",
        "object_name",
        "object_type",
        "metrics_code",
        "value",
    }
    missing_columns = sorted(required_columns - set(df.columns))
    if missing_columns:
        raise ValueError(f"结果文件缺少必需列: {', '.join(missing_columns)}")
    if df.empty:
        raise ValueError("结果文件不包含任何数据行")

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["data_index"] = pd.to_numeric(df["data_index"], errors="coerce")
    if df["data_index"].dropna().empty:
        raise ValueError("结果文件中 data_index 全部无效，疑似坏文件或残缺文件")
    if df["value"].dropna().empty:
        raise ValueError("结果文件中 value 全部无效，疑似坏文件或残缺文件")
    return df


def round_number(value: float | int | None, digits: int = 2) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), digits)


def describe_variation_window(group: pd.DataFrame) -> str:
    if group.empty or "data_index" not in group.columns or "value" not in group.columns:
        return "全过程"

    ordered = group.sort_values("data_index").copy()
    min_row = ordered.loc[ordered["value"].idxmin()]
    max_row = ordered.loc[ordered["value"].idxmax()]
    start_step = int(min(min_row["data_index"], max_row["data_index"]))
    end_step = int(max(min_row["data_index"], max_row["data_index"]))
    if start_step == end_step:
        return f"展示步 {start_step} 附近"
    return f"展示步 {start_step} 到 {end_step} 之间"


def create_object_sort_key(location_map: dict[str, float]):
    def sort_key(name: str) -> tuple[float, str]:
        loc = location_map.get(name, float('inf'))
        if loc == float('inf'):
            for k in location_map:
                if name.startswith(k):
                    loc = location_map[k]
                    break
        return (loc, name)
    return sort_key


def format_datetime_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def format_duration_text(total_seconds: int | float | None) -> str | None:
    if total_seconds is None:
        return None
    remaining = int(total_seconds)
    days, remaining = divmod(remaining, 86_400)
    hours, remaining = divmod(remaining, 3_600)
    minutes, _ = divmod(remaining, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes and len(parts) < 2:
        parts.append(f"{minutes}分钟")
    if not parts:
        parts.append("0分钟")
    return "".join(parts)


def parse_datetime_text(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None


def fetch_scenario_metadata(scenario_yaml_url: str) -> dict[str, Any] | None:
    local_path = Path(scenario_yaml_url).expanduser()
    try:
        if local_path.exists():
            text = local_path.read_text(encoding="utf-8")
        else:
            with urllib.request.urlopen(normalize_remote_url(scenario_yaml_url), timeout=20) as response:
                text = response.read().decode("utf-8")
    except Exception:
        return None

    def extract(key: str) -> str | None:
        match = re.search(rf"^\s*{re.escape(key)}:\s*(.+)$", text, re.M)
        return match.group(1).strip() if match else None

    total_steps = extract("total_steps")
    sim_step_size = extract("sim_step_size")
    output_step_size = extract("output_step_size")
    start_time = extract("biz_start_time")
    return {
        "scenario_yaml_url": scenario_yaml_url,
        "scenario_yaml_id": Path(urlsplit(scenario_yaml_url).path).name,
        "scenario_name": extract("biz_scenario_name"),
        "waterway_id": extract("waterway_id"),
        "waterway_name": extract("waterway_name"),
        "objects_yaml_url": extract("hydros_objects_modeling_url"),
        "total_steps": int(total_steps) if total_steps and total_steps.isdigit() else None,
        "sim_step_size": int(sim_step_size) if sim_step_size and sim_step_size.isdigit() else None,
        "output_step_size": int(output_step_size) if output_step_size and output_step_size.isdigit() else None,
        "biz_start_time": start_time,
    }


def cache_objects_yaml(data_dir: Path, objects_yaml_url: str | None) -> Path | None:
    if not objects_yaml_url:
        return None

    target_path = data_dir / "objects.yaml"
    local_path = Path(objects_yaml_url).expanduser()
    if local_path.exists():
        if local_path.resolve() != target_path.resolve():
            shutil.copyfile(local_path, target_path)
        return target_path
    with urllib.request.urlopen(normalize_remote_url(objects_yaml_url), timeout=20) as response:
        target_path.write_text(response.read().decode("utf-8"), encoding="utf-8")
    return target_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 Hydros 时序结果文件生成 HTML + Markdown 分析报告")
    parser.add_argument("timeseries_file")
    parser.add_argument("output_dir", nargs="?")
    parser.add_argument("--scenario-yaml-url", default=None, help="显式传入场景 YAML 地址")
    parser.add_argument("--objects-yaml-url", default=None, help="显式传入 objects.yaml 地址；优先于场景 YAML 中的配置")
    parser.add_argument("--total-steps", type=int, default=None)
    parser.add_argument("--sim-step-size", type=int, default=None, help="计算步长，单位秒")
    parser.add_argument("--output-step-size", type=int, default=None, help="输出步长，单位秒")
    parser.add_argument("--llm-name", default=None, help="当前使用的模型名称；如 gpt-5.4 / claude-sonnet")
    parser.add_argument("--mpc-results-json", default=None, help="可选传入 get_mpc_simulation_results 的 JSON 响应，用于补齐梯级电站场景的水轮机出力")
    parser.add_argument("--scenario-events-json", default=None, help="可选传入 get_simulation_scenario_events 的 JSON 响应，用于在报告中展示工况事件")
    return parser.parse_args(argv)


def resolve_llm_name(explicit_name: str | None) -> str | None:
    if explicit_name and explicit_name.strip():
        return explicit_name.strip()

    for env_name in ["LLM_NAME", "LLM_MODEL", "MODEL", "OPENAI_MODEL", "CODEX_MODEL", "ANTHROPIC_MODEL"]:
        value = os.getenv(env_name)
        if value and value.strip():
            return value.strip()
    return None


def resolve_runtime_config(
    unique_steps: list[int], scenario_meta: dict[str, Any] | None, args: argparse.Namespace
) -> RuntimeConfig:
    csv_step_interval = sorted({b - a for a, b in zip(unique_steps, unique_steps[1:])})
    stable_csv_interval = csv_step_interval[0] if len(csv_step_interval) == 1 else None
    total_steps = args.total_steps if args.total_steps is not None else (scenario_meta or {}).get("total_steps")
    sim_step_size = args.sim_step_size if args.sim_step_size is not None else (scenario_meta or {}).get("sim_step_size")
    output_step_size = (
        args.output_step_size if args.output_step_size is not None else (scenario_meta or {}).get("output_step_size")
    )

    if total_steps is not None:
        expected_sample_count = total_steps + 1 if unique_steps and min(unique_steps) == 0 else total_steps
    else:
        expected_sample_count = None

    has_unreliable_time_axis = False
    axis_mode = "csv_index"
    axis_label = "步长"
    axis_note = "结果文件里的时间信息不够完整，图表横轴按结果输出先后顺序显示。"
    sample_step_note = f"第 {unique_steps[0]} 次 ~ 第 {unique_steps[-1]} 次输出"

    if stable_csv_interval is not None and stable_csv_interval > 1:
        axis_mode = "calculation_step"
        axis_label = "仿真步"
        axis_note = "结果文件里的序号可以对应到仿真推进过程，图表横轴显示仿真进行到第几步。"
        sample_step_note = f"仿真第 {unique_steps[0]} 步 ~ 第 {unique_steps[-1]} 步"
    elif output_step_size is not None and stable_csv_interval == 1 and expected_sample_count is not None:
        if abs(expected_sample_count - len(unique_steps)) <= 1:
            axis_mode = "output_ordinal"
            axis_label = "步长"
            axis_note = (
                "结果文件里的序号更接近结果输出顺序，图表横轴按结果输出先后顺序显示，"
                "并结合本次仿真设置做时长判断。"
            )
            sample_step_note = f"第 {unique_steps[0]} 次 ~ 第 {unique_steps[-1]} 次输出"
        else:
            has_unreliable_time_axis = True
            axis_mode = "csv_index_unreliable"
            axis_label = "步长"
            axis_note = (
                f"结果文件目前只看到 {len(unique_steps)} 次结果输出，但按本次设置原本应有约 {expected_sample_count} 次结果输出；"
                "结果文件的时间信息可能不完整，因此图表横轴仅按结果输出先后顺序显示。"
            )
            sample_step_note = f"第 {unique_steps[0]} 次 ~ 第 {unique_steps[-1]} 次输出（时间信息不完整）"

    return RuntimeConfig(
        total_steps=total_steps,
        sim_step_size=sim_step_size,
        output_step_size=output_step_size,
        sampled_steps=unique_steps,
        csv_step_interval=stable_csv_interval,
        expected_sample_count=expected_sample_count,
        axis_mode=axis_mode,
        axis_label=axis_label,
        axis_note=axis_note,
        sample_step_note=sample_step_note,
        has_unreliable_time_axis=has_unreliable_time_axis,
    )


def detect_placeholder_steps(metric_df: pd.DataFrame) -> list[int]:
    placeholder_steps: list[int] = []
    focus_df = metric_df[metric_df["object_type"] == "CrossSection"].copy()
    if focus_df.empty:
        focus_df = metric_df
    for step, group in focus_df.groupby("data_index", sort=True):
        count = len(group)
        if count == 0:
            continue
        zero_mask = group["value"].abs() <= 1e-9
        zero_ratio = float(zero_mask.sum()) / count
        non_zero_count = int((~zero_mask).sum())
        # Allow a small number of inlet/anchor sections to carry real values while the
        # rest of the first exported frame is still effectively an all-zero bootstrap step.
        if zero_ratio >= 0.8 and non_zero_count <= max(2, math.floor(count * 0.1)):
            placeholder_steps.append(int(step))
    return placeholder_steps


def preserve_only_available_sample(metric_df: pd.DataFrame, placeholder_steps: list[int]) -> list[int]:
    if not placeholder_steps or metric_df.empty:
        return placeholder_steps
    metric_steps = set(int(step) for step in metric_df["data_index"].unique().tolist())
    if metric_steps and metric_steps.issubset(set(placeholder_steps)):
        return []
    return placeholder_steps


def build_metric_series(df: pd.DataFrame, metric: str, excluded_steps: set[int] | None = None, sort_key_func=None) -> list[dict[str, Any]]:
    series = []
    metric_df = df[df["metrics_code"] == metric].copy()
    if excluded_steps:
        metric_df = metric_df[~metric_df["data_index"].astype(int).isin(excluded_steps)].copy()
    name_column = "series_name" if "series_name" in metric_df.columns else "object_name"
    group_columns = [name_column, "object_type"]
    if "object_id" in metric_df.columns:
        group_columns.append("object_id")
    for group_key, group in metric_df.groupby(group_columns, sort=False, dropna=False):
        if len(group_columns) == 3:
            object_name, object_type, object_id = group_key
        else:
            object_name, object_type = group_key
            object_id = None
        ordered = group.sort_values("data_index")
        if ordered.empty:
            continue
        points = [[int(step), round_number(value)] for step, value in zip(ordered["data_index"], ordered["value"])]
        item: dict[str, Any] = {
            "name": object_name,
            "objectType": object_type,
            "data": points,
        }
        numeric_object_id = pd.to_numeric(pd.Series([object_id]), errors="coerce").iloc[0]
        if pd.notna(numeric_object_id):
            item["objectId"] = int(numeric_object_id)
        if metric == "water_flow":
            item["minValue"] = round_number(ordered["value"].min())
        series.append(item)

    if sort_key_func:
        series.sort(key=lambda item: (item["objectType"], sort_key_func(item["name"])))
    else:
        series.sort(key=lambda item: (item["objectType"], item["name"]))
    return series


def build_gate_series(df: pd.DataFrame, excluded_steps: set[int] | None = None, sort_key_func=None) -> list[dict[str, Any]]:
    series = []
    gate_df = df[(df["object_type"] == "Gate") & (df["metrics_code"] == "gate_opening")].copy()
    if excluded_steps:
        gate_df = gate_df[~gate_df["data_index"].astype(int).isin(excluded_steps)].copy()
    for object_name, group in gate_df.groupby("object_name", sort=False):
        ordered = group.sort_values("data_index")
        if ordered.empty:
            continue
        values = list(zip(ordered["data_index"].astype(int), ordered["value"].astype(float)))
        compressed: list[list[Any]] = []
        previous = None
        for step, value in values:
            if previous is None or value != previous:
                compressed.append([f"Step {step}", round_number(value)])
                previous = value
        last_step, last_value = values[-1]
        if compressed[-1][0] != f"Step {last_step}":
            compressed.append([f"Step {last_step}", round_number(last_value)])
        gate_group = re.sub(r"\d+#?$", "", object_name).rstrip("-#") or "闸门"
        series.append(
            {
                "name": object_name,
                "objectType": "Gate",
                "filterType": gate_group,
                "filterTypeLabel": gate_group,
                "range": round_number(ordered["value"].max() - ordered["value"].min()),
                "data": compressed,
            }
        )

    if sort_key_func:
        series.sort(key=lambda item: sort_key_func(item["name"]))
    else:
        series.sort(key=lambda item: item["name"])
    return series


def build_turbine_output_series(
    df: pd.DataFrame,
    excluded_steps: set[int] | None = None,
    sort_key_func=None,
) -> list[dict[str, Any]]:
    series = []
    turbine_df = select_turbine_output_rows(df)
    if excluded_steps:
        turbine_df = turbine_df[~turbine_df["data_index"].astype(int).isin(excluded_steps)].copy()

    group_columns = ["object_name"]
    has_device_name = "device_name" in turbine_df.columns
    has_object_id = "object_id" in turbine_df.columns
    if has_device_name:
        group_columns.append("device_name")
    if has_object_id:
        group_columns.append("object_id")

    for group_key, group in turbine_df.groupby(group_columns, sort=False, dropna=False):
        if has_device_name and has_object_id:
            object_name, device_name, object_id = group_key
        elif has_device_name:
            object_name, device_name = group_key
            object_id = None
        elif has_object_id:
            object_name, object_id = group_key
            device_name = None
        else:
            object_name = group_key
            device_name = None
            object_id = None

        ordered = group.sort_values("data_index")
        if ordered.empty:
            continue
        display_name = str(device_name or object_name or "未命名水轮机")
        item: dict[str, Any] = {
            "name": str(object_name or display_name),
            "displayName": display_name,
            "legendName": display_name,
            "sourceName": str(object_name or display_name),
            "sourceObjectType": "Turbine",
            "objectType": "Turbine",
            "metricsCode": "output_power",
            "seriesId": f"output_power|turbine|{display_name}",
            "filterType": "电站",
            "filterTypeLabel": "电站",
            "businessCategory": "电站",
            "businessObjectName": display_name,
            "businessObjectLabel": display_name,
            "defaultSelected": True,
            "data": [[int(step), round_number(value, 3)] for step, value in zip(ordered["data_index"], ordered["value"])],
            "minValue": round_number(ordered["value"].min(), 3),
            "maxValue": round_number(ordered["value"].max(), 3),
            "range": round_number(ordered["value"].max() - ordered["value"].min(), 3),
        }
        numeric_object_id = pd.to_numeric(pd.Series([object_id]), errors="coerce").iloc[0]
        if pd.notna(numeric_object_id):
            item["objectId"] = int(numeric_object_id)
            item["businessObjectId"] = int(numeric_object_id)
        series.append(item)

    if sort_key_func:
        series.sort(key=lambda item: sort_key_func(item["displayName"]))
    else:
        series.sort(key=lambda item: item["displayName"])
    return series


def build_business_turbine_series(
    df: pd.DataFrame,
    business_children: list[dict[str, Any]] | None = None,
    excluded_steps: set[int] | None = None,
    sort_key_func=None,
) -> list[dict[str, Any]]:
    base_series = build_turbine_output_series(df, excluded_steps, sort_key_func)
    if not business_children:
        return base_series

    by_name = {item["name"]: item for item in base_series}
    by_id = {item.get("objectId"): item for item in base_series if item.get("objectId") is not None}
    series: list[dict[str, Any]] = []
    mapped_keys: set[tuple[str, str]] = set()
    mapped_ids: set[int] = set()

    for child in business_children:
        if child.get("sourceObjectType") != "Turbine":
            continue
        source_id = child.get("sourceObjectId")
        base_item = by_id.get(source_id) if source_id is not None else None
        if not base_item:
            base_item = by_name.get(child["sourceObjectName"])
        if not base_item:
            continue
        item = dict(base_item)
        item.update(
            {
                "seriesId": (
                    f"output_power|{child['businessCategory']}|{child['businessObjectId']}|"
                    f"Turbine|{child.get('sourceObjectId') or child['sourceObjectName']}"
                ),
                "sourceName": child["sourceObjectName"],
                "sourceObjectType": "Turbine",
                "sourceObjectId": child.get("sourceObjectId"),
                "businessCategory": child["businessCategory"],
                "businessObjectName": child["businessObjectName"],
                "businessObjectId": child["businessObjectId"],
                "businessObjectLabel": child["businessObjectLabel"],
                "businessObjectOrder": child["businessObjectOrder"],
                "childRole": child["childRole"],
                "childOrder": child["childOrder"],
                "displayName": child["sourceObjectName"],
                "legendName": child["sourceObjectName"],
                "defaultSelected": bool(child.get("defaultSelected")),
            }
        )
        series.append(item)
        mapped_keys.add(("Turbine", child["sourceObjectName"]))
        if source_id is not None:
            mapped_ids.add(int(source_id))

    for base_item in base_series:
        object_id = base_item.get("objectId")
        if object_id is not None and int(object_id) in mapped_ids:
            continue
        if ("Turbine", base_item["name"]) in mapped_keys:
            continue
        series.append(
            {
                **base_item,
                "seriesId": f"output_power|fallback|Turbine|{base_item['name']}",
                "businessCategory": "电站",
                "businessObjectName": infer_station_name_from_turbine(base_item["name"]) or "未归属电站",
                "businessObjectId": object_id if object_id is not None else base_item["name"],
                "businessObjectLabel": infer_station_name_from_turbine(base_item["name"]) or "未归属电站",
                "businessObjectOrder": 999999,
                "childRole": "水轮机设备",
                "childOrder": 999999,
                "displayName": base_item["name"],
                "legendName": base_item["name"],
                "defaultSelected": False,
            }
        )

    return sort_business_series(series)


def select_turbine_output_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=df.columns)

    masks: list[pd.Series] = []
    if "device_type" in df.columns and "command_type" in df.columns:
        masks.append(
            (df["device_type"].fillna("").astype(str) == "Turbine")
            & (df["command_type"].fillna("").astype(str) == "output_power")
        )
    if "object_type" in df.columns and "metrics_code" in df.columns:
        masks.append(
            (df["object_type"].fillna("").astype(str) == "Turbine")
            & (df["metrics_code"].fillna("").astype(str) == "output_power")
        )

    if not masks:
        return pd.DataFrame(columns=df.columns)

    mask = masks[0].copy()
    for item in masks[1:]:
        mask = mask | item
    return df[mask].copy()


def infer_station_name_from_turbine(turbine_name: str) -> str | None:
    mapping = {
        "瀑布沟": "瀑布沟站(6机+3闸)",
        "深溪沟": "深溪沟站(4机+3闸)",
        "枕头坝": "枕头坝站(4机+5闸)",
        "沙坪": "沙坪站(6机+5闸)",
    }
    for keyword, station_name in mapping.items():
        if keyword in str(turbine_name or ""):
            return station_name
    return None


def load_mpc_payload(mpc_results_json: str | None) -> dict[str, Any] | None:
    if not mpc_results_json:
        return None

    payload = json.loads(Path(mpc_results_json).read_text(encoding="utf-8-sig"))
    if "result" in payload and isinstance(payload["result"], dict):
        result = payload["result"]
        if isinstance(result.get("content"), list) and result["content"]:
            text = result["content"][0].get("text")
            if text:
                return json.loads(text)
    if "data" in payload:
        return payload
    return None


def load_scenario_events_payload(scenario_events_json: str | None) -> list[dict[str, Any]]:
    if not scenario_events_json:
        return []

    payload = json.loads(Path(scenario_events_json).read_text(encoding="utf-8-sig"))
    if "result" in payload and isinstance(payload["result"], dict):
        result = payload["result"]
        if isinstance(result.get("structuredContent"), dict):
            structured = result["structuredContent"]
            if isinstance(structured.get("result"), dict):
                payload = structured["result"]
            else:
                payload = structured
        elif isinstance(result.get("content"), list) and result["content"]:
            text = result["content"][0].get("text")
            if text:
                payload = json.loads(text)
            else:
                payload = result
        else:
            payload = result
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def build_scenario_events_payload(
    events: list[dict[str, Any]],
    simulation_start_dt: datetime | None,
    sim_step_size: int | None,
) -> dict[str, Any]:
    if not events:
        return {
            "available": False,
            "count": 0,
            "summary": "本次报告未拿到可展示的工况事件明细。",
            "items": [],
        }

    items: list[dict[str, Any]] = []
    for event in events:
        auto_step = event.get("auto_schedule_at_step")
        step_text = f"第 {auto_step} 步" if auto_step is not None else "未提供步号"
        scheduled_time_text = None
        if simulation_start_dt is not None and auto_step is not None and sim_step_size:
            try:
                scheduled_dt = simulation_start_dt + timedelta(seconds=int(auto_step) * int(sim_step_size))
                scheduled_time_text = format_datetime_text(scheduled_dt)
            except Exception:
                scheduled_time_text = None
        series_items = event.get("object_time_series") or []
        first_series = series_items[0] if series_items and isinstance(series_items[0], dict) else None
        series_summary = None
        if first_series:
            sample_count = len(first_series.get("time_series") or [])
            series_summary = (
                f"{first_series.get('object_name') or '-'} / "
                f"{first_series.get('metrics_code') or '-'} / "
                f"{sample_count} 点"
            )
        items.append(
            {
                "name": str(event.get("hydro_event_name") or "未命名事件"),
                "description": str(event.get("hydro_event_description") or event.get("description") or "未提供说明"),
                "eventId": str(event.get("hydro_event_id") or "-"),
                "priority": str(event.get("priority") or "未提供"),
                "injectMode": str(event.get("inject_mode") or "未提供"),
                "step": int(auto_step) if auto_step is not None else None,
                "stepText": step_text,
                "scheduledTime": scheduled_time_text,
                "seriesSummary": series_summary,
            }
        )

    names = "、".join(item["name"] for item in items[:3])
    summary = f"本次工况共识别到 {len(items)} 个已注入事件，主要包括 {names}。"
    if any(item.get("scheduledTime") for item in items):
        summary += " 报告同时给出了事件注入步号和可推导的注入时间。"
    else:
        summary += " 由于部分事件缺少完整时间元数据，报告优先展示注入步号。"
    return {
        "available": True,
        "count": len(items),
        "summary": summary,
        "items": items,
    }


def _build_event_step_windows(unique_steps: list[int], event_step: int, window_size: int = 3) -> tuple[list[int], list[int], int | None]:
    if not unique_steps:
        return [], [], None

    anchor_step = min(unique_steps, key=lambda step: abs(int(step) - int(event_step)))
    anchor_index = unique_steps.index(anchor_step)
    before_steps = unique_steps[max(0, anchor_index - window_size):anchor_index]
    after_steps = unique_steps[anchor_index:min(len(unique_steps), anchor_index + window_size + 1)]
    return before_steps, after_steps, anchor_step


def _format_event_response_delta(metric_code: str, delta: float) -> str:
    unit_map = {
        "water_level": "m",
        "water_flow": "m³/s",
        "gate_opening": "m",
        "output_power": "MW",
    }
    label_map = {
        "water_level": "水位",
        "water_flow": "流量",
        "gate_opening": "闸门开度",
        "output_power": "机组出力",
    }
    trend = "抬升" if delta > 0 and metric_code == "water_level" else "增加" if delta > 0 else "下降" if metric_code == "water_level" else "减小"
    return f"{label_map.get(metric_code, metric_code)}{trend} {abs(delta):.2f} {unit_map.get(metric_code, '')}".strip()


def build_event_response_payload(
    df: pd.DataFrame,
    events: list[dict[str, Any]],
    simulation_start_dt: datetime | None,
    sim_step_size: int | None,
) -> dict[str, Any]:
    if not events:
        return {"available": False, "summary": "", "items": []}

    focus_metrics = {"water_level", "water_flow", "gate_opening", "output_power"}
    focus_types = {"CrossSection", "GateStation", "Gate", "Turbine"}
    candidate_df = df[
        df["metrics_code"].isin(focus_metrics)
        & df["object_type"].isin(focus_types)
        & df["data_index"].notna()
        & df["value"].notna()
    ].copy()
    if candidate_df.empty:
        return {
            "available": False,
            "summary": "结果文件缺少可用于事件前后响应分析的关键断面/站点序列。",
            "items": [],
        }

    candidate_df["data_index"] = candidate_df["data_index"].astype(int)
    unique_steps = sorted(int(step) for step in candidate_df["data_index"].dropna().unique().tolist())
    grouped_series: dict[tuple[str, str, str], pd.DataFrame] = {}
    for key, group in candidate_df.groupby(["object_name", "object_type", "metrics_code"], sort=False):
        grouped_series[(str(key[0]), str(key[1]), str(key[2]))] = group.sort_values("data_index").copy()

    items: list[dict[str, Any]] = []
    total_response_count = 0
    for event in events:
        try:
            event_step = int(event.get("auto_schedule_at_step"))
        except (TypeError, ValueError):
            event_step = None
        if event_step is None:
            items.append(
                {
                    "eventName": str(event.get("hydro_event_name") or "未命名事件"),
                    "eventStep": None,
                    "summary": "该事件缺少注入步号，暂时无法对齐结果序列做前后响应分析。",
                    "responses": [],
                }
            )
            continue

        before_steps, after_steps, anchor_step = _build_event_step_windows(unique_steps, event_step)
        if not before_steps or len(after_steps) < 2:
            items.append(
                {
                    "eventName": str(event.get("hydro_event_name") or "未命名事件"),
                    "eventStep": event_step,
                    "summary": "事件附近的结果输出点不足，暂时无法形成稳定的前后窗口对比。",
                    "responses": [],
                }
            )
            continue

        response_candidates: list[dict[str, Any]] = []
        for (object_name, object_type, metric_code), group in grouped_series.items():
            before_slice = group[group["data_index"].isin(before_steps)]
            after_slice = group[group["data_index"].isin(after_steps)]
            if before_slice.empty or after_slice.empty:
                continue
            before_mean = float(before_slice["value"].mean())
            after_mean = float(after_slice["value"].mean())
            delta = after_mean - before_mean
            abs_delta = abs(delta)
            if metric_code == "water_level" and abs_delta < 0.02:
                continue
            if metric_code == "water_flow" and abs_delta < 0.5:
                continue
            if metric_code == "gate_opening" and abs_delta < 0.02:
                continue
            if metric_code == "output_power" and abs_delta < 0.5:
                continue
            response_candidates.append(
                {
                    "objectName": object_name,
                    "objectType": object_type,
                    "metricCode": metric_code,
                    "beforeMean": round_number(before_mean),
                    "afterMean": round_number(after_mean),
                    "delta": round_number(delta),
                    "absDelta": abs_delta,
                    "responseText": _format_event_response_delta(metric_code, delta),
                }
            )

        if not response_candidates:
            items.append(
                {
                    "eventName": str(event.get("hydro_event_name") or "未命名事件"),
                    "eventStep": event_step,
                    "anchorStep": anchor_step,
                    "summary": "事件前后未识别出明显超过阈值的断面/站点响应，整体更接近平稳传递。",
                    "responses": [],
                }
            )
            continue

        metric_priority = {"water_flow": 0, "water_level": 1, "output_power": 2, "gate_opening": 3}
        top_by_metric: list[dict[str, Any]] = []
        seen_metrics: set[str] = set()
        for candidate in sorted(
            response_candidates,
            key=lambda item: (metric_priority.get(str(item["metricCode"]), 99), -float(item["absDelta"]), str(item["objectName"])),
        ):
            metric_code = str(candidate["metricCode"])
            if metric_code in seen_metrics:
                continue
            seen_metrics.add(metric_code)
            top_by_metric.append(candidate)
            if len(top_by_metric) >= 4:
                break
        top_by_metric.sort(key=lambda item: (-float(item["absDelta"]), str(item["objectName"])))
        total_response_count += len(top_by_metric)

        headline = "；".join(
            f"{item['objectName']}（{item['objectType']}）{item['responseText']}"
            for item in top_by_metric[:3]
        )
        items.append(
            {
                "eventName": str(event.get("hydro_event_name") or "未命名事件"),
                "eventStep": event_step,
                "anchorStep": anchor_step,
                "beforeSteps": before_steps,
                "afterSteps": after_steps,
                "summary": (
                    f"以事件步号 {event_step} 为中心，对比前 {len(before_steps)} 个与后 {len(after_steps)} 个输出步后，"
                    f"最明显的响应出现在 {headline}。"
                ),
                "responses": top_by_metric,
            }
        )

    available_count = sum(1 for item in items if item.get("responses"))
    summary = (
        f"共对 {len(items)} 个工况事件尝试做前后窗口响应分析，其中 {available_count} 个事件识别出了明确的断面/站点响应，"
        f"合计提炼 {total_response_count} 条关键响应观察。"
        if items
        else "当前没有可用于事件响应分析的工况事件。"
    )
    return {
        "available": bool(available_count),
        "summary": summary,
        "items": items,
    }


def select_coupled_section_names(df: pd.DataFrame, count: int = 4) -> list[str]:
    level_df = df[(df["object_type"] == "CrossSection") & (df["metrics_code"] == "water_level")].copy()
    flow_df = df[(df["object_type"] == "CrossSection") & (df["metrics_code"] == "water_flow")].copy()
    if level_df.empty or flow_df.empty:
        return []
    level_stats = level_df.groupby("object_name")["value"].agg(["min", "max"])
    flow_stats = flow_df.groupby("object_name")["value"].agg(["min", "max"])
    common_names = sorted(set(level_stats.index) & set(flow_stats.index))
    ranked: list[tuple[float, str]] = []
    for name in common_names:
        level_range = float(level_stats.loc[name, "max"] - level_stats.loc[name, "min"])
        flow_range = float(flow_stats.loc[name, "max"] - flow_stats.loc[name, "min"])
        ranked.append((abs(flow_range) + abs(level_range) * 10, str(name)))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in ranked[:count]]


def summarize_level_flow_coupling(df: pd.DataFrame) -> dict[str, Any]:
    section_names = select_coupled_section_names(df)
    if not section_names:
        return {
            "available": False,
            "sections": [],
            "analysis": "当前结果缺少可用于水位-流量联动对比的断面序列。",
        }
    level_df = df[(df["object_type"] == "CrossSection") & (df["metrics_code"] == "water_level")].copy()
    flow_df = df[(df["object_type"] == "CrossSection") & (df["metrics_code"] == "water_flow")].copy()
    level_df = level_df[level_df["object_name"].isin(section_names)].copy()
    flow_df = flow_df[flow_df["object_name"].isin(section_names)].copy()

    level_ranges: dict[str, float] = {}
    flow_ranges: dict[str, float] = {}
    if not level_df.empty:
        grouped_level = level_df.groupby("object_name")["value"].agg(["min", "max"])
        level_ranges = {
            str(name): float(row["max"] - row["min"])
            for name, row in grouped_level.iterrows()
        }
    if not flow_df.empty:
        grouped_flow = flow_df.groupby("object_name")["value"].agg(["min", "max"])
        flow_ranges = {
            str(name): float(row["max"] - row["min"])
            for name, row in grouped_flow.iterrows()
        }

    def coupling_score(name: str) -> float:
        return abs(flow_ranges.get(name, 0.0)) + abs(level_ranges.get(name, 0.0)) * 10.0

    highlight_section = max(section_names, key=coupling_score)
    highlight_level_range = level_ranges.get(highlight_section, 0.0)
    highlight_flow_range = flow_ranges.get(highlight_section, 0.0)
    return {
        "available": True,
        "sections": section_names,
        "analysis": (
            f"已选取 {'、'.join(section_names)} 等 {len(section_names)} 个关键断面做水位-流量联动复核，"
            f"其中 {highlight_section} 的联动变化最值得优先关注，水位变幅约 {round_number(highlight_level_range, 2)} m、"
            f"流量变幅约 {round_number(highlight_flow_range, 2)} m³/s。"
            "解读时应重点看同一断面上流量抬升或回落后，水位是否同步响应、是否存在明显滞后，以及水位变化幅度相对流量变化是否异常偏大或偏小；"
            "若流量先发生台阶式切换而水位随后平滑跟随，通常说明调节动作主导；若流量变化不大但水位持续抬升或回落，则需继续复核局部顶托、边界控制或断面过流能力变化。"
        ),
    }


def summarize_station_power_comparison_from_mpc_legacy(mpc_results_json: str | None) -> dict[str, Any]:
    payload = load_mpc_payload(mpc_results_json)
    if not payload:
        return {
            "available": False,
            "stations": [],
            "analysis": "当前未提供可用的 MPC 结果负荷数据，无法生成梯级电站来流-出力对比。",
        }

    station_labels = {
        20100: "瀑布沟站",
        20300: "深溪沟站",
        20500: "枕头坝站",
        20700: "沙坪坝站",
    }
    turbine_ids_by_station: dict[int, set[int]] = {}
    power_points: dict[int, list[float]] = {}
    flow_points: dict[int, list[float]] = {}

    for item in payload.get("data") or []:
        for detail in item.get("hydro_mpc_details") or []:
            if str(detail.get("command_type") or "") == "output_power" and detail.get("node_id") is not None and detail.get("object_id") is not None:
                node_id = int(detail["node_id"])
                object_id = int(detail["object_id"])
                turbine_ids_by_station.setdefault(node_id, set()).add(object_id)

    for item in payload.get("data") or []:
        station_power: dict[int, float] = {}
        station_flow: dict[int, float] = {}
        for detail in item.get("hydro_mpc_details") or []:
            node_id = detail.get("node_id")
            object_id = detail.get("object_id")
            value = detail.get("value")
            command_type = str(detail.get("command_type") or "")
            if node_id is None or object_id is None or value is None:
                continue
            node_id = int(node_id)
            object_id = int(object_id)
            value = float(value)
            if command_type == "output_power":
                station_power[node_id] = station_power.get(node_id, 0.0) + value
            elif command_type == "water_flow" and object_id in turbine_ids_by_station.get(node_id, set()):
                station_flow[node_id] = station_flow.get(node_id, 0.0) + value
        for node_id, node_value in station_power.items():
            power_points.setdefault(node_id, []).append(node_value)
        for node_id, node_value in station_flow.items():
            flow_points.setdefault(node_id, []).append(node_value)

    station_names = sorted({station_labels.get(node_id, f"Node {node_id}") for node_id in set(power_points) | set(flow_points)})
    if not station_names:
        return {
            "available": False,
            "stations": [],
            "analysis": "MPC 结果中未识别到可用于梯级电站对比的来流或出力序列。",
        }

    def station_score(name: str) -> float:
        reverse_map = {station_labels.get(node_id, f"Node {node_id}"): node_id for node_id in set(power_points) | set(flow_points)}
        node_id = reverse_map[name]
        power_values = power_points.get(node_id, [])
        flow_values = flow_points.get(node_id, [])
        power_range = max(power_values) - min(power_values) if power_values else 0.0
        flow_range = max(flow_values) - min(flow_values) if flow_values else 0.0
        return float(power_range + flow_range)

    highlight_station = max(station_names, key=station_score)
    return {
        "available": True,
        "stations": station_names,
        "analysis": (
            f"已聚合 {len(station_names)} 个梯级电站的来流代理与总出力过程，"
            f"其中 {highlight_station} 的联动变化幅度最大，建议优先复核调度合理性。"
        ),
    }


def summarize_station_power_comparison(
    df: pd.DataFrame,
    business_children: list[dict[str, Any]] | None = None,
    mpc_results_json: str | None = None,
) -> dict[str, Any]:
    def format_station_names(names: list[str]) -> str:
        return "、".join(names[:4]) if names else "相关电站"

    section_to_station: dict[str, str] = {}
    turbine_name_to_station: dict[str, str] = {}
    turbine_id_to_station: dict[int, str] = {}
    station_names_from_catalog: set[str] = set()

    for child in business_children or []:
        station_name = str(child.get("businessObjectName") or "").strip()
        if not station_name or not is_station_business_category(child.get("businessCategory")):
            continue
        station_names_from_catalog.add(station_name)
        if child.get("sourceObjectType") == "CrossSection" and child.get("childRole") == "闸前断面":
            section_name = str(child.get("sourceObjectName") or "").strip()
            if section_name:
                section_to_station[section_name] = station_name
        elif child.get("sourceObjectType") == "Turbine":
            turbine_name = str(child.get("sourceObjectName") or "").strip()
            if turbine_name:
                turbine_name_to_station[turbine_name] = station_name
            turbine_id = child.get("sourceObjectId")
            if turbine_id is not None:
                try:
                    turbine_id_to_station[int(turbine_id)] = station_name
                except (TypeError, ValueError):
                    pass

    flow_points: dict[str, list[float]] = {}
    power_points: dict[str, list[float]] = {}

    flow_df = df[(df["object_type"] == "CrossSection") & (df["metrics_code"] == "water_flow")].copy()
    if not flow_df.empty and section_to_station:
        flow_df["station_name"] = flow_df["object_name"].map(lambda name: section_to_station.get(str(name).strip()))
        flow_df = flow_df[flow_df["station_name"].notna()].copy()
        if not flow_df.empty:
            grouped_flow = flow_df.groupby(["data_index", "station_name"])["value"].sum().reset_index()
            for station_name, group in grouped_flow.groupby("station_name"):
                flow_points[str(station_name)] = [float(value) for value in group["value"].tolist()]

    turbine_df = select_turbine_output_rows(df).copy()
    if not turbine_df.empty:
        if "object_id" not in turbine_df.columns:
            turbine_df["object_id"] = pd.NA

        def map_turbine_station(row: pd.Series) -> str | None:
            object_id = row.get("object_id")
            if pd.notna(object_id):
                try:
                    station_name = turbine_id_to_station.get(int(float(object_id)))
                    if station_name:
                        return station_name
                except (TypeError, ValueError):
                    pass
            object_name = str(row.get("object_name") or "").strip()
            return turbine_name_to_station.get(object_name) or infer_station_name_from_turbine(object_name)

        turbine_df["station_name"] = turbine_df.apply(map_turbine_station, axis=1)
        turbine_df = turbine_df[turbine_df["station_name"].notna()].copy()
        if not turbine_df.empty:
            grouped_power = turbine_df.groupby(["data_index", "station_name"])["value"].sum().reset_index()
            for station_name, group in grouped_power.groupby("station_name"):
                power_points[str(station_name)] = [float(value) for value in group["value"].tolist()]

    if not power_points and mpc_results_json:
        payload = load_mpc_payload(mpc_results_json)
        if payload:
            node_labels: dict[int, str] = {}
            for child in business_children or []:
                if not is_station_business_category(child.get("businessCategory")):
                    continue
                business_object_id = child.get("businessObjectId")
                if business_object_id is None:
                    continue
                try:
                    node_labels[int(business_object_id)] = str(child.get("businessObjectName") or business_object_id)
                except (TypeError, ValueError):
                    continue

            for item in payload.get("data") or []:
                station_power: dict[str, float] = {}
                for detail in item.get("hydro_mpc_details") or []:
                    if str(detail.get("command_type") or "") != "output_power":
                        continue
                    node_id = detail.get("node_id")
                    value = detail.get("value")
                    if node_id is None or value is None:
                        continue
                    try:
                        station_name = node_labels.get(int(node_id), f"Node {int(node_id)}")
                        station_power[station_name] = station_power.get(station_name, 0.0) + float(value)
                    except (TypeError, ValueError):
                        continue
                for station_name, station_value in station_power.items():
                    power_points.setdefault(station_name, []).append(float(station_value))

    stations = sorted((set(flow_points) | set(power_points) | station_names_from_catalog))
    comparable_stations = [name for name in stations if flow_points.get(name) and power_points.get(name)]
    if comparable_stations:
        def station_score(name: str) -> float:
            power_values = power_points.get(name, [])
            flow_values = flow_points.get(name, [])
            power_range = max(power_values) - min(power_values) if power_values else 0.0
            flow_range = max(flow_values) - min(flow_values) if flow_values else 0.0
            return float(power_range + flow_range)

        highlight_station = max(comparable_stations, key=station_score)
        return {
            "available": True,
            "stations": comparable_stations,
            "analysis": (
                f"已聚合 {len(comparable_stations)} 个梯级电站的闸前断面来流代理与机组总出力过程，"
                f"覆盖 {format_station_names(comparable_stations)} 等站点，其中 {highlight_station} 的联动变化幅度最大。"
                "解读时应重点关注来流抬升后出力是否同步抬升、出力峰谷相对来流是否存在明显滞后或过度放大，"
                "以及相邻电站之间是否出现异常反相或平台切换；若来流基本平稳而出力频繁跳变，通常说明调度动作主导，"
                "若来流变化明显而出力响应偏弱，则需继续复核过机流量分配、机组负荷约束或下游顶托影响。"
            ),
        }

    power_only_stations = sorted(name for name in stations if power_points.get(name))
    if power_only_stations:
        return {
            "available": False,
            "stations": power_only_stations,
            "analysis": (
                f"已识别 {len(power_only_stations)} 个电站的机组总出力，但缺少对应闸前断面来流代理，"
                f"当前仅覆盖 {format_station_names(power_only_stations)} 等站点，暂时无法生成完整的梯级电站来流-出力对比。"
                "现阶段仍可用它比较各站出力水平、平台切换顺序和机组负荷分布，但不能据此判断来水-出力耦合关系、"
                "站间传递是否顺畅或削峰填谷是否合理；后续补齐闸前断面来流代理后，应优先复核出力变化是否跟随来流变化、"
                "是否存在异常滞后、放大或反向响应。"
            ),
        }

    return {
        "available": False,
        "stations": [],
        "analysis": (
            "当前结果中未识别到可用于梯级电站来流-出力对比的站级来流代理或机组总出力序列。"
            "在补齐站级来流代理或机组总出力前，不建议对梯级电站调度协调性、来水利用效率或站间能量传递关系给出正式结论。"
        ),
    }


def summarize_station_output_composition(
    df: pd.DataFrame,
    business_children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    turbine_name_to_station: dict[str, str] = {}
    turbine_id_to_station: dict[int, str] = {}
    turbine_df = select_turbine_output_rows(df).copy()
    if turbine_df.empty:
        return {
            "available": False,
            "stations": [],
            "analysis": "当前结果未识别到可用于梯级总出力构成分析的机组出力序列。",
        }

    for child in business_children or []:
        if child.get("sourceObjectType") != "Turbine" or not is_station_business_category(child.get("businessCategory")):
            continue
        station_name = str(child.get("businessObjectName") or "").strip()
        turbine_name = str(child.get("sourceObjectName") or "").strip()
        if station_name and turbine_name:
            turbine_name_to_station[turbine_name] = station_name
        turbine_id = child.get("sourceObjectId")
        if station_name and turbine_id is not None:
            try:
                turbine_id_to_station[int(turbine_id)] = station_name
            except (TypeError, ValueError):
                pass

    if "object_id" not in turbine_df.columns:
        turbine_df["object_id"] = pd.NA

    def map_station(row: pd.Series) -> str | None:
        object_id = row.get("object_id")
        if pd.notna(object_id):
            try:
                station_name = turbine_id_to_station.get(int(float(object_id)))
                if station_name:
                    return station_name
            except (TypeError, ValueError):
                pass
        object_name = str(row.get("object_name") or "").strip()
        return turbine_name_to_station.get(object_name) or infer_station_name_from_turbine(object_name)

    turbine_df["station_name"] = turbine_df.apply(map_station, axis=1)
    turbine_df = turbine_df[turbine_df["station_name"].notna()].copy()
    if turbine_df.empty:
        return {
            "available": False,
            "stations": [],
            "analysis": "当前结果未建立起机组到电站的有效映射，无法生成梯级总出力构成图。",
        }

    grouped = turbine_df.groupby(["data_index", "station_name"])["value"].sum().reset_index()
    station_totals = grouped.groupby("station_name")["value"].sum().sort_values(ascending=False)
    station_names = [str(name) for name in station_totals.index.tolist()]
    if not station_names:
        return {
            "available": False,
            "stations": [],
            "analysis": "当前结果缺少可用于梯级总出力构成分析的站级总出力数据。",
        }
    highlight_station = station_names[0]
    total_output = float(station_totals.sum())
    highlight_share = float(station_totals.iloc[0] / total_output) if total_output else 0.0
    return {
        "available": True,
        "stations": station_names,
        "analysis": (
            f"已按电站汇总 {len(station_names)} 个梯级站点的总出力构成，"
            f"其中 {highlight_station} 的累计出力占比最高，约为 {round_number(highlight_share * 100, 1)}%。"
            "这张图适合直接观察不同电站在总发电任务中的分工、接力与退让关系；"
            "若总出力平台切换主要由单一电站承担，应继续复核该站是否过度承担调节任务，"
            "若多站占比在相邻时段连续切换，则更能体现梯级协同调度特征。"
        ),
    }


def summarize_turbine_dispatch_heatmap(df: pd.DataFrame) -> dict[str, Any]:
    turbine_df = select_turbine_output_rows(df).copy()
    if turbine_df.empty:
        return {
            "available": False,
            "turbines": [],
            "analysis": "当前结果未识别到可用于机组负荷分配热力图的水轮机出力序列。",
        }

    grouped = turbine_df.groupby("object_name")["value"].agg(["min", "max", "mean"])
    if grouped.empty:
        return {
            "available": False,
            "turbines": [],
            "analysis": "当前结果未形成可用于机组负荷分配热力图的有效机组出力统计。",
        }

    grouped["range"] = grouped["max"] - grouped["min"]
    grouped = grouped.sort_values(["mean", "range"], ascending=[False, False])
    turbine_names = [str(name) for name in grouped.index.tolist()]
    highlight_turbine = turbine_names[0]
    highlight_mean = float(grouped.iloc[0]["mean"])
    highlight_range = float(grouped.iloc[0]["range"])
    return {
        "available": True,
        "turbines": turbine_names,
        "analysis": (
            f"热力图覆盖 {len(turbine_names)} 台机组的全过程负荷分配，其中 {highlight_turbine} 的平均出力最高，"
            f"约为 {round_number(highlight_mean, 2)}，变幅约 {round_number(highlight_range, 2)}。"
            "这张图更适合看站内协同而不是单机趋势：颜色长期偏亮说明该机组长期承担主力，"
            "颜色分段轮换说明存在负荷转移或轮机接力；若少数机组长期高负荷而其他机组接近冷备，"
            "则应继续复核机组分配是否均衡、是否存在约束卡死或调度策略过度集中。"
        ),
    }


def _series_change_summary(points: list[list[Any]] | None) -> dict[str, Any]:
    normalized: list[tuple[int, float]] = []
    for point in points or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            normalized.append((int(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    normalized.sort(key=lambda item: item[0])
    if not normalized:
        return {"range": 0.0, "peak_step": None, "peak_delta": 0.0}

    peak_step = None
    peak_delta = 0.0
    peak_abs_delta = -1.0
    for previous, current in zip(normalized, normalized[1:]):
        delta = current[1] - previous[1]
        if abs(delta) > peak_abs_delta:
            peak_abs_delta = abs(delta)
            peak_delta = float(delta)
            peak_step = int(current[0])

    values = [value for _, value in normalized]
    return {
        "range": float(max(values) - min(values)),
        "peak_step": peak_step,
        "peak_delta": peak_delta,
    }


def build_comparison_decision_summary(
    station_power_chart: dict[str, Any],
    station_output_chart: dict[str, Any],
    scenario_events_payload: dict[str, Any],
    scenario_event_responses: dict[str, Any],
) -> dict[str, Any]:
    object_type_label_map = {
        "CrossSection": "断面",
        "GateStation": "站点",
        "Gate": "闸门",
        "Turbine": "机组",
    }
    station_summaries: list[dict[str, Any]] = []
    for station in station_power_chart.get("stations", []) or []:
        flow_summary = _series_change_summary(station.get("flowData"))
        power_summary = _series_change_summary(station.get("powerData"))
        if not station.get("flowData") and not station.get("powerData"):
            continue
        station_summaries.append(
            {
                "name": str(station.get("name") or "-"),
                "flow": flow_summary,
                "power": power_summary,
                "score": float(abs(flow_summary["peak_delta"]) + abs(power_summary["peak_delta"])),
            }
        )

    if not station_summaries:
        return {
            "available": False,
            "title": "水动力响应与 MPC 调度对照",
            "summary": "当前缺少可用于形成站级来流-出力联动结论的数据。",
            "narrative": "本轮尚未识别到同时具备来流代理与总出力序列的站点，暂时无法形成正式的水动力响应与 MPC 调度对照摘要。",
            "cards": [],
            "responseHighlights": [],
        }

    dominant_station = max(
        station_summaries,
        key=lambda item: (item["score"], item["flow"]["range"] + item["power"]["range"], item["name"]),
    )
    flow_peak_delta = float(dominant_station["flow"]["peak_delta"])
    power_peak_delta = float(dominant_station["power"]["peak_delta"])
    flow_peak_step = dominant_station["flow"]["peak_step"]
    power_peak_step = dominant_station["power"]["peak_step"]
    response_lag_steps = (
        abs(int(power_peak_step) - int(flow_peak_step))
        if flow_peak_step is not None and power_peak_step is not None
        else None
    )

    top_station = None
    top_share = None
    composition_stations = station_output_chart.get("stations", []) or []
    if composition_stations:
        top_station = str(composition_stations[0].get("name") or dominant_station["name"])
        try:
            top_share = float(composition_stations[0].get("share"))
        except (TypeError, ValueError):
            top_share = None

    if response_lag_steps is None:
        control_mode = "待补数"
    elif response_lag_steps <= 1 and (top_share or 0.0) >= 45:
        control_mode = "首响主调"
    elif response_lag_steps <= 2:
        control_mode = "首响+接力"
    else:
        control_mode = "滞后调节"

    response_highlights: list[dict[str, Any]] = []
    for item in scenario_event_responses.get("items", []) or []:
        responses = item.get("responses") or []
        if not responses:
            continue
        top_response = responses[0]
        metric_code = str(top_response.get("metricCode") or "")
        delta_raw = top_response.get("delta")
        try:
            delta_value = float(delta_raw)
        except (TypeError, ValueError):
            delta_value = 0.0
        object_type_label = object_type_label_map.get(str(top_response.get("objectType") or ""), "对象")
        response_highlights.append(
            {
                "eventName": str(item.get("eventName") or "未命名事件"),
                "eventStep": item.get("eventStep"),
                "summary": str(item.get("summary") or ""),
                "responseText": f"{object_type_label} {top_response.get('objectName') or '-'}{_format_event_response_delta(metric_code, delta_value)}",
            }
        )
    response_highlights = response_highlights[:3]

    event_focus = ""
    event_items = scenario_events_payload.get("items", []) or []
    if event_items:
        first_event = event_items[0]
        scheduled_time_text = first_event.get("scheduledTime")
        event_focus = (
            f"当前工况事件以 {first_event.get('name') or '首个事件'} 为主要观察锚点，"
            f"注入步号 {first_event.get('stepText') or '-'}"
            f"{f'，对应 {scheduled_time_text}' if scheduled_time_text else ''}。"
        )

    lag_text = f"{response_lag_steps} 个输出步" if response_lag_steps is not None else "暂无法推导"
    top_share_text = f"{round_number(top_share, 1)}%" if top_share is not None else "暂无法推导"
    flow_peak_text = f"{'增加' if flow_peak_delta >= 0 else '减小'} {round_number(abs(flow_peak_delta), 2)} m³/s"
    power_peak_text = f"{'增加' if power_peak_delta >= 0 else '减小'} {round_number(abs(power_peak_delta), 2)} MW"
    narrative = (
        f"以 {dominant_station['name']} 作为主调节站复核时，可见其站级来流代理峰值变化约 {flow_peak_text}，"
        f"对应总出力峰值变化约 {power_peak_text}，两者峰值响应间隔 {lag_text}。"
        f"{f'当前总出力构成中，{top_station} 占比最高，约 {top_share_text}；' if top_station else ''}"
        f"综合判断，本轮更接近“{control_mode}”的控制模式，适合结合事件前后断面/站点响应继续核对调度是否顺滑。"
    )
    if event_focus:
        narrative = f"{narrative}{event_focus}"

    concise_event_focus = ""
    if event_items:
        first_event = event_items[0]
        scheduled_time_text = first_event.get("scheduledTime")
        concise_event_focus = (
            f"主要工况锚点为 {first_event.get('name') or '首个事件'}，"
            f"注入步号 {first_event.get('stepText') or '-'}"
            f"{f'，对应 {scheduled_time_text}' if scheduled_time_text else ''}。"
        )

    narrative_parts = [
        f"本轮以 {dominant_station['name']} 作为主调节点复核。",
        f"站级来流峰值变化约 {flow_peak_text}，对应总出力峰值变化约 {power_peak_text}，两者峰值响应间隔 {lag_text}。",
    ]
    if top_station:
        narrative_parts.append(f"在总出力构成中，{top_station} 承担占比最高，约 {top_share_text}。")
    narrative_parts.append(f"综合判断，本轮更接近“{control_mode}”的调节模式。")
    if concise_event_focus:
        narrative_parts.append(concise_event_focus)
    narrative = "".join(narrative_parts)

    return {
        "available": True,
        "title": "水动力响应与 MPC 调度对照",
        "summary": narrative,
        "narrative": narrative,
        "cards": [
            {"label": "主调节站", "value": dominant_station["name"], "hint": "站级来流代理与总出力峰值综合最显著"},
            {"label": "来流峰值变化", "value": flow_peak_text, "hint": f"峰值步号 {flow_peak_step if flow_peak_step is not None else '-'}"},
            {"label": "出力峰值变化", "value": power_peak_text, "hint": f"峰值步号 {power_peak_step if power_peak_step is not None else '-'}"},
            {"label": "响应滞后 / 模式", "value": lag_text, "hint": control_mode},
        ],
        "dominantStation": dominant_station["name"],
        "peakInflowDelta": round_number(flow_peak_delta, 3),
        "peakPowerDelta": round_number(power_peak_delta, 3),
        "responseLagSteps": response_lag_steps,
        "controlMode": control_mode,
        "responseHighlights": response_highlights,
    }


def build_station_mappings(
    business_children: list[dict[str, Any]] | None = None,
) -> dict[str, dict[Any, str]]:
    section_to_station: dict[str, str] = {}
    turbine_name_to_station: dict[str, str] = {}
    turbine_id_to_station: dict[int, str] = {}
    station_names_from_catalog: set[str] = set()

    for child in business_children or []:
        station_name = str(child.get("businessObjectName") or "").strip()
        if not station_name or not is_station_business_category(child.get("businessCategory")):
            continue
        station_names_from_catalog.add(station_name)
        if child.get("sourceObjectType") == "CrossSection" and child.get("childRole") == "闸前断面":
            section_name = str(child.get("sourceObjectName") or "").strip()
            if section_name:
                section_to_station[section_name] = station_name
        elif child.get("sourceObjectType") == "Turbine":
            turbine_name = str(child.get("sourceObjectName") or "").strip()
            if turbine_name:
                turbine_name_to_station[turbine_name] = station_name
            turbine_id = child.get("sourceObjectId")
            if turbine_id is not None:
                try:
                    turbine_id_to_station[int(turbine_id)] = station_name
                except (TypeError, ValueError):
                    pass

    return {
        "section_to_station": section_to_station,
        "turbine_name_to_station": turbine_name_to_station,
        "turbine_id_to_station": turbine_id_to_station,
        "station_names_from_catalog": station_names_from_catalog,
    }


def build_coupling_chart_payload(
    df: pd.DataFrame,
    excluded_steps: set[int] | None = None,
) -> dict[str, Any]:
    working_df = df.copy()
    if excluded_steps:
        working_df = working_df[~working_df["data_index"].astype(int).isin(excluded_steps)].copy()

    section_names = select_coupled_section_names(working_df)
    if not section_names:
        return {"available": False, "sections": []}

    sections: list[dict[str, Any]] = []
    for section_name in section_names:
        level_group = working_df[
            (working_df["object_type"] == "CrossSection")
            & (working_df["metrics_code"] == "water_level")
            & (working_df["object_name"] == section_name)
        ].sort_values("data_index")
        flow_group = working_df[
            (working_df["object_type"] == "CrossSection")
            & (working_df["metrics_code"] == "water_flow")
            & (working_df["object_name"] == section_name)
        ].sort_values("data_index")
        if level_group.empty or flow_group.empty:
            continue
        sections.append(
            {
                "name": str(section_name),
                "levelData": [
                    [int(step), round_number(value, 3)]
                    for step, value in zip(level_group["data_index"], level_group["value"])
                ],
                "flowData": [
                    [int(step), round_number(value, 3)]
                    for step, value in zip(flow_group["data_index"], flow_group["value"])
                ],
            }
        )
    return {"available": bool(sections), "sections": sections}


def build_station_power_chart_payload(
    df: pd.DataFrame,
    business_children: list[dict[str, Any]] | None = None,
    excluded_steps: set[int] | None = None,
    mpc_results_json: str | None = None,
) -> dict[str, Any]:
    mappings = build_station_mappings(business_children)
    section_to_station = mappings["section_to_station"]
    turbine_name_to_station = mappings["turbine_name_to_station"]
    turbine_id_to_station = mappings["turbine_id_to_station"]

    working_df = df.copy()
    if excluded_steps:
        working_df = working_df[~working_df["data_index"].astype(int).isin(excluded_steps)].copy()

    flow_points: dict[str, list[list[Any]]] = {}
    power_points: dict[str, list[list[Any]]] = {}

    flow_df = working_df[
        (working_df["object_type"] == "CrossSection") & (working_df["metrics_code"] == "water_flow")
    ].copy()
    if not flow_df.empty and section_to_station:
        flow_df["station_name"] = flow_df["object_name"].map(lambda name: section_to_station.get(str(name).strip()))
        flow_df = flow_df[flow_df["station_name"].notna()].copy()
        if not flow_df.empty:
            grouped_flow = flow_df.groupby(["data_index", "station_name"])["value"].sum().reset_index()
            for station_name, group in grouped_flow.groupby("station_name"):
                flow_points[str(station_name)] = [
                    [int(step), round_number(value, 3)]
                    for step, value in zip(group["data_index"], group["value"])
                ]

    turbine_df = select_turbine_output_rows(working_df).copy()
    if not turbine_df.empty:
        if "object_id" not in turbine_df.columns:
            turbine_df["object_id"] = pd.NA

        def map_station(row: pd.Series) -> str | None:
            object_id = row.get("object_id")
            if pd.notna(object_id):
                try:
                    station_name = turbine_id_to_station.get(int(float(object_id)))
                    if station_name:
                        return station_name
                except (TypeError, ValueError):
                    pass
            object_name = str(row.get("object_name") or "").strip()
            return turbine_name_to_station.get(object_name) or infer_station_name_from_turbine(object_name)

        turbine_df["station_name"] = turbine_df.apply(map_station, axis=1)
        turbine_df = turbine_df[turbine_df["station_name"].notna()].copy()
        if not turbine_df.empty:
            grouped_power = turbine_df.groupby(["data_index", "station_name"])["value"].sum().reset_index()
            for station_name, group in grouped_power.groupby("station_name"):
                power_points[str(station_name)] = [
                    [int(step), round_number(value, 3)]
                    for step, value in zip(group["data_index"], group["value"])
                ]

    if not power_points and mpc_results_json:
        payload = load_mpc_payload(mpc_results_json)
        if payload:
            node_labels: dict[int, str] = {}
            for child in business_children or []:
                if not is_station_business_category(child.get("businessCategory")):
                    continue
                business_object_id = child.get("businessObjectId")
                if business_object_id is None:
                    continue
                try:
                    node_labels[int(business_object_id)] = str(child.get("businessObjectName") or business_object_id)
                except (TypeError, ValueError):
                    continue

            station_power_steps: dict[str, list[list[Any]]] = {}
            for item in payload.get("data") or []:
                step = item.get("step")
                if step is None:
                    continue
                station_power: dict[str, float] = {}
                for detail in item.get("hydro_mpc_details") or []:
                    if str(detail.get("command_type") or "") != "output_power":
                        continue
                    node_id = detail.get("node_id")
                    value = detail.get("value")
                    if node_id is None or value is None:
                        continue
                    try:
                        station_name = node_labels.get(int(node_id), f"Node {int(node_id)}")
                        station_power[station_name] = station_power.get(station_name, 0.0) + float(value)
                    except (TypeError, ValueError):
                        continue
                for station_name, station_value in station_power.items():
                    station_power_steps.setdefault(station_name, []).append([int(step), round_number(station_value, 3)])
            power_points.update(station_power_steps)

    station_names = sorted(set(flow_points) | set(power_points))
    stations = [
        {
            "name": station_name,
            "flowData": flow_points.get(station_name, []),
            "powerData": power_points.get(station_name, []),
        }
        for station_name in station_names
        if flow_points.get(station_name) or power_points.get(station_name)
    ]
    return {"available": bool(stations), "stations": stations}


def build_station_output_composition_chart_payload(
    df: pd.DataFrame,
    business_children: list[dict[str, Any]] | None = None,
    excluded_steps: set[int] | None = None,
) -> dict[str, Any]:
    station_payload = build_station_power_chart_payload(
        df,
        business_children=business_children,
        excluded_steps=excluded_steps,
        mpc_results_json=None,
    )
    stations = [item for item in station_payload.get("stations", []) if item.get("powerData")]
    if not stations:
        return {"available": False, "steps": [], "stations": [], "totalData": []}

    step_values = sorted(
        {
            int(point[0])
            for station in stations
            for point in station.get("powerData", [])
            if point and point[0] is not None
        }
    )
    if not step_values:
        return {"available": False, "steps": [], "stations": [], "totalData": []}

    station_series: list[dict[str, Any]] = []
    total_by_step = {step: 0.0 for step in step_values}
    station_totals: list[tuple[str, float]] = []

    for station in stations:
        point_map = {int(step): float(value) for step, value in station.get("powerData", [])}
        data = []
        total_value = 0.0
        for step in step_values:
            value = point_map.get(step, 0.0)
            total_by_step[step] += value
            total_value += value
            data.append(round_number(value, 3))
        station_totals.append((station["name"], total_value))
        station_series.append({"name": station["name"], "data": data})

    aggregate_total = sum(value for _, value in station_totals)
    share_map = {
        name: round_number((value / aggregate_total) * 100, 2) if aggregate_total else 0.0
        for name, value in station_totals
    }
    for station in station_series:
        station["share"] = share_map.get(station["name"], 0.0)

    station_series.sort(
        key=lambda item: (
            -(float(item.get("share") or 0.0)),
            str(item.get("name") or ""),
        )
    )
    return {
        "available": True,
        "steps": step_values,
        "stations": station_series,
        "totalData": [round_number(total_by_step[step], 3) for step in step_values],
    }


def build_turbine_dispatch_heatmap_payload(
    df: pd.DataFrame,
    business_children: list[dict[str, Any]] | None = None,
    excluded_steps: set[int] | None = None,
) -> dict[str, Any]:
    turbine_series = build_business_turbine_series(df, business_children, excluded_steps)
    if not turbine_series:
        return {"available": False, "steps": [], "turbines": [], "matrix": []}

    step_values = sorted(
        {
            int(point[0])
            for series in turbine_series
            for point in series.get("data", [])
            if point and point[0] is not None
        }
    )
    if not step_values:
        return {"available": False, "steps": [], "turbines": [], "matrix": []}

    ranked_series = sorted(
        turbine_series,
        key=lambda item: (
            -float(
                sum(float(point[1]) for point in item.get("data", []) if len(point) >= 2)
                / max(len(item.get("data", [])), 1)
            ),
            str(item.get("displayName") or item.get("name") or ""),
        ),
    )
    step_index_map = {step: index for index, step in enumerate(step_values)}
    matrix: list[list[Any]] = []
    turbine_names: list[str] = []

    for row_index, series in enumerate(ranked_series):
        turbine_name = str(series.get("displayName") or series.get("name") or f"机组{row_index + 1}")
        turbine_names.append(turbine_name)
        for point in series.get("data", []):
            if len(point) < 2:
                continue
            step = int(point[0])
            if step not in step_index_map:
                continue
            matrix.append([step_index_map[step], row_index, round_number(point[1], 3)])

    return {
        "available": bool(matrix),
        "steps": step_values,
        "turbines": turbine_names,
        "matrix": matrix,
    }


def summarize_turbine_dispatch_heatmap(df: pd.DataFrame) -> dict[str, Any]:
    turbine_df = select_turbine_output_rows(df).copy()
    if turbine_df.empty:
        return {
            "available": False,
            "turbines": [],
            "analysis": "当前结果未识别到可用于机组分组堆叠面积图的水轮机出力序列。",
        }

    grouped = turbine_df.groupby("object_name")["value"].agg(["min", "max", "mean"])
    if grouped.empty:
        return {
            "available": False,
            "turbines": [],
            "analysis": "当前结果未形成可用于机组分组堆叠面积图的有效机组出力统计。",
        }

    grouped["range"] = grouped["max"] - grouped["min"]
    grouped = grouped.sort_values(["mean", "range"], ascending=[False, False])
    turbine_names = [str(name) for name in grouped.index.tolist()]
    highlight_turbine = turbine_names[0]
    highlight_mean = float(grouped.iloc[0]["mean"])
    highlight_range = float(grouped.iloc[0]["range"])
    total_mean = float(grouped["mean"].sum()) if not grouped.empty else 0.0
    top3_share = float(grouped["mean"].head(3).sum() / total_mean) if total_mean > 0 else 0.0
    mean_shares = (grouped["mean"] / total_mean).fillna(0.0) if total_mean > 0 else grouped["mean"] * 0.0
    concentration_index = float((mean_shares.pow(2)).sum())
    concentration_level = "偏高" if concentration_index >= 0.25 else ("中等" if concentration_index >= 0.16 else "偏低")
    return {
        "available": True,
        "turbines": turbine_names,
        "analysis": (
            f"机组分组堆叠面积图覆盖 {len(turbine_names)} 台机组的全过程负荷分配，其中 {highlight_turbine} 的平均出力最高，"
            f"约为 {round_number(highlight_mean, 2)}，变幅约 {round_number(highlight_range, 2)}；平均负荷前 3 台机组合计占比约 "
            f"{round_number(top3_share * 100, 1)}%，集中度指数约 {round_number(concentration_index, 3)}，整体集中度 {concentration_level}。"
            "这张图更适合直接看主力机组、接力机组和平台切换：若面积长期由少数机组主导，说明负荷承担偏集中；"
            "若不同机组面积在相邻时段有明显此消彼长，则说明存在轮换接力；若总出力变化时总是由固定少数机组率先抬升或回落，"
            "则应继续复核机组分配是否均衡、是否存在约束卡死或调度策略过度集中。"
        ),
    }


def build_turbine_dispatch_heatmap_payload(
    df: pd.DataFrame,
    business_children: list[dict[str, Any]] | None = None,
    excluded_steps: set[int] | None = None,
) -> dict[str, Any]:
    turbine_series = build_business_turbine_series(df, business_children, excluded_steps)
    if not turbine_series:
        return {"available": False, "steps": [], "turbines": [], "series": [], "totalData": [], "stationGroups": []}

    step_values = sorted(
        {
            int(point[0])
            for series in turbine_series
            for point in series.get("data", [])
            if point and point[0] is not None
        }
    )
    if not step_values:
        return {"available": False, "steps": [], "turbines": [], "series": [], "totalData": [], "stationGroups": []}

    ranked_series = sorted(
        turbine_series,
        key=lambda item: (
            str(item.get("businessObjectName") or ""),
            -float(
                sum(float(point[1]) for point in item.get("data", []) if len(point) >= 2)
                / max(len(item.get("data", [])), 1)
            ),
            str(item.get("displayName") or item.get("name") or ""),
        ),
    )
    step_index_map = {step: index for index, step in enumerate(step_values)}
    chart_series: list[dict[str, Any]] = []
    turbine_names: list[str] = []
    total_data = [0.0 for _ in step_values]
    grouped_names: dict[str, list[str]] = {}

    for row_index, series in enumerate(ranked_series):
        turbine_name = str(series.get("displayName") or series.get("name") or f"机组{row_index + 1}")
        station_name = str(series.get("businessObjectName") or "未归属电站")
        turbine_names.append(turbine_name)
        grouped_names.setdefault(station_name, []).append(turbine_name)
        values = [0.0 for _ in step_values]
        for point in series.get("data", []):
            if len(point) < 2:
                continue
            step = int(point[0])
            if step not in step_index_map:
                continue
            index = step_index_map[step]
            values[index] = float(point[1])
            total_data[index] += float(point[1])
        chart_series.append(
            {
                "name": turbine_name,
                "stationName": station_name,
                "data": [round_number(value, 3) for value in values],
            }
        )

    station_groups = [
        {"stationName": station_name, "turbines": names}
        for station_name, names in grouped_names.items()
    ]
    return {
        "available": bool(chart_series),
        "steps": step_values,
        "turbines": turbine_names,
        "series": chart_series,
        "totalData": [round_number(value, 3) for value in total_data],
        "stationGroups": station_groups,
    }


def augment_dataframe_with_mpc_turbine_output(
    df: pd.DataFrame,
    mpc_results_json: str | None,
    output_step_size: int | None,
) -> tuple[pd.DataFrame, int]:
    payload = load_mpc_payload(mpc_results_json)
    if not payload:
        return df, 0

    records = payload.get("data") or []
    if not records:
        return df, 0

    working_df = df.copy()
    for column in ["device_type", "command_type", "device_name"]:
        if column not in working_df.columns:
            working_df[column] = ""

    simulation_start = pd.to_datetime(working_df.get("source_time"), errors="coerce").dropna().min()
    runtime_started = pd.to_datetime(working_df.get("gmt_create"), errors="coerce").dropna().min()
    effective_output_step_size = int(output_step_size or 0) if output_step_size is not None else 0
    if effective_output_step_size <= 0:
        effective_output_step_size = 3600

    biz_scenario_id = str(working_df["biz_scenario_id"].dropna().iloc[0])
    biz_scene_instance_id = str(working_df["biz_scenario_instance_id"].dropna().iloc[0])
    tenant_id = str(working_df["tenant_id"].dropna().iloc[0]) if "tenant_id" in working_df.columns and not working_df["tenant_id"].dropna().empty else ""
    waterway_id = str(working_df["waterway_id"].dropna().iloc[0]) if "waterway_id" in working_df.columns and not working_df["waterway_id"].dropna().empty else ""

    append_rows: list[dict[str, Any]] = []
    for item in records:
        step = item.get("step")
        if step is None:
            continue
        for detail in item.get("hydro_mpc_details") or []:
            if str(detail.get("command_type") or "") != "output_power":
                continue
            node_id = detail.get("node_id")
            object_id = detail.get("object_id")
            display_name = f"Turbine-{node_id}-{object_id}"
            source_time = None
            if pd.notna(simulation_start):
                source_time = (simulation_start + timedelta(seconds=int(step) * effective_output_step_size)).isoformat()
            append_rows.append(
                {
                    "attributes": None,
                    "back_water_flow": None,
                    "back_water_level": None,
                    "biz_scenario_id": biz_scenario_id,
                    "biz_scenario_instance_id": biz_scene_instance_id,
                    "data_index": int(step),
                    "edge_node_code": None,
                    "front_water_flow": None,
                    "front_water_level": None,
                    "gmt_create": runtime_started.isoformat() if pd.notna(runtime_started) else None,
                    "gmt_modified": runtime_started.isoformat() if pd.notna(runtime_started) else None,
                    "id": None,
                    "is_deleted": False,
                    "metrics_code": "output_power",
                    "value": detail.get("value"),
                    "object_id": object_id,
                    "object_name": display_name,
                    "object_status": None,
                    "object_type": "Turbine",
                    "position_code": "none",
                    "source_agent_type": None,
                    "source_id": "MPC",
                    "source_time": source_time,
                    "source_type": "MPC",
                    "tenant_id": tenant_id,
                    "waterway_id": waterway_id,
                    "device_type": "Turbine",
                    "command_type": "output_power",
                    "device_name": display_name,
                }
            )

    if not append_rows:
        return working_df, 0

    append_df = pd.DataFrame(append_rows)
    for column in working_df.columns:
        if column not in append_df.columns:
            append_df[column] = None
    for column in append_df.columns:
        if column not in working_df.columns:
            working_df[column] = None
    append_df = append_df[working_df.columns]
    working_df = pd.concat([working_df, append_df], ignore_index=True)
    return working_df, len(append_df)

BUSINESS_CATEGORY_ORDER = {"渠道": 0, "闸站": 1, "电站": 1, "倒虹吸": 2, "分水口": 3, "其他": 9}
STATION_BUSINESS_CATEGORIES = {"电站", "闸站"}


def is_station_business_category(category: Any) -> bool:
    return str(category or "").strip() in STATION_BUSINESS_CATEGORIES


def classify_gate_section_role(section: dict[str, Any], ref: dict[str, Any], section_index: int) -> str:
    role_hints = " ".join(
        str(value or "").strip()
        for value in (
            ref.get("aliasName"),
            ref.get("name"),
            section.get("aliasName"),
            section.get("alias_name"),
            section.get("name"),
        )
    )
    if "闸前" in role_hints:
        return "闸前断面"
    if "闸后" in role_hints:
        return "闸后断面"
    return "闸前断面" if ref.get("role") == "INLET" or section_index == 0 else "闸后断面"


def parse_child_refs(block: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for child_block in re.split(r"(?m)^\s+-\s*$", block):
        if not child_block.strip():
            continue
        id_match = re.search(r"(?m)^\s+id:\s*(\d+)\s*$", child_block)
        type_match = re.search(r"(?m)^\s+type:\s*(.+?)\s*$", child_block)
        name_match = re.search(r"(?m)^\s+name:\s*(.+?)\s*$", child_block)
        alias_match = re.search(r"(?m)^\s+alias_name:\s*(.+?)\s*$", child_block)
        if not id_match and not name_match:
            continue
        refs.append(
            {
                "id": int(id_match.group(1)) if id_match else None,
                "type": type_match.group(1).strip() if type_match else "",
                "name": name_match.group(1).strip() if name_match else "",
                "aliasName": alias_match.group(1).strip() if alias_match else "",
            }
        )
    return refs


def parse_business_objects(objects_yaml_text: str | None) -> dict[str, Any] | None:
    if not objects_yaml_text:
        return None

    sections = parse_cross_sections(objects_yaml_text)
    sections_by_name = {item["name"]: item for item in sections}
    sections_by_id = {int(item["id"]): item for item in sections if item.get("id") is not None}
    objects: list[dict[str, Any]] = []

    for source_index, block in enumerate(split_object_blocks(objects_yaml_text)):
        object_type = extract_block_value(block, "type")
        object_name = extract_block_value(block, "name")
        object_id = extract_block_value(block, "id")
        if not object_type or not object_name or not object_id:
            continue

        parameters = extract_nested_block(block, "parameters")
        location_match = re.search(r"\n\s*location:\s*([-\d.]+)", parameters)
        objects.append(
            {
                "id": int(object_id),
                "type": object_type,
                "name": object_name,
                "aliasName": extract_block_value(block, "alias_name") or "",
                "location": float(location_match.group(1)) if location_match else None,
                "sectionRefs": parse_cross_section_children(extract_nested_block(block, "cross_section_children")),
                "deviceRefs": parse_child_refs(extract_nested_block(block, "device_children")),
                "sourceIndex": source_index,
            }
        )

    return {
        "objects": objects,
        "sections": sections,
        "sectionsByName": sections_by_name,
        "sectionsById": sections_by_id,
    }


def resolve_section_ref(ref: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any] | None:
    section = None
    if ref.get("id") is not None and int(ref["id"]) in catalog["sectionsById"]:
        section = catalog["sectionsById"][int(ref["id"])]
    elif ref.get("name") and ref["name"] in catalog["sectionsByName"]:
        section = catalog["sectionsByName"][ref["name"]]
    if section and section.get("identity_role") == "source_duplicate":
        return None
    return section


def get_object_location(item: dict[str, Any], catalog: dict[str, Any]) -> float:
    if item.get("location") is not None:
        return float(item["location"])
    section_locations = [
        float(section["location"])
        for ref in item.get("sectionRefs", [])
        for section in [resolve_section_ref(ref, catalog)]
        if section is not None and section.get("location") is not None
    ]
    if section_locations:
        return sum(section_locations) / len(section_locations)
    return float("inf")


def collect_referenced_sections(item: dict[str, Any], catalog: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    unique: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    for ref in item.get("sectionRefs", []):
        section = resolve_section_ref(ref, catalog)
        if section is None:
            continue
        if abs(float(section.get("top_elevation", 0)) - float(section.get("bottom_elevation", 0))) < 1e-6:
            continue
        unique.setdefault(int(section["id"]), (section, ref))
    return sorted(
        unique.values(),
        key=lambda item_ref: (
            float(item_ref[0]["location"]),
            int(item_ref[0].get("source_index", 0)),
        ),
    )


def build_business_children(catalog: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not catalog:
        return []

    type_to_category = {
        "UnifiedCanal": "渠道",
        "GateStation": "闸站",
        "Pipe": "倒虹吸",
        "DisturbanceNode": "分水口",
    }
    children: list[dict[str, Any]] = []
    objects = sorted(
        catalog["objects"],
        key=lambda item: (
            BUSINESS_CATEGORY_ORDER.get(type_to_category.get(item["type"], "其他"), 9),
            get_object_location(item, catalog),
            item["sourceIndex"],
        ),
    )

    for object_order, item in enumerate(objects):
        object_type = item["type"]
        if object_type not in type_to_category:
            continue
        category = type_to_category[object_type]
        object_label = f"{item['name']}（{item['id']}）"

        if object_type == "UnifiedCanal":
            channel_sections = collect_referenced_sections(item, catalog)
            last_index = len(channel_sections) - 1
            middle_index = 0
            for section_index, (section, ref) in enumerate(channel_sections):
                ref_role = ref.get("role", "")
                if ref_role == "INLET" or section_index == 0:
                    role = "首断面"
                elif ref_role == "OUTLET" or section_index == last_index:
                    role = "尾断面"
                else:
                    middle_index += 1
                    role = f"中间断面 {middle_index}"
                children.append(
                    {
                        "sourceObjectType": "CrossSection",
                        "sourceObjectName": section["name"],
                        "sourceObjectId": section["id"],
                        "businessCategory": category,
                        "businessObjectName": item["name"],
                        "businessObjectId": item["id"],
                        "businessObjectLabel": object_label,
                        "businessObjectOrder": object_order,
                        "childRole": role,
                        "childOrder": section_index,
                        "defaultSelected": role in {"首断面", "尾断面"},
                    }
                )
            continue

        if object_type == "GateStation":
            for section_index, ref in enumerate(item.get("sectionRefs", [])):
                section = resolve_section_ref(ref, catalog)
                if not section:
                    continue
                role = classify_gate_section_role(section, ref, section_index)
                children.append(
                    {
                        "sourceObjectType": "CrossSection",
                        "sourceObjectName": section["name"],
                        "sourceObjectId": section["id"],
                        "businessCategory": category,
                        "businessObjectName": item["name"],
                        "businessObjectId": item["id"],
                        "businessObjectLabel": object_label,
                        "businessObjectOrder": object_order,
                        "childRole": role,
                        "childOrder": section_index,
                        "defaultSelected": True,
                    }
                )
            for gate_index, gate in enumerate(item.get("deviceRefs", [])):
                if gate.get("type") == "Gate" and gate.get("name"):
                    children.append(
                        {
                            "sourceObjectType": "Gate",
                            "sourceObjectName": gate["name"],
                            "sourceObjectId": gate.get("id"),
                            "businessCategory": category,
                            "businessObjectName": item["name"],
                            "businessObjectId": item["id"],
                            "businessObjectLabel": object_label,
                            "businessObjectOrder": object_order,
                            "childRole": "闸门设备",
                            "childOrder": 1000 + gate_index,
                            "defaultSelected": True,
                        }
                    )
                    continue
                if gate.get("type") != "Turbine" or not gate.get("name"):
                    continue
                children.append(
                    {
                        "sourceObjectType": "Turbine",
                        "sourceObjectName": gate["name"],
                        "sourceObjectId": gate.get("id"),
                        "businessCategory": category,
                        "businessObjectName": item["name"],
                        "businessObjectId": item["id"],
                        "businessObjectLabel": object_label,
                        "businessObjectOrder": object_order,
                        "childRole": "水轮机设备",
                        "childOrder": 2000 + gate_index,
                        "defaultSelected": True,
                    }
                )
            continue

        if object_type == "Pipe":
            children.append(
                {
                    "sourceObjectType": "Pipe",
                    "sourceObjectName": item["name"],
                    "sourceObjectId": item["id"],
                    "businessCategory": category,
                    "businessObjectName": item["name"],
                    "businessObjectId": item["id"],
                    "businessObjectLabel": object_label,
                    "businessObjectOrder": object_order,
                    "childRole": "倒虹吸本体",
                    "childOrder": 0,
                    "defaultSelected": True,
                }
            )
            for section_index, ref in enumerate(item.get("sectionRefs", [])):
                section = resolve_section_ref(ref, catalog)
                if not section:
                    continue
                role = "进口断面" if ref.get("role") == "INLET" or section_index == 0 else "出口断面"
                children.append(
                    {
                        "sourceObjectType": "CrossSection",
                        "sourceObjectName": section["name"],
                        "sourceObjectId": section["id"],
                        "businessCategory": category,
                        "businessObjectName": item["name"],
                        "businessObjectId": item["id"],
                        "businessObjectLabel": object_label,
                        "businessObjectOrder": object_order,
                        "childRole": role,
                        "childOrder": section_index + 1,
                        "defaultSelected": True,
                    }
                )
            continue

        children.append(
            {
                "sourceObjectType": "DisturbanceNode",
                "sourceObjectName": item["name"],
                "sourceObjectId": item["id"],
                "businessCategory": category,
                "businessObjectName": item["name"],
                "businessObjectId": item["id"],
                "businessObjectLabel": object_label,
                "businessObjectOrder": object_order,
                "childRole": "节点本体",
                "childOrder": 0,
                "defaultSelected": True,
            }
        )

    return children


def clone_series_with_business_meta(base_item: dict[str, Any], child: dict[str, Any], metric: str) -> dict[str, Any]:
    series_id = (
        f"{metric}|{child['businessCategory']}|{child['businessObjectId']}|"
        f"{child['sourceObjectType']}|{child['sourceObjectId'] or child['sourceObjectName']}|{child['childRole']}"
    )
    source_label = (
        f"{child['sourceObjectName']}（{child['sourceObjectId']}）"
        if child.get("sourceObjectId")
        else child["sourceObjectName"]
    )
    item = dict(base_item)
    item.update(
        {
            "seriesId": series_id,
            "sourceName": child["sourceObjectName"],
            "sourceObjectType": child["sourceObjectType"],
            "sourceObjectId": child.get("sourceObjectId"),
            "businessCategory": child["businessCategory"],
            "businessObjectName": child["businessObjectName"],
            "businessObjectId": child["businessObjectId"],
            "businessObjectLabel": child["businessObjectLabel"],
            "businessObjectOrder": child["businessObjectOrder"],
            "childRole": child["childRole"],
            "childOrder": child["childOrder"],
            "displayName": f"{child['childRole']}：{source_label}",
            "legendName": f"{child['businessObjectName']} / {child['childRole']}",
            "defaultSelected": bool(child.get("defaultSelected")),
        }
    )
    return item


def sort_business_series(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        series,
        key=lambda item: (
            BUSINESS_CATEGORY_ORDER.get(item.get("businessCategory", "其他"), 9),
            int(item.get("businessObjectOrder", 999999)),
            int(item.get("childOrder", 999999)),
            item.get("displayName") or item.get("name", ""),
        ),
    )


def build_business_metric_series(
    df: pd.DataFrame,
    metric: str,
    business_children: list[dict[str, Any]] | None = None,
    excluded_steps: set[int] | None = None,
    sort_key_func=None,
) -> list[dict[str, Any]]:
    base_series = build_metric_series(df, metric, excluded_steps, sort_key_func)
    if not business_children:
        return base_series

    referenced_section_ids = {
        int(child["sourceObjectId"])
        for child in business_children
        if child.get("sourceObjectType") == "CrossSection" and child.get("sourceObjectId") is not None
    }

    by_source_id = {
        (item["objectType"], item.get("objectId")): item
        for item in base_series
        if item.get("objectId") is not None
    }
    by_source = {(item["objectType"], item["name"]): item for item in base_series}
    series: list[dict[str, Any]] = []
    mapped_id_keys: set[tuple[str, int]] = set()
    mapped_keys: set[tuple[str, str]] = set()

    for child in business_children:
        id_key = (
            child["sourceObjectType"],
            int(child["sourceObjectId"]),
        ) if child.get("sourceObjectId") is not None else None
        key = (child["sourceObjectType"], child["sourceObjectName"])
        base_item = by_source_id.get(id_key) if id_key is not None else None
        if not base_item:
            base_item = by_source.get(key)
        if not base_item:
            continue
        series.append(clone_series_with_business_meta(base_item, child, metric))
        if id_key is not None:
            mapped_id_keys.add(id_key)
        else:
            mapped_keys.add(key)

    for base_item in base_series:
        object_id = base_item.get("objectId")
        if base_item["objectType"] == "CrossSection" and object_id is not None and int(object_id) not in referenced_section_ids:
            continue
        if object_id is not None and (base_item["objectType"], int(object_id)) in mapped_id_keys:
            continue
        key = (base_item["objectType"], base_item["name"])
        if key in mapped_keys:
            continue
        series.append(
            {
                **base_item,
                "seriesId": f"{metric}|fallback|{base_item['objectType']}|{base_item['name']}",
                "sourceName": base_item["name"],
                "sourceObjectType": base_item["objectType"],
                "businessCategory": "其他",
                "businessObjectName": "未归属对象",
                "businessObjectId": "fallback",
                "businessObjectLabel": "未归属对象",
                "businessObjectOrder": 999999,
                "childRole": base_item["objectType"],
                "childOrder": 999999,
                "displayName": base_item["name"],
                "legendName": base_item["name"],
                "defaultSelected": False,
            }
        )

    return sort_business_series(series)


def build_business_gate_series(
    df: pd.DataFrame,
    business_children: list[dict[str, Any]] | None = None,
    excluded_steps: set[int] | None = None,
    sort_key_func=None,
) -> list[dict[str, Any]]:
    base_series = build_gate_series(df, excluded_steps, sort_key_func)
    if not business_children:
        return base_series

    by_name = {item["name"]: item for item in base_series}
    series: list[dict[str, Any]] = []
    mapped_names: set[str] = set()
    for child in business_children:
        if child["sourceObjectType"] != "Gate":
            continue
        base_item = by_name.get(child["sourceObjectName"])
        if not base_item:
            continue
        series.append(clone_series_with_business_meta(base_item, child, "gate_opening"))
        mapped_names.add(child["sourceObjectName"])

    for base_item in base_series:
        if base_item["name"] in mapped_names:
            continue
        series.append(
            {
                **base_item,
                "seriesId": f"gate_opening|fallback|Gate|{base_item['name']}",
                "sourceName": base_item["name"],
                "sourceObjectType": "Gate",
                "businessCategory": "其他",
                "businessObjectName": base_item.get("filterType") or "未归属闸门",
                "businessObjectId": base_item.get("filterType") or "fallback",
                "businessObjectLabel": base_item.get("filterType") or "未归属闸门",
                "businessObjectOrder": 999999,
                "childRole": "闸门设备",
                "childOrder": 999999,
                "displayName": base_item["name"],
                "legendName": base_item["name"],
                "defaultSelected": False,
            }
        )

    return sort_business_series(series)


def build_longitudinal_profile_payload(
    df: pd.DataFrame, profile_dataset: dict[str, Any] | None, step_values: list[int]
) -> dict[str, Any]:
    if not profile_dataset:
        return {"available": False}

    base_points = profile_dataset["profile_points"]
    tracked_names = {item["name"] for item in base_points}
    level_df = df[(df["metrics_code"] == "water_level") & (df["object_name"].isin(tracked_names))].copy()
    level_df["data_index"] = level_df["data_index"].astype(int)
    water_lookup: dict[int, dict[str, float]] = {}
    for step, step_group in level_df.groupby("data_index", sort=True):
        water_lookup[int(step)] = {
            row.object_name: round_number(row.value, 3)  # type: ignore[arg-type]
            for row in step_group.itertuples(index=False)
        }

    frames: list[dict[str, Any]] = []
    max_water_level_all = None
    minimum_valid_points = max(3, math.ceil(len(base_points) * 0.6))
    for step in step_values:
        water_map = water_lookup.get(step, {})
        points = []
        for item in base_points:
            water = water_map.get(item["name"])
            if water is None:
                continue
            # Ignore physically impossible placeholder values such as step 0 zeros.
            if water < item["bottom_elevation"] - 0.05:
                continue
            points.append(
                {
                    "name": item["name"],
                    "location": item["location"],
                    "bottom_elevation": item["bottom_elevation"],
                    "top_elevation": item["top_elevation"],
                    "water_level": water,
                    "depth": round_number(water - item["bottom_elevation"], 3),
                }
            )
        if len(points) < minimum_valid_points:
            continue
        max_water_level_all = max(
            max_water_level_all or points[0]["water_level"],
            max(point["water_level"] for point in points),
        )
        frames.append({"step": step, "points": points})

    if not frames:
        return {"available": False}

    current_frame = frames[-1]
    start_point = current_frame["points"][0]
    end_point = current_frame["points"][-1]
    deepest = max(current_frame["points"], key=lambda item: item["depth"])
    shallowest = min(current_frame["points"], key=lambda item: item["depth"])
    head_loss = round_number(start_point["water_level"] - end_point["water_level"], 3)
    distance_km = abs(end_point["location"] - start_point["location"]) or float(profile_dataset["meta"].get("distance_km") or 0)
    water_slope = round_number(head_loss / distance_km, 3) if distance_km else None
    avg_depth = round_number(
        sum(point["depth"] for point in current_frame["points"]) / len(current_frame["points"]),
        3,
    )
    freeboard_points = [
        {
            **point,
            "freeboard": round_number(point["top_elevation"] - point["water_level"], 3),
        }
        for point in current_frame["points"]
    ]
    min_freeboard = min(freeboard_points, key=lambda item: item["freeboard"])
    overtopped_count = sum(1 for point in freeboard_points if point["freeboard"] < 0)
    low_freeboard_count = sum(1 for point in freeboard_points if 0 <= point["freeboard"] <= 0.5)

    meta = dict(profile_dataset["meta"])
    meta["max_water_level_all"] = round_number(max_water_level_all, 3)
    meta["timeline_step_count"] = len(frames)

    return {
        "available": True,
        "chartImage": "../charts/chart7_longitudinal_profile.png",
        "meta": meta,
        "gateMarkers": profile_dataset["gate_markers"],
        "objectAnnotations": profile_dataset.get("object_annotations", []),
        "sectionErrors": profile_dataset.get("profile_errors", profile_dataset.get("section_errors", [])),
        "objectErrors": profile_dataset.get("object_errors", []),
        "gateErrors": profile_dataset.get("gate_errors", []),
        "profileErrors": profile_dataset.get("profile_errors", []),
        "points": current_frame["points"],
        "frames": frames,
        "stepValues": [frame["step"] for frame in frames],
        "highlights": {
            "start": start_point,
            "end": end_point,
            "deepest": deepest,
            "shallowest": shallowest,
            "avg_depth": avg_depth,
            "water_slope": water_slope,
            "min_freeboard": min_freeboard,
            "overtopped_count": overtopped_count,
            "low_freeboard_count": low_freeboard_count,
        },
        "summary": (
            f"纵剖面最后时刻覆盖 {start_point['name']} 至 {end_point['name']}，共 "
            f"{len(current_frame['points'])} 个有效断面、约 {round_number(distance_km, 3)} km。"
            f"水面线从 {start_point['water_level']} m 降至 {end_point['water_level']} m，"
            f"沿程水头损失 {head_loss} m"
            f"{f'，平均水面坡降 {water_slope} m/km' if water_slope is not None else ''}。"
            f"平均水深 {avg_depth} m，最大水深断面为 {deepest['name']}（{deepest['depth']} m），"
            f"最小水深断面为 {shallowest['name']}（{shallowest['depth']} m）；"
            f"最小顶高程余量位于 {min_freeboard['name']}（{min_freeboard['freeboard']} m），"
            f"{'存在 ' + str(overtopped_count) + ' 个超顶断面' if overtopped_count else '未发现超顶断面'}，"
            f"{low_freeboard_count} 个断面余量不超过 0.50 m。"
        ),
    }


def describe_series_points(group: pd.DataFrame) -> str:
    ordered = group.sort_values("data_index")
    if ordered.empty:
        return "本次结果输出点不足，无法生成首末值描述"
    first_step = int(ordered["data_index"].iloc[0])
    last_step = int(ordered["data_index"].iloc[-1])
    first_value = round_number(ordered["value"].iloc[0])
    last_value = round_number(ordered["value"].iloc[-1])
    return f"{first_step} 步为 {first_value}，{last_step} 步为 {last_value}"


def build_report_data(
    df: pd.DataFrame,
    csv_path: Path,
    runtime_config: RuntimeConfig,
    scenario_meta: dict[str, Any] | None = None,
    scenario_yaml_url: str | None = None,
    llm_name: str | None = None,
    profile_dataset: dict[str, Any] | None = None,
    asset_status: dict[str, Any] | None = None,
    profile_error: str | None = None,
    location_map: dict[str, float] | None = None,
    objects_yaml_text: str | None = None,
    mpc_results_json: str | None = None,
    scenario_events_json: str | None = None,
) -> dict[str, Any]:
    location_map = location_map or {}
    sort_key_func = create_object_sort_key(location_map)
    business_catalog = parse_business_objects(objects_yaml_text)
    business_children = build_business_children(business_catalog)
    raw_unique_steps = sorted(int(step) for step in df["data_index"].unique().tolist())
    step_interval = runtime_config.csv_step_interval
    scenario_id = str(df["biz_scenario_id"].iloc[0])

    metric_counts = {key: int(value) for key, value in df["metrics_code"].value_counts().to_dict().items()}
    object_type_counts = {key: int(value) for key, value in df["object_type"].value_counts().to_dict().items()}
    scenario_requires_turbine_output = scenario_id in TURBINE_OUTPUT_REQUIRED_SCENARIOS
    coupling_summary = summarize_level_flow_coupling(df)
    station_power_summary = summarize_station_power_comparison(df, business_children, mpc_results_json)
    station_output_summary = summarize_station_output_composition(df, business_children)
    turbine_heatmap_summary = summarize_turbine_dispatch_heatmap(df)

    flow_df = df[df["metrics_code"] == "water_flow"].copy()
    level_df = df[df["metrics_code"] == "water_level"].copy()
    gate_df = df[(df["object_type"] == "Gate") & (df["metrics_code"] == "gate_opening")].copy()
    turbine_power_df = select_turbine_output_rows(df)
    if not turbine_power_df.empty:
        metric_counts["output_power"] = int(len(turbine_power_df))
        object_type_counts["Turbine"] = int(len(turbine_power_df))
    elif scenario_requires_turbine_output:
        metric_counts.setdefault("output_power", 0)
        object_type_counts.setdefault("Turbine", 0)
    placeholder_level_steps = detect_placeholder_steps(level_df)
    placeholder_flow_steps = detect_placeholder_steps(flow_df)
    placeholder_level_steps = preserve_only_available_sample(level_df, placeholder_level_steps)
    placeholder_flow_steps = preserve_only_available_sample(flow_df, placeholder_flow_steps)
    display_excluded_steps = sorted(set(placeholder_level_steps) | set(placeholder_flow_steps))
    if raw_unique_steps and set(raw_unique_steps).issubset(set(display_excluded_steps)):
        display_excluded_steps = []
    unique_steps = [step for step in raw_unique_steps if step not in display_excluded_steps] or raw_unique_steps
    level_display_df = level_df[~level_df["data_index"].astype(int).isin(placeholder_level_steps)].copy()
    flow_display_df = flow_df[~flow_df["data_index"].astype(int).isin(placeholder_flow_steps)].copy()
    gate_display_df = gate_df[~gate_df["data_index"].astype(int).isin(display_excluded_steps)].copy()
    turbine_power_display_df = turbine_power_df[~turbine_power_df["data_index"].astype(int).isin(display_excluded_steps)].copy()
    excluded_steps_by_metric = {
        "water_level": set(placeholder_level_steps),
        "water_flow": set(placeholder_flow_steps),
        "gate_opening": set(display_excluded_steps),
        "output_power": set(display_excluded_steps),
    }
    expected_steps_by_metric = {
        "water_level": set(int(step) for step in level_display_df["data_index"].unique().tolist()),
        "water_flow": set(int(step) for step in flow_display_df["data_index"].unique().tolist()),
        "gate_opening": set(int(step) for step in gate_display_df["data_index"].unique().tolist()),
        "output_power": set(int(step) for step in turbine_power_display_df["data_index"].unique().tolist()),
    }
    coupling_chart = build_coupling_chart_payload(df, set(display_excluded_steps))
    station_power_chart = build_station_power_chart_payload(
        df,
        business_children=business_children,
        excluded_steps=set(display_excluded_steps),
        mpc_results_json=mpc_results_json,
    )
    station_output_chart = build_station_output_composition_chart_payload(
        df,
        business_children=business_children,
        excluded_steps=set(display_excluded_steps),
    )
    turbine_heatmap_chart = build_turbine_dispatch_heatmap_payload(
        df,
        business_children=business_children,
        excluded_steps=set(display_excluded_steps),
    )
    negative_flow = flow_display_df[flow_display_df["value"] < 0].copy()
    asset_status = asset_status or {"required": [], "missing": [], "complete": True}
    zero_flow_groups = []
    constant_flow_groups = []
    dynamic_gate_groups = []
    completeness_issues = []
    turbine_output_missing = scenario_requires_turbine_output and turbine_power_df.empty

    for (object_name, metric, object_type), group in df.groupby(["object_name", "metrics_code", "object_type"], sort=False):
        expected_steps = expected_steps_by_metric.get(metric, set(raw_unique_steps))
        actual_steps = set(int(step) for step in group["data_index"].tolist()) - excluded_steps_by_metric.get(metric, set())
        if actual_steps != expected_steps:
            completeness_issues.append(
                {
                    "object": object_name,
                    "metric": metric,
                    "object_type": object_type,
                    "missing_steps": len(expected_steps - actual_steps),
                }
            )

    flow_name_column = "series_name" if "series_name" in flow_display_df.columns else "object_name"
    level_name_column = "series_name" if "series_name" in level_display_df.columns else "object_name"
    for (object_name, object_type), group in flow_display_df.groupby([flow_name_column, "object_type"], sort=False):
        values = group["value"]
        if (values == 0).all():
            zero_flow_groups.append((object_name, object_type, group))
        elif values.nunique() == 1:
            constant_flow_groups.append((object_name, object_type, group))

    for object_name, group in gate_display_df.groupby("object_name", sort=False):
        ordered = group.sort_values("data_index")
        values = ordered["value"].tolist()
        change_steps = []
        for index in range(1, len(values)):
            if abs(values[index] - values[index - 1]) >= GATE_OPENING_MIN_EFFECTIVE_CHANGE_M:
                change_steps.append(int(ordered["data_index"].iloc[index]))
        if change_steps:
            dynamic_gate_groups.append((object_name, ordered, change_steps))

    flow_range = (
        flow_display_df.groupby([flow_name_column, "object_type"])["value"]
        .agg(["min", "max", "mean", "std"])
        .assign(range=lambda frame: frame["max"] - frame["min"])
        .sort_values("range", ascending=False)
    )
    level_range = (
        level_display_df.groupby([level_name_column, "object_type"])["value"]
        .agg(["min", "max", "mean", "std"])
        .assign(range=lambda frame: frame["max"] - frame["min"])
        .sort_values("range", ascending=False)
    )
    turbine_power_range = (
        turbine_power_display_df.groupby("object_name")["value"]
        .agg(["min", "max", "mean", "std"])
        .assign(range=lambda frame: frame["max"] - frame["min"])
        .sort_values("range", ascending=False)
        if not turbine_power_display_df.empty
        else pd.DataFrame()
    )

    highlight_flow_name = None
    highlight_flow_type = None
    highlight_flow_stats = None
    highlight_flow_group = pd.DataFrame(columns=flow_display_df.columns)
    if not flow_range.empty:
        highlight_flow_name, highlight_flow_type = flow_range.index[0]
        highlight_flow_stats = flow_range.iloc[0]
        highlight_flow_group = flow_display_df[
            (flow_display_df[flow_name_column] == highlight_flow_name) & (flow_display_df["object_type"] == highlight_flow_type)
        ]
    highlight_flow_window_text = describe_variation_window(highlight_flow_group)
    highlight_flow_display_name = highlight_flow_name or "流量结果序列"
    highlight_turbine_name = None
    highlight_turbine_stats = None
    highlight_turbine_group = pd.DataFrame(columns=turbine_power_display_df.columns)
    if not turbine_power_range.empty:
        highlight_turbine_name = turbine_power_range.index[0]
        highlight_turbine_stats = turbine_power_range.iloc[0]
        highlight_turbine_group = turbine_power_display_df[turbine_power_display_df["object_name"] == highlight_turbine_name]
    highlight_flow_range_value = (
        float(highlight_flow_stats["range"])
        if highlight_flow_stats is not None and pd.notna(highlight_flow_stats["range"])
        else 0.0
    )
    highlight_flow_min_value = (
        float(highlight_flow_stats["min"])
        if highlight_flow_stats is not None and pd.notna(highlight_flow_stats["min"])
        else None
    )
    highlight_flow_max_value = (
        float(highlight_flow_stats["max"])
        if highlight_flow_stats is not None and pd.notna(highlight_flow_stats["max"])
        else None
    )

    last_step = unique_steps[-1]
    cs_level_df = level_df[level_df["object_type"] == "CrossSection"].copy()
    cs_last = cs_level_df[cs_level_df["data_index"] == last_step].copy()
    
    if location_map:
        cs_last["order"] = cs_last["object_name"].map(lambda n: location_map.get(n, float('inf')))
        cs_last = cs_last[cs_last["order"] != float('inf')]
        cs_last = cs_last.sort_values("order")
    else:
        cs_last = cs_last.sort_values("object_name")
        
    level_drop = 0
    if not cs_last.empty:
        start_level = round_number(cs_last["value"].iloc[0])
        end_level = round_number(cs_last["value"].iloc[-1])
        level_drop = round_number((start_level or 0) - (end_level or 0))

    anomaly_items: list[dict[str, str]] = []
    negative_points = int(len(negative_flow))
    negative_object_count = int(negative_flow.groupby([flow_name_column, "object_type"]).ngroups)
    negative_min_value = float(negative_flow["value"].min()) if not negative_flow.empty else None
    if not negative_flow.empty:
        negative_stats = (
            negative_flow.groupby([flow_name_column, "object_type"])["value"]
            .agg(["count", "min"])
            .sort_values("min", ascending=True)
        )
        worst_negative_name, _ = negative_stats.index[0]
        worst_negative_stats = negative_stats.iloc[0]
        anomaly_items.append(
            {
                "priority": "高",
                "object": str(worst_negative_name),
                "metric": "water_flow",
                "finding": (
                    f"检测到 {negative_points} 个负流量点、涉及 {negative_object_count} 条序列；"
                    f"其中该序列最小值为 {round_number(worst_negative_stats['min'])} m³/s，"
                    f"负值点数为 {int(worst_negative_stats['count'])}。"
                ),
                "advice": "按倒流异常处理，优先复核上下游水位差、稳态缓存、初始条件与控制命令生效时序。",
            }
        )
    if zero_flow_groups:
        object_name, _, group = zero_flow_groups[0]
        anomaly_items.append(
            {
                "priority": "中",
                "object": object_name,
                "metric": "water_flow",
                "finding": f"全部 {len(group)} 次结果输出的流量均为 0。",
                "advice": "确认该对象在当前工况下是否应参与配水（如保持关闭状态），必要时复核场景配置。",
            }
        )

    if highlight_flow_name is not None and highlight_flow_stats is not None:
        anomaly_items.append(
            {
                "priority": "中",
                "object": highlight_flow_name,
                "metric": "water_flow",
                "finding": (
                    f"流量最大变化幅度最大，最小 {round_number(highlight_flow_stats['min'])}、"
                    f"最大 {round_number(highlight_flow_stats['max'])}，幅度 {round_number(highlight_flow_stats['range'])}。"
                ),
                "advice": "复核该断面附近的分流、闸门动作或边界条件切换，确认是否属于预期工况响应。",
            }
        )

    if dynamic_gate_groups:
        gate_name, gate_group, gate_steps = dynamic_gate_groups[0]
        values = gate_group.sort_values("data_index")["value"].tolist()
        anomaly_items.append(
            {
                "priority": "低",
                "object": gate_name,
                "metric": "gate_opening",
                "finding": (
                    f"开度存在阶段切换，变化步包括 {', '.join(str(step) for step in gate_steps[:4])}，"
                    f"范围 {round_number(max(values) - min(values))}。"
                ),
                "advice": "建议结合控制策略或调度事件，验证闸门动作与断面流量变化是否同步。",
            }
        )

    if constant_flow_groups:
        names = "、".join(item[0] for item in constant_flow_groups[:4])
        anomaly_items.append(
            {
                "priority": "低",
                "object": "多个分水口/退水闸",
                "metric": "water_flow",
                "finding": f"{len(constant_flow_groups)} 个对象保持恒定非零流量，典型对象包括 {names}。",
                "advice": "若本次目的是做稳态校核可以接受；若要观察动态响应，建议注入事件或调整边界条件。",
            }
        )

    if turbine_output_missing:
        anomaly_items.append(
            {
                "priority": "高",
                "object": "梯级电站场景 200060",
                "metric": "output_power",
                "finding": "当前结果导出未包含 device_type=Turbine 且 command_type=output_power 的水轮机出力记录。",
                "advice": "将本次结果视为导出不完整；需要补齐水轮机出力后，再进行梯级电站场景的正式结果解读与对外汇报。",
            }
        )
    elif highlight_turbine_name is not None and highlight_turbine_stats is not None:
        anomaly_items.append(
            {
                "priority": "低",
                "object": highlight_turbine_name,
                "metric": "output_power",
                "finding": (
                    f"水轮机出力最大变化幅度为 {round_number(highlight_turbine_stats['range'])}，"
                    f"最小 {round_number(highlight_turbine_stats['min'])}、最大 {round_number(highlight_turbine_stats['max'])}。"
                ),
                "advice": "建议结合机组调度策略、上游来水和尾水位过程，复核该机组出力变化是否符合预期。",
            }
        )

    zero_flow_count = len(zero_flow_groups)
    constant_flow_count = len(constant_flow_groups)
    dynamic_gate_count = len(dynamic_gate_groups)
    stability_score = max(55, 95 - zero_flow_count * 4 - constant_flow_count - len(anomaly_items) * 2)
    control_score = min(85, 20 + dynamic_gate_count * 12 + (10 if highlight_flow_range_value > 20 else 0))
    highlight_level_name = "水位结果序列"
    highlight_level_type = None
    highlight_level_stats = {"min": None, "max": None, "mean": None, "std": None, "range": 0.0}
    highlight_level_group = pd.DataFrame(columns=level_display_df.columns)
    if not level_range.empty:
        highlight_level_name, highlight_level_type = level_range.index[0]
        highlight_level_stats = level_range.iloc[0]
        highlight_level_group = level_display_df[
            (level_display_df["object_name"] == highlight_level_name) & (level_display_df["object_type"] == highlight_level_type)
        ]
    highlight_level_window_text = describe_variation_window(highlight_level_group)

    runtime_started_at = pd.to_datetime(df["gmt_create"].min())
    runtime_completed_at = pd.to_datetime(df["gmt_create"].max())
    scenario_total_steps = runtime_config.total_steps if runtime_config.total_steps is not None else (
        scenario_meta["total_steps"] if scenario_meta and scenario_meta.get("total_steps") is not None else None
    )
    step_resolution_seconds = runtime_config.sim_step_size
    total_runtime_steps = (
        runtime_config.total_steps
        if runtime_config.total_steps is not None
        else unique_steps[-1]
    )
    simulation_start_dt = parse_datetime_text(scenario_meta["biz_start_time"]) if scenario_meta else None
    scenario_events_raw = load_scenario_events_payload(scenario_events_json)
    scenario_events_payload = build_scenario_events_payload(
        scenario_events_raw,
        simulation_start_dt,
        step_resolution_seconds,
    )
    scenario_event_responses = build_event_response_payload(
        df,
        scenario_events_raw,
        simulation_start_dt,
        step_resolution_seconds,
    )
    comparison_event_markers = [
        {
            "name": str(item.get("name") or "未命名事件"),
            "step": int(item["step"]),
            "scheduledTime": item.get("scheduledTime"),
            "priority": item.get("priority"),
        }
        for item in scenario_events_payload.get("items", [])
        if item.get("step") is not None
    ]
    if comparison_event_markers:
        coupling_chart["eventMarkers"] = comparison_event_markers
        station_power_chart["eventMarkers"] = comparison_event_markers
    comparison_decision_summary = build_comparison_decision_summary(
        station_power_chart,
        station_output_chart,
        scenario_events_payload,
        scenario_event_responses,
    )
    output_interval_seconds = runtime_config.output_step_size
    # Hydros total duration is counted in output intervals; sim_step_size is only
    # the internal calculation step and must not be used for coverage duration.
    simulation_end_dt = (
        simulation_start_dt + timedelta(seconds=total_runtime_steps * output_interval_seconds)
        if simulation_start_dt and output_interval_seconds is not None
        else None
    )
    simulation_duration_seconds = (
        total_runtime_steps * output_interval_seconds
        if total_runtime_steps is not None and output_interval_seconds is not None
        else None
    )
    sampled_duration_seconds = (
        (len(raw_unique_steps) - 1) * output_interval_seconds
        if output_interval_seconds is not None and len(raw_unique_steps) > 1
        else None
    )
    duration_gap_seconds = (
        simulation_duration_seconds - sampled_duration_seconds
        if simulation_duration_seconds is not None and sampled_duration_seconds is not None
        else None
    )
    sim_step_size_text = (
        f"{step_resolution_seconds} 秒/计算步（{format_duration_text(step_resolution_seconds)}）"
        if step_resolution_seconds is not None
        else "未提供，无法可靠推导"
    )
    output_step_text = (
        f"{output_interval_seconds} 秒/输出步（{format_duration_text(output_interval_seconds)}）"
        if output_interval_seconds is not None
        else (
            f"结果序号间隔 {step_interval}" if step_interval is not None else "无法可靠推导"
        )
    )
    simulation_duration_text = (
        f"{format_duration_text(simulation_duration_seconds)}（总步数 × 输出步长）"
        if simulation_duration_seconds is not None and total_runtime_steps is not None
        else (
            f"{total_runtime_steps} 个输出步长"
            if runtime_config.total_steps is not None
            else "根据当前结果文件无法可靠推导"
        )
    )
    raw_sampled_point_count = len(raw_unique_steps)
    display_sampled_point_count = len(unique_steps)
    if runtime_config.expected_sample_count is not None and runtime_config.expected_sample_count != raw_sampled_point_count:
        anomaly_items.insert(
            0,
            {
                "priority": "高",
                "object": "结果文件时间轴",
                "metric": "结果时间信息",
                "finding": (
                    f"按本次设置原本应看到约 {runtime_config.expected_sample_count} 次结果输出，但结果文件实际只有 {raw_sampled_point_count} 次结果输出；"
                    f"期望总时长 {format_seconds_text(simulation_duration_seconds) or '无法推导'}，"
                    f"按当前结果文件最多只能覆盖 {format_seconds_text(sampled_duration_seconds) or '无法推导'}。"
                ),
                "advice": "将该结果文件标记为时间信息不完整，报告中不要把文件里的编号直接解释为真实仿真步数；建议排查导出逻辑或补齐完整时间信息。",
            },
        )
    if asset_status["missing"]:
        anomaly_items.insert(
            0,
            {
                "priority": "中",
                "object": "报告产物完整性",
                "metric": "PNG 图表 / 纵剖面",
                "finding": f"本次正式报告缺少以下图表产物：{'、'.join(asset_status['missing'])}。",
                "advice": (
                    "HTML 已保留该缺失说明；解读时应注意缺失图表对应的分析维度不完整，"
                    "建议补拉 objects.yaml、重跑纵剖面或检查图表生成链路。"
                ),
            },
        )

    scenario_name = scenario_meta["scenario_name"] if scenario_meta and scenario_meta.get("scenario_name") else None
    recommendation_targets = [name for name in [highlight_flow_name, zero_flow_groups[0][0] if zero_flow_groups else None] if name]
    recommendation_target_text = "、".join(dict.fromkeys(recommendation_targets[:2])) or "关键变化区段"
    recommendation_actions = [
        "更细粒度输出步长",
        "关键节点控制动作复核",
        "工况事件补充校核",
    ]
    max_gate_change = (
        round_number(gate_df.groupby("object_name")["value"].agg(lambda s: s.max() - s.min()).max())
        if not gate_df.empty
        else None
    )
    leading_zero_flow_name = zero_flow_groups[0][0] if zero_flow_groups else None
    flow_condition_text = (
        (
            f"检测到 {negative_points} 个负流量点，涉及 {negative_object_count} 条序列，"
            f"最小值为 {round_number(negative_min_value)} m³/s，存在显著倒流风险"
        )
        if negative_points
        else "未发现负流量"
    )

    summary_paragraphs = [
        (
            f"本次研究围绕{scenario_name or f'场景 {scenario_id}'}开展水动力仿真结果分析，"
            f"重点复核主干渠及关键节点在当前工况下的水位、流量、闸门调节和沿程水面线变化。"
            f"本次结果文件共包含 {len(df)} 条记录，覆盖 {df['object_name'].nunique()} 个对象、"
            f"{df['metrics_code'].nunique()} 类指标，当前页面共展示 {display_sampled_point_count} 次结果输出，"
            f"覆盖仿真第 {unique_steps[0]} 步至第 {unique_steps[-1]} 步。"
        ),
        (
            f"结果表明，{flow_condition_text}；"
            f"主干断面在最后时刻的沿程水头损失约 {level_drop} m，整体仍符合上游高、下游低的基本水力梯度。"
            f"当前需要重点关注的是个别退水闸零流量，以及 {highlight_flow_name} 的局部流量最大变化幅度较大。"
        ),
        (
            f"综合分析认为，当前结果存在显著倒流异常，" if negative_points else
            f"综合分析认为，当前结果反映出方案总体运行平稳，"
        )
        + (
            f"但 {recommendation_target_text} 等敏感区段仍需进一步做重点核查。"
            f"建议下一阶段补充 {'、'.join(recommendation_actions)}，"
            f"以降低局部变化误判风险，并为后续设计复核和调度判断提供支撑。"
        ),
    ]
    if scenario_requires_turbine_output:
        summary_paragraphs.insert(
            1,
            (
                "由于当前场景属于梯级电站场景 200060，本轮结果解读额外要求校核水轮机出力链路。"
                + (
                    (
                        f"本次已识别到 {turbine_power_display_df['object_name'].nunique()} 台机组的出力序列，"
                        f"其中 {highlight_turbine_name} 的出力变化幅度最大，为 {round_number(highlight_turbine_stats['range'])}。"
                    )
                    if not turbine_power_display_df.empty and highlight_turbine_stats is not None
                    else "但当前导出结果未包含 `device_type=Turbine` 且 `command_type=output_power` 的机组出力记录，应视为导出不完整。"
                )
            ),
        )
    if runtime_config.expected_sample_count is not None and runtime_config.expected_sample_count != raw_sampled_point_count:
        summary_paragraphs[-1] += (
            f" 同时，按本次设置原本应看到约 {runtime_config.expected_sample_count} 次结果输出，"
            f"而结果文件实际仅导出 {raw_sampled_point_count} 次结果输出，说明结果文件的时间信息存在异常。"
        )

    summary_paragraph = "\n\n".join(summary_paragraphs)
    longitudinal_profile = build_longitudinal_profile_payload(df, profile_dataset, unique_steps)
    if not longitudinal_profile["available"] and profile_error:
        longitudinal_profile["reason"] = profile_error

    summary_bullets = [
        {
            "title": "运行表现",
            "body": (
                f"流向核查结果显示：{flow_condition_text}；"
                f"主干断面最后时刻沿程水头损失约 {level_drop} m，整体仍保持上游高、下游低的基本趋势。"
            ),
        },
        {
            "title": "变化规律",
            "body": (
                f"从当前结果看，变化主要集中在 {highlight_level_name}、{highlight_flow_display_name} 等关键位置；"
                "其余大多数区段过程较平顺，主干渠沿程水面线整体呈平滑下降。"
                if longitudinal_profile is not None
                else (
                    f"从当前结果曲线看，变化主要集中在 {highlight_level_name}、{highlight_flow_display_name} 等关键位置；"
                    "其余大多数区段过程较平顺。"
                )
            ),
        },
        {
            "title": "局部差异",
            "body": (
                (
                    f"{highlight_flow_name} 的流量最大变化幅度最明显，达到 {round_number(highlight_flow_range_value)} m³/s；"
                    "相比之下，其余大多数对象变化幅度更小，说明差异主要集中在局部关键节点。"
                )
                if highlight_flow_stats is not None
                else "本次结果输出步数较少，流量变化幅度无法可靠排序，建议把该项作为短时快跑结果解读。"
            ),
        },
        {
            "title": "异常情况",
            "body": (
                (
                    "梯级电站场景要求展示水轮机出力，但当前导出结果缺少 `Turbine/output_power` 记录，应先补齐导出数据后再做正式结论。"
                    if turbine_output_missing
                    else (
                        f"{leading_zero_flow_name} 最为特殊，全程结果均为 0，需先核查是正常停运、关闭状态，还是配置或取数异常。"
                        if leading_zero_flow_name
                        else (
                            f"当前最特殊的现象出现在 {highlight_flow_name}，其变化幅度明显高于其他对象，"
                            "需要结合工况进一步复核。"
                        )
                    )
                )
            ),
        },
        {
            "title": "机组出力",
            "body": (
                (
                    f"已识别 {turbine_power_display_df['object_name'].nunique()} 台水轮机出力序列，"
                    f"其中 {highlight_turbine_name} 的出力变化幅度最大，为 {round_number(highlight_turbine_stats['range'])}。"
                )
                if not turbine_power_display_df.empty and highlight_turbine_stats is not None
                else "当前结果未识别到可用于分析的水轮机出力序列。"
            ),
        },
        {
            "title": "重点复核",
            "body": (
                f"重点关注 {recommendation_target_text}。与其他位置相比，这两个位置对本次工况变化反应更明显，建议优先复核。"
                f" 本次共有 {len(dynamic_gate_groups)} 个闸门序列发生调节，最大开度变化 {max_gate_change}。"
                if dynamic_gate_groups and max_gate_change is not None
                else (
                    f"重点关注 {recommendation_target_text}。与其他位置相比，这些位置对本次工况变化反应更明显，建议优先复核。"
                )
            ),
        },
        {
            "title": "原因分析",
            "body": (
                "形成上述现象的主要原因，是主干渠整体仍受上游高、下游低的水力梯度控制，"
                "同时局部区段又叠加了分流、退水或闸门调节的影响，因此整体平稳、局部更敏感。"
            ),
        },
    ]
    if asset_status["missing"]:
        summary_bullets[3]["body"] += f" 另外，本次报告还缺少 {'、'.join(asset_status['missing'])}，相关图表维度需按缺失范围降级解读。"
    if runtime_config.expected_sample_count is not None and runtime_config.expected_sample_count != raw_sampled_point_count:
        summary_bullets[3]["body"] += (
            f" 另外，按本次设置原本应看到约 {runtime_config.expected_sample_count} 次结果输出，"
            f"但结果文件实际仅导出 {raw_sampled_point_count} 次结果输出。"
        )

    if not longitudinal_profile["available"]:
        summary_bullets[3]["body"] += (
            f" 纵剖面本次未生成，原因是 {profile_error or longitudinal_profile.get('reason') or '缺少对象高程/里程数据或生成链路失败'}。"
        )

    recommendations = [
        "优先确认发生零流量或极低流量的节点在该场景下是否应保持关闭，避免把配置状态误判为异常。",
        (
            f"复核 {highlight_flow_name} 附近的边界条件、分流关系和联动控制，解释其变化原因。"
            if highlight_flow_name
            else "本次结果输出步数较少，建议结合更多输出点复核流量变化过程。"
        ),
        (
            f"若需要更细的过程诊断，建议把输出步长从当前 {runtime_config.output_step_size} 秒/次缩短到 600-1200 秒/次。"
            if runtime_config.output_step_size
            else "若需要更细的过程诊断，建议缩短输出步长并重新导出结果。"
        ),
        "若后续要做动态评估，可叠加工况事件注入，观察闸门动作对沿程水位和分水口流量的传递影响。",
    ]
    if scenario_requires_turbine_output:
        recommendations.insert(
            0,
            (
                "优先核对结果导出链路是否包含 `device_type=Turbine`、`command_type=output_power` 的机组出力记录。"
                if turbine_output_missing
                else "结合梯级电站调度目标复核机组出力过程，确认各台水轮机的负荷分配与水位流量过程是否一致。"
            ),
        )

    mini_table = []
    for _, row in (
        df.sort_values(["data_index", "object_name", "metrics_code"])
        .loc[:, ["object_name", "metrics_code", "data_index", "value"]]
        .head(6)
        .iterrows()
    ):
        mini_table.append(
            {
                "object_name": row["object_name"],
                "metrics_code": row["metrics_code"],
                "data_index": int(row["data_index"]),
                "value": round_number(row["value"]),
            }
        )

    report_title = f"{scenario_name} 分析报告" if scenario_name else "Hydros 仿真分析报告"

    condition_text = (
        f"总步数 {scenario_total_steps}、输出步长 {output_interval_seconds} 秒/次的当前工况"
        if scenario_total_steps is not None and output_interval_seconds is not None
        else "当前仿真工况"
    )
    overall_operation_text = (
        "主干渠整体保持稳定输水，未见明显倒流和突发失稳"
        if negative_flow.empty
        else f"检测到 {negative_points} 个负流量点，存在显著倒流风险"
    )
    overall_control_text = (
        "闸门调节过程总体平稳"
        if dynamic_gate_groups
        else "闸门运行状态总体平稳"
    )
    if scenario_requires_turbine_output:
        overall_control_text += (
            "，且已纳入机组出力校核"
            if not turbine_output_missing
            else "，但机组出力数据缺失"
        )
    overall_risk_text = (
        "局部节点仍需结合零流量和变化较大区段继续复核"
        if zero_flow_groups or highlight_flow_stats is not None
        else "当前未见突出的局部异常"
    )
    if turbine_output_missing:
        overall_risk_text = "当前结果导出缺少梯级电站场景要求的水轮机出力数据，正式结论存在信息缺口"
    overall_judgement_text = (
        "当前结果未见明显整体失稳迹象"
        if asset_status["complete"] and not runtime_config.has_unreliable_time_axis and negative_flow.empty and not turbine_output_missing
        else "当前结果还需结合缺失图表或时间轴情况继续核查"
    )

    risk_area_names: list[str] = []
    risk_findings: list[str] = []
    if zero_flow_groups:
        risk_area_names.append(zero_flow_groups[0][0])
        risk_findings.append("局部节点长时间零流量")
    if highlight_flow_name:
        risk_area_names.append(highlight_flow_name)
        risk_findings.append("流量最大变化幅度偏大")
    if dynamic_gate_groups:
        risk_area_names.append(dynamic_gate_groups[0][0])
        risk_findings.append("控制动作存在阶段切换")
    if turbine_output_missing:
        risk_area_names.append("梯级电站机组出力")
        risk_findings.append("缺少水轮机出力导出记录")
    elif highlight_turbine_name:
        risk_area_names.append(highlight_turbine_name)
        risk_findings.append("机组出力变化需要与调度策略联动核查")

    risk_area_text = "、".join(dict.fromkeys(risk_area_names[:3])) if risk_area_names else "当前未发现集中的高风险区域"
    risk_finding_text = "、".join(dict.fromkeys(risk_findings[:3])) if risk_findings else "以局部变化区段复核为主"

    max_level_variation_text = f"{round_number(highlight_level_stats['range'])} m"
    max_flow_variation_text = (
        f"{round_number(highlight_flow_stats['range'])} m³/s"
        if highlight_flow_stats is not None
        else "无法可靠提取"
    )
    key_range_area_text = highlight_flow_name or highlight_level_name or "主干渠重点区段"
    range_assessment_text = (
        "需要重点复核"
        if zero_flow_groups or not negative_flow.empty
        else "仍处于可控范围内"
    )

    payload = {
        "resultFilePath": csv_path.name,
        "meta": {
            "report_title": report_title,
            "biz_scene_instance_id": str(df["biz_scenario_instance_id"].iloc[0]),
            "biz_scenario_id": scenario_id,
            "scenario_name": scenario_name or f"场景 {scenario_id}",
            "tenant_id": str(df["tenant_id"].iloc[0]),
            "task_status": "已完成",
            "task_status_raw": "COMPLETED",
            "total_steps": scenario_total_steps if scenario_total_steps is not None else len(unique_steps),
            "sampled_point_count": len(unique_steps),
            "completed_at": format_datetime_text(runtime_completed_at.to_pydatetime()) or str(df["gmt_create"].max()),
            "runtime_started_at": format_datetime_text(runtime_started_at.to_pydatetime()) or str(df["gmt_create"].min()),
            "scenario_yaml_id": (
                scenario_meta["scenario_yaml_id"]
                if scenario_meta
                else (Path(urlsplit(scenario_yaml_url).path).name if scenario_yaml_url else None)
            ),
            "scenario_yaml_url": scenario_meta["scenario_yaml_url"] if scenario_meta else scenario_yaml_url,
            "simulation_start_time": format_datetime_text(simulation_start_dt),
            "simulation_end_time": format_datetime_text(simulation_end_dt),
            "simulation_duration": simulation_duration_text,
            "sampled_duration": format_seconds_text(sampled_duration_seconds) or "无法推导",
            "duration_gap": format_seconds_text(duration_gap_seconds) or "无法推导",
            "sim_step_size": step_resolution_seconds,
            "output_step_size": runtime_config.output_step_size,
            "sim_step_size_text": sim_step_size_text,
            "output_step_text": output_step_text,
            "time_axis_note": runtime_config.axis_note,
            "axis_label": runtime_config.axis_label,
            "analyst": llm_name,
            "record_count": int(len(df)),
            "object_count": int(df["object_name"].nunique()),
            "metric_count": int(df["metrics_code"].nunique()),
            "negative_flow_points": negative_points,
            "negative_flow_objects": negative_object_count,
            "negative_flow_min_value": round_number(negative_min_value),
            "zero_flow_objects": zero_flow_count,
            "water_level_series_count": int(level_df.groupby(["series_name", "object_type"]).ngroups),
            "water_flow_series_count": int(flow_df.groupby(["series_name", "object_type"]).ngroups),
            "gate_series_count": int(gate_df.groupby("object_name").ngroups),
            "turbine_output_series_count": int(turbine_power_df.groupby("object_name").ngroups) if not turbine_power_df.empty else 0,
            "turbine_output_required": scenario_requires_turbine_output,
            "turbine_output_missing": turbine_output_missing,
            "report_asset_complete": asset_status["complete"],
            "missing_report_assets": asset_status["missing"],
        },
        "metaCards": [],
        "headlineCards": [
            {
                "eyebrow": "关键总结 1",
                "title": "总体结论",
                "body": (
                    f"{overall_operation_text}，{overall_control_text}，{overall_risk_text}，"
                    f"{overall_judgement_text}。"
                ),
            },
            {
                "eyebrow": "关键总结 2",
                "title": "主要风险",
                "body": (
                    f"风险主要出现在 {risk_area_text}，主要表现为 {risk_finding_text}。"
                    if risk_area_names
                    else "当前未发现集中爆发的高风险区域，主要风险集中在局部变化区段解释和配置复核。"
                ),
            },
            {
                "eyebrow": "关键总结 3",
                "title": "影响范围与程度",
                "body": (
                    f"断面 {highlight_level_name} 的水位最大变化幅度约为 {max_level_variation_text}，"
                    f"{(highlight_flow_name + ' 的流量最大变化幅度约为 ' + max_flow_variation_text) if highlight_flow_name else ('流量最大变化幅度约为 ' + max_flow_variation_text)}，"
                    f"其中 {key_range_area_text} {range_assessment_text}。"
                ),
            },
            {
                "eyebrow": "关键总结 4",
                "title": "建议措施",
                "body": (
                    f"建议优先对 {recommendation_target_text} 做重点核查，并补充 "
                    f"{'、'.join(recommendation_actions)}，以降低局部变化误判风险，并支撑后续设计决策。"
                ),
            },
        ],
        "summaryParagraph": summary_paragraph,
        "summaryParagraphs": summary_paragraphs,
        "summaryBullets": summary_bullets,
        "scenarioEvents": scenario_events_payload,
        "scenarioEventResponses": scenario_event_responses,
        "comparisonDecisionSummary": comparison_decision_summary,
        "anomalies": anomaly_items,
        "recommendations": recommendations,
        "riskBars": [
            {"label": "倒流风险", "value": min(100, negative_points * 8)},
            {"label": "控制滞后", "value": control_score},
            {"label": "总体稳定性", "value": stability_score},
        ],
        "snapshotRows": [
            {"label": "任务状态", "value": "已完成"},
            {"label": "场景 ID", "value": scenario_id},
            {"label": "开始时间", "value": format_datetime_text(simulation_start_dt) or "场景 YAML 未提供"},
            {"label": "结束时间", "value": format_datetime_text(simulation_end_dt) or "根据显式参数与场景信息无法推导"},
            {"label": "计算步长", "value": sim_step_size_text},
            {"label": "输出步长", "value": output_step_text},
            {"label": "仿真时长", "value": simulation_duration_text},
            {"label": "工况事件数", "value": str(scenario_events_payload["count"])},
            {"label": "结果文件覆盖时长", "value": format_seconds_text(sampled_duration_seconds) or "无法推导"},
            {"label": "时长差值", "value": format_seconds_text(duration_gap_seconds) or "无法推导"},
            {"label": "结果覆盖步段", "value": f"{runtime_config.sample_step_note}（共输出 {display_sampled_point_count} 次结果）"},
            {"label": "结果导出时间", "value": format_datetime_text(runtime_completed_at.to_pydatetime()) or str(df["gmt_create"].max())},
        ],
        "miniTable": mini_table,
        "charts": {
            "levelSeries": build_business_metric_series(
                df, "water_level", business_children, set(placeholder_level_steps), sort_key_func
            ),
            "flowSeries": build_business_metric_series(
                df, "water_flow", business_children, set(placeholder_flow_steps), sort_key_func
            ),
            "gateSeries": build_business_gate_series(df, business_children, set(display_excluded_steps), sort_key_func),
            "turbinePowerSeries": build_business_turbine_series(
                df, business_children, set(display_excluded_steps), sort_key_func
            ),
            "couplingComparison": coupling_chart,
            "stationPowerComparison": station_power_chart,
            "stationOutputComposition": station_output_chart,
            "turbineDispatchHeatmap": turbine_heatmap_chart,
        },
        "chartInterpretations": {
            "level": {
                "analysis": (
                    f"水位结果曲线整体变化不大，{highlight_level_name} 的最大变化幅度为 "
                    f"{round_number(highlight_level_stats['range'])} m；默认建议优先查看断面序列的同步变化。"
                    f"渠道水位下降速率按输出步长折算，{WATER_LEVEL_DROP_WARN_RATE_M_PER_H} m/h 作为关注阈值，"
                    f"{WATER_LEVEL_DROP_CONTROL_RATE_M_PER_H} m/h 作为控制阈值。"
                ),
                "placeholder_steps": placeholder_level_steps,
            },
            "flow": {
                "analysis": (
                    (
                        (
                            "流量结果存在显著负值，不能按稳定顺向输水解读；"
                            if negative_points
                            else "流量结果曲线以稳定输水为主，"
                        )
                        + f"{highlight_flow_name} 的最大变化幅度为 "
                        f"{round_number(highlight_flow_range_value)} m³/s。"
                    )
                    if highlight_flow_stats is not None
                    else "本次结果输出点较少，流量变化幅度无法可靠排序。"
                ),
                "placeholder_steps": placeholder_flow_steps,
            },
            "gate": {
                "analysis": (
                    f"闸门结果曲线共 {int(gate_df.groupby('object_name').ngroups)} 条，"
                    f"{dynamic_gate_count} 条存在明显开度切换，适合与水位、流量阶段变化联动解释。"
                    f"闸门开度允许为正值或负值，开度变幅按绝对值检查，"
                    f"≥ {GATE_OPENING_MIN_EFFECTIVE_CHANGE_M} m 计为有效变化。"
                ),
                "placeholder_steps": display_excluded_steps,
                "dynamic_gate_count": dynamic_gate_count,
            },
            "coupling": {
                "analysis": coupling_summary["analysis"],
                "sections": coupling_summary["sections"],
                "available": coupling_summary["available"],
            },
            "stationPower": {
                "analysis": station_power_summary["analysis"],
                "stations": station_power_summary["stations"],
                "available": station_power_summary["available"],
            },
            "stationComposition": {
                "analysis": station_output_summary["analysis"],
                "stations": station_output_summary["stations"],
                "available": station_output_summary["available"],
            },
            "turbineHeatmap": {
                "analysis": turbine_heatmap_summary["analysis"],
                "turbines": turbine_heatmap_summary["turbines"],
                "available": turbine_heatmap_summary["available"],
            },
            "turbine": {
                "analysis": (
                    (
                        f"已识别 {int(turbine_power_df.groupby('object_name').ngroups)} 台水轮机的出力结果曲线，"
                        f"{highlight_turbine_name} 的出力变化幅度最大，为 {round_number(highlight_turbine_stats['range'])}。"
                    )
                    if not turbine_power_df.empty and highlight_turbine_stats is not None
                    else (
                        "梯级电站场景要求展示水轮机出力，但当前导出结果未包含 `device_type=Turbine`、`command_type=output_power` 记录。"
                        if scenario_requires_turbine_output
                        else "当前结果未包含可用于展示的水轮机出力序列。"
                    )
                ),
                "placeholder_steps": display_excluded_steps,
                "required": scenario_requires_turbine_output,
                "missing": turbine_output_missing,
            },
        },
        "analysisSummary": {
            "step_values": unique_steps,
            "raw_step_values": raw_unique_steps,
            "step_interval": step_interval,
            "scenario_total_steps": scenario_total_steps,
            "sim_step_size": step_resolution_seconds,
            "output_step_size": runtime_config.output_step_size,
            "step_resolution_seconds": step_resolution_seconds,
            "total_output_steps": total_runtime_steps,
            # Backward-compatible field name for older templates.
            "last_calculation_step": total_runtime_steps,
            "simulation_start_time": format_datetime_text(simulation_start_dt),
            "simulation_end_time": format_datetime_text(simulation_end_dt),
            "simulation_duration": simulation_duration_text,
            "sampled_duration": format_seconds_text(sampled_duration_seconds),
            "duration_gap": format_seconds_text(duration_gap_seconds),
            "output_step_text": output_step_text,
            "axis_mode": runtime_config.axis_mode,
            "axis_label": runtime_config.axis_label,
            "axis_note": runtime_config.axis_note,
            "sample_step_note": runtime_config.sample_step_note,
            "expected_sample_count": runtime_config.expected_sample_count,
            "raw_sampled_point_count": raw_sampled_point_count,
            "display_sampled_point_count": display_sampled_point_count,
            "metric_counts": metric_counts,
            "object_type_counts": object_type_counts,
            "turbine_output_required": scenario_requires_turbine_output,
            "turbine_output_missing": turbine_output_missing,
            "turbine_output_series_count": int(turbine_power_df.groupby("object_name").ngroups) if not turbine_power_df.empty else 0,
            "top_flow_variation": {
                "object_name": highlight_flow_name,
                "object_type": highlight_flow_type,
                "min": round_number(highlight_flow_min_value),
                "max": round_number(highlight_flow_max_value),
                "range": round_number(highlight_flow_range_value),
                "description": describe_series_points(highlight_flow_group),
            },
            "top_turbine_output_variation": {
                "object_name": highlight_turbine_name,
                "min": round_number(float(highlight_turbine_stats["min"])) if highlight_turbine_stats is not None and pd.notna(highlight_turbine_stats["min"]) else None,
                "max": round_number(float(highlight_turbine_stats["max"])) if highlight_turbine_stats is not None and pd.notna(highlight_turbine_stats["max"]) else None,
                "range": round_number(float(highlight_turbine_stats["range"])) if highlight_turbine_stats is not None and pd.notna(highlight_turbine_stats["range"]) else None,
                "description": describe_series_points(highlight_turbine_group),
            },
            "top_level_variation": {
                "object_name": highlight_level_name,
                "object_type": highlight_level_type,
                "range": round_number(highlight_level_stats["range"]),
            },
            "completeness_issues": completeness_issues,
            "placeholder_steps": {
                "water_level": placeholder_level_steps,
                "water_flow": placeholder_flow_steps,
                "display_excluded_steps": display_excluded_steps,
            },
            "report_assets": asset_status,
            "profile_error": profile_error,
        },
        "longitudinalProfile": longitudinal_profile,
    }
    return payload


def write_markdown_report(report_dir: Path, payload: dict[str, Any]) -> None:
    analysis = payload["analysisSummary"]
    profile = payload["longitudinalProfile"]
    scenario_events = payload.get("scenarioEvents", {})
    scenario_event_responses = payload.get("scenarioEventResponses", {})
    comparison_summary = payload.get("comparisonDecisionSummary", {})
    asset_status = payload["analysisSummary"].get("report_assets", {})
    missing_assets = asset_status.get("missing", [])
    negative_points = int(payload.get("meta", {}).get("negative_flow_points") or 0)
    negative_objects = int(payload.get("meta", {}).get("negative_flow_objects") or 0)
    negative_min_value = payload.get("meta", {}).get("negative_flow_min_value")
    flow_direction_statement = (
        f"检测到 {negative_points} 个负流量点，涉及 {negative_objects} 条序列，"
        f"最小值 {negative_min_value} m³/s，不能判定为稳定顺向输水。"
        if negative_points
        else "未检测到负流量，当前结果未见明显倒流。"
    )
    anomaly_rows = "\n".join(
        f"| {item['priority']} | {item['object']} | {item['metric']} | {item['finding']} | {item['advice']} |"
        for item in payload["anomalies"]
    )
    profile_markdown = ""
    if profile["available"]:
        profile_markdown = f"""
### 6. 渠道纵剖面

![渠道纵剖面](../charts/chart7_longitudinal_profile.png)

{profile['summary']} 图中同时标出了各闸站位置，便于把控制动作和沿程水面线一起解释。
"""
    else:
        profile_markdown = f"""
### 6. 渠道纵剖面

本次未生成渠道纵剖面图。原因：{profile.get('reason') or payload['analysisSummary'].get('profile_error') or '缺少对象高程/里程数据或生成链路失败'}。
"""
    comparison_summary_markdown = ""
    if comparison_summary.get("available"):
        response_rows = "\n".join(
            f"- {item['eventName']}：{item['responseText']}。{item['summary']}"
            for item in comparison_summary.get("responseHighlights", [])
        )
        if not response_rows:
            response_rows = "- 当前尚未提炼出可稳定复用的事件前后关键响应摘要。"
        comparison_summary_markdown = f"""
### 7. 水动力响应与 MPC 调度对照摘要

{comparison_summary.get('narrative') or comparison_summary.get('summary') or '当前已形成站级来流-出力对照摘要。'}

{response_rows}
"""
    asset_issue_markdown = ""
    if missing_assets:
        asset_issue_markdown = (
            f"- 报告图表产物存在缺失：`{'`、`'.join(missing_assets)}`。\n"
            "- HTML 页面已显式标注该问题；相关图表维度应按缺失范围降级解读，不应视为“完整图表已全部产出”。"
        )
    station_composition_markdown = ""
    station_composition_info = payload["chartInterpretations"].get("stationComposition", {})
    if station_composition_info.get("available"):
        station_composition_markdown = f"""
### 6. 梯级总出力构成

![梯级总出力构成](../charts/chart10_station_output_composition.png)

{station_composition_info['analysis']} 这张图更适合从站间分工角度看“谁在承担主力、谁在接力调节、谁在平台切换时退让”。
"""
    turbine_heatmap_markdown = ""
    turbine_heatmap_info = payload["chartInterpretations"].get("turbineHeatmap", {})
    if turbine_heatmap_info.get("available"):
        turbine_heatmap_markdown = f"""
### 7. 机组分组堆叠面积图

![机组分组堆叠面积图](../charts/chart11_turbine_dispatch_heatmap.png)

{turbine_heatmap_info['analysis']} 这张图更适合直观看出哪些机组长期承担主力、哪些机组在相邻时段接力，以及负荷是否长期集中在少数机组。
"""
    if "不可靠" in str(payload["meta"].get("time_axis_note", "")):
        conclusion_axis_line = (
            "- 本次结果文件在数值层面可用于结果分析，但时间信息不完整；"
            "报告已按可用参数恢复时长判断，并把图表横轴降级为结果输出顺序。"
        )
    else:
        conclusion_axis_line = (
            f"- 本次结果文件中的横轴可按{payload['meta'].get('axis_label', '仿真步')}理解，横轴含义明确；"
            f"本次报告覆盖仿真第 `{analysis['step_values'][0]}` 步至第 `{analysis['step_values'][-1]}` 步，共输出 `{payload['meta']['sampled_point_count']}` 次结果。"
        )

    duration_gap_text = str(payload["meta"].get("duration_gap", ""))
    if duration_gap_text.startswith("0 秒"):
        conclusion_duration_line = (
            f"- 结果文件覆盖时长与当前可推导的仿真总时长一致，时长差值为 `{duration_gap_text}`，"
            "可用于完整过程复盘。"
        )
    else:
        conclusion_duration_line = (
            f"- 用户参数推导的总时长与结果文件覆盖时长存在差异，当前差值为 `{duration_gap_text}`；"
            "需优先排查结果文件导出链路，再决定是否可用于严格时间过程分析。"
        )
    gate_dynamic_count = int(payload.get("chartInterpretations", {}).get("gate", {}).get("dynamic_gate_count") or 0)
    gate_curve_followup = (
        "存在有效阶跃变化，说明场景中存在控制动作，而不是完全静态工况。"
        if gate_dynamic_count > 0
        else f"当前未识别到 ≥ {GATE_OPENING_MIN_EFFECTIVE_CHANGE_M} m 的有效开度阶跃，整体更接近静态或微调工况。"
    )
    scenario_events_markdown = ""
    if scenario_events.get("available"):
        event_rows = "\n".join(
            f"| {item['name']} | {item['stepText']} | {item.get('scheduledTime') or '-'} | "
            f"{item['priority']} | {item.get('seriesSummary') or '-'} | {item['description']} |"
            for item in scenario_events.get("items", [])
        )
        response_lookup = {
            str(item.get("eventName") or ""): item
            for item in scenario_event_responses.get("items", [])
            if isinstance(item, dict)
        }
        response_blocks = []
        for item in scenario_events.get("items", []):
            response_info = response_lookup.get(str(item.get("name") or ""))
            if not response_info:
                continue
            response_lines = "\n".join(
                f"- `{resp['objectName']}`（{resp['objectType']} / {resp['metricCode']}）：事件前均值 `{resp['beforeMean']}`，事件后均值 `{resp['afterMean']}`，变化 `{resp['delta']}`"
                for resp in response_info.get("responses", [])
            )
            if not response_lines:
                response_lines = "- 当前未识别出超过阈值的关键断面/站点响应。"
            response_blocks.append(
                f"""### {item['name']} 的前后响应解读

{response_info.get('summary') or '当前未形成可读的前后窗口响应解读。'}

{response_lines}
"""
            )
        response_markdown = "\n".join(response_blocks)
        scenario_events_markdown = f"""
## 工况事件

{scenario_events.get('summary')}

| 事件 | 注入步号 | 推导时间 | 优先级 | 作用对象/时序 | 说明 |
| --- | --- | --- | --- | --- | --- |
{event_rows}

### 事件前后关键断面/站点响应

{scenario_event_responses.get('summary') or '当前未形成可展示的事件前后响应分析。'}

{response_markdown}
"""
    markdown = f"""# {payload['meta']['report_title']}

## 概况

- 场景 ID：`{payload['meta']['biz_scenario_id']}`
- 任务状态：`{payload['meta']['task_status']}`
- 开始时间：`{payload['meta']['simulation_start_time'] or '场景 YAML 未提供'}`
- 结束时间：`{payload['meta']['simulation_end_time'] or '根据结果文件无法推导'}`
- 计算步长：`{payload['meta']['sim_step_size_text']}`
- 输出步长：`{payload['meta']['output_step_text']}`
- 仿真时长：`{payload['meta']['simulation_duration']}`
- 结果文件覆盖时长：`{payload['meta']['sampled_duration']}`
- 时长差值：`{payload['meta']['duration_gap']}`
- 记录数：`{payload['meta']['record_count']}`
- 对象数：`{payload['meta']['object_count']}`
- 指标数：`{payload['meta']['metric_count']}`
- 结果覆盖步段：{payload['meta'].get('sample_step_note') or f"第 `{analysis['step_values'][0]}` 次至第 `{analysis['step_values'][-1]}` 次输出"}，共输出 `{payload['meta']['sampled_point_count']}` 次结果
- 配置总输出步数：`{payload['meta']['total_steps']}`
## 执行摘要

{payload['summaryParagraph']}

### 关键发现

{chr(10).join(f"- **{item['title']}**：{item['body']}" for item in payload['summaryBullets'])}

## 指标分布

### 对象类型分布

| 类型 | 记录数 |
| --- | ---: |
{chr(10).join(f"| {name} | {count} |" for name, count in analysis['object_type_counts'].items())}

### 指标分布

| 指标 | 记录数 |
| --- | ---: |
{chr(10).join(f"| {name} | {count} |" for name, count in analysis['metric_counts'].items())}

## 异常与建议

| 优先级 | 对象 | 指标 | 现象 | 建议 |
| --- | --- | --- | --- | --- |
{anomaly_rows}

{scenario_events_markdown}

## 图表分析

### 1. 水位时序

![关键断面水位时序](../charts/chart1_water_level.png)

{payload['chartInterpretations']['level']['analysis']} 沿程断面平稳下降，整体水力坡降关系清晰。

### 2. 流量时序

![关键断面流量时序](../charts/chart2_water_flow.png)

{payload['chartInterpretations']['flow']['analysis']} {flow_direction_statement}

### 3. 闸门开度

![闸门开度时序](../charts/chart4_gate_opening.png)

{payload['chartInterpretations']['gate']['analysis']} {gate_curve_followup}

### 4. 分水口/退水闸流量

![分水口流量时序](../charts/chart5_disturbance_flow.png)

分流/退水节点侧呈现“少数动态、多数恒定”的特征。部分节点全程为零或维持恒定流量，更像稳态配水结果而非持续调节过程。

{comparison_summary_markdown}

### 5. 水位-流量联动对比

![关键断面水位-流量联动对比](../charts/chart8_level_flow_coupling.png)

{payload['chartInterpretations']['coupling']['analysis']} 这张图更适合快速确认局部调节是“流量先变”还是“水位跟随”。

### 6. 梯级电站来流-出力对比

![梯级电站来流-出力对比](../charts/chart9_station_inflow_power_comparison.png)

{payload['chartInterpretations']['stationPower']['analysis']} 可用于把站级调度结果和过程侧来流代理放在同一视图下复核。

{station_composition_markdown}

{turbine_heatmap_markdown}

{profile_markdown}

## 结论

{conclusion_axis_line}
{conclusion_duration_line}
{asset_issue_markdown}
- {flow_direction_statement}
- 建议优先复核零流量或恒定流量节点的合理性，以及 `{analysis['top_flow_variation']['object_name']}` 的局部变化原因。
- 若下一步要做动态评估，建议增加事件注入或更细粒度输出。

## 后续建议动作

{chr(10).join(f"{index}. {item}" for index, item in enumerate(payload['recommendations'], start=1))}
"""
    (report_dir / "simulation_report.md").write_text(markdown, encoding="utf-8")


def write_html_assets(report_dir: Path, data_dir: Path, payload: dict[str, Any]) -> None:
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2).replace("</", "<\\/")
    report_js = "window.HYDROS_REPORT_DATA = " + payload_json + ";\n"
    (data_dir / "report.data.js").write_text(report_js, encoding="utf-8")
    template_html = TEMPLATE_HTML.read_text(encoding="utf-8")
    inline_data_tag = f"    <script>\n{report_js}    </script>\n\n"
    if "async function loadReportData()" not in template_html:
        raise RuntimeError("未找到 HTML 报告模板的数据加载入口")
    report_html = template_html.replace("    <script>\n      async function loadReportData()", inline_data_tag + "    <script>\n      async function loadReportData()", 1)
    (report_dir / "simulation_report.html").write_text(report_html, encoding="utf-8")
    (data_dir / "analysis_summary.json").write_text(
        json.dumps(payload["analysisSummary"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def validate_required_report_assets(
    charts_dir: Path,
    profile_dataset: Any,
    require_turbine_output: bool = False,
) -> dict[str, Any]:
    required_chart_names = [
        "chart1_water_level.png",
        "chart2_water_flow.png",
        "chart4_gate_opening.png",
        "chart5_disturbance_flow.png",
        "chart7_longitudinal_profile.png",
        "chart8_level_flow_coupling.png",
    ]
    if require_turbine_output:
        required_chart_names.append("chart6_turbine_output_power.png")
        required_chart_names.append("chart9_station_inflow_power_comparison.png")
        required_chart_names.append("chart10_station_output_composition.png")
        required_chart_names.append("chart11_turbine_dispatch_heatmap.png")
    missing = [name for name in required_chart_names if not (charts_dir / name).exists()]
    if profile_dataset is None and "chart7_longitudinal_profile.png" not in missing:
        missing.append("chart7_longitudinal_profile.png")
    return {
        "required": required_chart_names,
        "missing": sorted(set(missing)),
        "complete": not missing,
    }


def main() -> None:
    args = parse_args(sys.argv[1:])
    llm_name = resolve_llm_name(args.llm_name)

    csv_path = Path(args.timeseries_file).resolve()
    df = load_dataframe(csv_path)
    output_dir = resolve_task_output_dir(csv_path, df, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = prepare_output_dirs(output_dir)

    working_csv_path = paths["data"] / csv_path.name
    if working_csv_path != csv_path:
        shutil.copy2(csv_path, working_csv_path)

    scenario_meta = fetch_scenario_metadata(args.scenario_yaml_url) if args.scenario_yaml_url else None
    if args.scenario_yaml_url and scenario_meta is None:
        print(f"场景 YAML 读取失败，已跳过场景元数据: {args.scenario_yaml_url}")
    runtime_config = resolve_runtime_config(
        sorted(int(step) for step in df["data_index"].unique().tolist()),
        scenario_meta,
        args,
    )
    coupling_summary = summarize_level_flow_coupling(df)
    scenario_id = str(df["biz_scenario_id"].iloc[0])
    if scenario_id in TURBINE_OUTPUT_REQUIRED_SCENARIOS and select_turbine_output_rows(df).empty and args.mpc_results_json:
        df, appended_turbine_rows = augment_dataframe_with_mpc_turbine_output(
            df,
            args.mpc_results_json,
            runtime_config.output_step_size,
        )
        if appended_turbine_rows:
            df.to_csv(working_csv_path, index=False, encoding="utf-8")
            print(f"已通过 MPC 结果补齐水轮机出力记录: {appended_turbine_rows} 条")

    resolved_objects_yaml_url = args.objects_yaml_url or (scenario_meta or {}).get("objects_yaml_url")
    objects_yaml_path = None
    location_map = {}
    objects_yaml_text = None
    try:
        objects_yaml_path = cache_objects_yaml(paths["data"], resolved_objects_yaml_url)
        if objects_yaml_path and objects_yaml_path.exists():
            from build_longitudinal_profile import parse_object_locations
            objects_yaml_text = objects_yaml_path.read_text(encoding="utf-8")
            location_map = parse_object_locations(objects_yaml_text)
    except Exception as exc:
        print(f"objects.yaml 预取失败或解析 location 失败: {exc}")

    chart_command = [sys.executable, str(CHART_SCRIPT), str(working_csv_path), str(paths["charts"])]
    if runtime_config.total_steps is not None:
        chart_command.extend(["--total-steps", str(runtime_config.total_steps)])
    if runtime_config.sim_step_size is not None:
        chart_command.extend(["--sim-step-size", str(runtime_config.sim_step_size)])
    if runtime_config.output_step_size is not None:
        chart_command.extend(["--output-step-size", str(runtime_config.output_step_size)])
    if args.mpc_results_json:
        chart_command.extend(["--mpc-results-json", str(args.mpc_results_json)])
    if objects_yaml_path and objects_yaml_path.exists():
        chart_command.extend(["--objects-yaml", str(objects_yaml_path)])
    run_command(chart_command)

    profile_dataset = None
    profile_error = None
    try:
        if objects_yaml_path is not None or resolved_objects_yaml_url:
            profile_dataset = build_longitudinal_dataset(
                working_csv_path,
                objects_yaml_path=objects_yaml_path,
                objects_yaml_url=resolved_objects_yaml_url,
            )
            save_profile_png(profile_dataset, paths["charts"] / "chart7_longitudinal_profile.png")
        else:
            profile_error = "未提供 objects.yaml 来源，已跳过纵剖面生成"
            print(f"纵剖面未生成: {profile_error}")
    except Exception as exc:
        profile_error = str(exc)
        print(f"纵剖面未生成: {profile_error}")

    asset_status = validate_required_report_assets(
        paths["charts"],
        profile_dataset,
        require_turbine_output=scenario_id in TURBINE_OUTPUT_REQUIRED_SCENARIOS,
    )

    charts_stats = paths["charts"] / "analysis_stats.json"
    if charts_stats.exists():
        shutil.move(str(charts_stats), str(paths["data"] / "analysis_stats.json"))

    payload = build_report_data(
        df,
        working_csv_path,
        runtime_config,
        scenario_meta,
        args.scenario_yaml_url,
        llm_name,
        profile_dataset,
        asset_status,
        profile_error,
        location_map,
        objects_yaml_text,
        args.mpc_results_json,
        args.scenario_events_json,
    )
    write_html_assets(paths["report"], paths["data"], payload)
    write_markdown_report(paths["report"], payload)

    print(f"HTML 报告: {paths['report'] / 'simulation_report.html'}")
    print(f"Markdown 报告: {paths['report'] / 'simulation_report.md'}")
    print(f"图表目录: {paths['charts']}")
    print(f"数据目录: {paths['data']}")


def build_turbine_dispatch_heatmap_payload(
    df: pd.DataFrame,
    business_children: list[dict[str, Any]] | None = None,
    excluded_steps: set[int] | None = None,
) -> dict[str, Any]:
    turbine_series = build_business_turbine_series(df, business_children, excluded_steps)
    if not turbine_series:
        return {"available": False, "steps": [], "turbines": [], "series": [], "totalData": [], "stationGroups": []}

    step_values = sorted(
        {
            int(point[0])
            for item in turbine_series
            for point in item.get("data", [])
            if len(point) >= 2 and point[0] is not None
        }
    )
    ranked_series = sorted(
        turbine_series,
        key=lambda item: (
            str(item.get("businessObjectName") or ""),
            -float(
                sum(float(point[1]) for point in item.get("data", []) if len(point) >= 2)
                / max(len(item.get("data", [])), 1)
            ),
            str(item.get("displayName") or item.get("name") or ""),
        ),
    )
    step_index_map = {step: index for index, step in enumerate(step_values)}
    chart_series: list[dict[str, Any]] = []
    turbine_names: list[str] = []
    total_data = [0.0 for _ in step_values]
    grouped_names: dict[str, list[str]] = {}

    for row_index, series in enumerate(ranked_series):
        turbine_name = str(series.get("displayName") or series.get("name") or f"机组{row_index + 1}").strip()
        raw_station_name = str(series.get("businessObjectName") or "").strip()
        inferred_station_name = infer_station_name_from_turbine(turbine_name)
        station_name = raw_station_name if raw_station_name and raw_station_name != turbine_name else (inferred_station_name or "未归属电站")
        turbine_names.append(turbine_name)
        grouped_names.setdefault(station_name, []).append(turbine_name)
        values = [0.0 for _ in step_values]
        for point in series.get("data", []):
            if len(point) < 2:
                continue
            step = int(point[0])
            if step not in step_index_map:
                continue
            index = step_index_map[step]
            values[index] = float(point[1])
            total_data[index] += float(point[1])
        chart_series.append(
            {
                "name": turbine_name,
                "stationName": station_name,
                "data": [round_number(value, 3) for value in values],
            }
        )

    station_groups = [
        {"stationName": station_name, "turbines": names}
        for station_name, names in grouped_names.items()
    ]
    return {
        "available": bool(chart_series),
        "steps": step_values,
        "turbines": turbine_names,
        "series": chart_series,
        "totalData": [round_number(value, 3) for value in total_data],
        "stationGroups": station_groups,
    }


if __name__ == "__main__":
    main()
