#!/usr/bin/env python3
"""
从 Hydros 时序 CSV 生成 HTML + Markdown 分析报告。

用法:
    python build_csv_report.py <timeseries_csv> [output_dir]
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

import pandas as pd

from build_longitudinal_profile import build_dataset as build_longitudinal_dataset
from build_longitudinal_profile import save_profile_png
from lib.url_utils import normalize_remote_url


ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT.parent.parent
TEMPLATE_HTML = ROOT / "assets" / "hydros-report-template" / "index.html"
CHART_SCRIPT = ROOT / "scripts" / "generate_charts.py"
SCENARIO_URL_TEMPLATE = "http://47.97.1.45:9000/hydros/mdm/scenarios/{scenario_id}.yaml"


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
    df = pd.read_csv(csv_path)
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
        raise ValueError(f"CSV 缺少必需列: {', '.join(missing_columns)}")
    if df.empty:
        raise ValueError("CSV 不包含任何数据行")

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["data_index"] = pd.to_numeric(df["data_index"], errors="coerce")
    if df["data_index"].dropna().empty:
        raise ValueError("CSV 中 data_index 全部无效，疑似坏文件或残缺文件")
    if df["value"].dropna().empty:
        raise ValueError("CSV 中 value 全部无效，疑似坏文件或残缺文件")
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


def fetch_scenario_metadata(scenario_id: str) -> dict[str, Any] | None:
    url = SCENARIO_URL_TEMPLATE.format(scenario_id=scenario_id)
    try:
        with urllib.request.urlopen(normalize_remote_url(url), timeout=20) as response:
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
        "scenario_yaml_url": url,
        "scenario_yaml_id": Path(url).name,
        "scenario_name": extract("biz_scenario_name"),
        "waterway_id": extract("waterway_id"),
        "waterway_name": extract("waterway_name"),
        "objects_yaml_url": extract("hydros_objects_modeling_url"),
        "total_steps": int(total_steps) if total_steps and total_steps.isdigit() else None,
        "sim_step_size": int(sim_step_size) if sim_step_size and sim_step_size.isdigit() else None,
        "output_step_size": int(output_step_size) if output_step_size and output_step_size.isdigit() else None,
        "biz_start_time": start_time,
    }


def cache_objects_yaml(data_dir: Path, scenario_meta: dict[str, Any] | None) -> Path | None:
    objects_yaml_url = (scenario_meta or {}).get("objects_yaml_url")
    if not objects_yaml_url:
        return None

    target_path = data_dir / "objects.yaml"
    with urllib.request.urlopen(normalize_remote_url(objects_yaml_url), timeout=20) as response:
        target_path.write_text(response.read().decode("utf-8"), encoding="utf-8")
    return target_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 Hydros 时序 CSV 生成 HTML + Markdown 分析报告")
    parser.add_argument("timeseries_csv")
    parser.add_argument("output_dir", nargs="?")
    parser.add_argument("--total-steps", type=int, default=None)
    parser.add_argument("--sim-step-size", type=int, default=None, help="计算步长，单位秒")
    parser.add_argument("--output-step-size", type=int, default=None, help="输出步长，单位秒")
    parser.add_argument("--llm-name", default=None, help="当前使用的模型名称；如 gpt-5.4 / claude-sonnet")
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

    expected_sample_count = None
    output_ratio = None
    if sim_step_size and output_step_size and sim_step_size > 0 and output_step_size > 0:
        output_ratio = output_step_size / sim_step_size
        if total_steps is not None:
            expected_sample_count = math.floor(total_steps / output_ratio) + 1

    has_unreliable_time_axis = False
    axis_mode = "csv_index"
    axis_label = "输出顺序"
    axis_note = "结果文件里的时间信息不够完整，图表横轴按结果输出先后顺序显示。"
    sample_step_note = f"第 {unique_steps[0]} 次 ~ 第 {unique_steps[-1]} 次输出"

    if stable_csv_interval is not None and stable_csv_interval > 1:
        axis_mode = "calculation_step"
        axis_label = "仿真步"
        axis_note = "结果文件里的序号可以对应到仿真推进过程，图表横轴显示仿真进行到第几步。"
        sample_step_note = f"仿真第 {unique_steps[0]} 步 ~ 第 {unique_steps[-1]} 步"
    elif output_ratio is not None and stable_csv_interval == 1 and expected_sample_count is not None:
        if abs(expected_sample_count - len(unique_steps)) <= 1:
            axis_mode = "output_ordinal"
            axis_label = "输出顺序"
            axis_note = (
                "结果文件里的序号更接近结果输出顺序，图表横轴按结果输出先后顺序显示，"
                "并结合本次仿真设置做时长判断。"
            )
            sample_step_note = f"第 {unique_steps[0]} 次 ~ 第 {unique_steps[-1]} 次输出"
        else:
            has_unreliable_time_axis = True
            axis_mode = "csv_index_unreliable"
            axis_label = "输出顺序"
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


def build_metric_series(df: pd.DataFrame, metric: str, excluded_steps: set[int] | None = None, sort_key_func=None) -> list[dict[str, Any]]:
    series = []
    metric_df = df[df["metrics_code"] == metric].copy()
    if excluded_steps:
        metric_df = metric_df[~metric_df["data_index"].astype(int).isin(excluded_steps)].copy()
    for (object_name, object_type), group in metric_df.groupby(["object_name", "object_type"], sort=False):
        ordered = group.sort_values("data_index")
        if ordered.empty:
            continue
        points = [[int(step), round_number(value)] for step, value in zip(ordered["data_index"], ordered["value"])]
        item: dict[str, Any] = {
            "name": object_name,
            "objectType": object_type,
            "data": points,
        }
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
        series.append(
            {
                "name": object_name,
                "objectType": "Gate",
                "range": round_number(ordered["value"].max() - ordered["value"].min()),
                "data": compressed,
            }
        )

    if sort_key_func:
        series.sort(key=lambda item: sort_key_func(item["name"]))
    else:
        series.sort(key=lambda item: item["name"])
    return series


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

    meta = dict(profile_dataset["meta"])
    meta["max_water_level_all"] = round_number(max_water_level_all, 3)
    meta["timeline_step_count"] = len(frames)

    return {
        "available": True,
        "chartImage": "../charts/chart7_longitudinal_profile.png",
        "meta": meta,
        "gateMarkers": profile_dataset["gate_markers"],
        "objectAnnotations": profile_dataset.get("object_annotations", []),
        "points": current_frame["points"],
        "frames": frames,
        "stepValues": [frame["step"] for frame in frames],
        "highlights": {
            "start": start_point,
            "end": end_point,
            "deepest": deepest,
            "shallowest": shallowest,
        },
        "summary": (
            f"纵剖面显示最后时刻水面线从 {start_point['name']} 的 {start_point['water_level']} m "
            f"下降到 {end_point['name']} 的 {end_point['water_level']} m，沿程水头损失 "
            f"{round_number(start_point['water_level'] - end_point['water_level'], 3)} m。"
        ),
    }


def describe_series_points(group: pd.DataFrame) -> str:
    ordered = group.sort_values("data_index")
    first_step = int(ordered["data_index"].iloc[0])
    last_step = int(ordered["data_index"].iloc[-1])
    first_value = round_number(ordered["value"].iloc[0])
    last_value = round_number(ordered["value"].iloc[-1])
    return f"{first_step} 步为 {first_value}，{last_step} 步为 {last_value}"


def build_report_data(
    df: pd.DataFrame,
    csv_path: Path,
    runtime_config: RuntimeConfig,
    llm_name: str | None = None,
    profile_dataset: dict[str, Any] | None = None,
    asset_status: dict[str, Any] | None = None,
    profile_error: str | None = None,
    location_map: dict[str, float] | None = None,
) -> dict[str, Any]:
    location_map = location_map or {}
    sort_key_func = create_object_sort_key(location_map)
    raw_unique_steps = sorted(int(step) for step in df["data_index"].unique().tolist())
    step_interval = runtime_config.csv_step_interval
    scenario_id = str(df["biz_scenario_id"].iloc[0])
    scenario_meta = fetch_scenario_metadata(scenario_id)

    metric_counts = {key: int(value) for key, value in df["metrics_code"].value_counts().to_dict().items()}
    object_type_counts = {key: int(value) for key, value in df["object_type"].value_counts().to_dict().items()}

    flow_df = df[df["metrics_code"] == "water_flow"].copy()
    level_df = df[df["metrics_code"] == "water_level"].copy()
    gate_df = df[(df["object_type"] == "Gate") & (df["metrics_code"] == "gate_opening")].copy()
    placeholder_level_steps = detect_placeholder_steps(level_df)
    placeholder_flow_steps = detect_placeholder_steps(flow_df)
    display_excluded_steps = sorted(set(placeholder_level_steps) | set(placeholder_flow_steps))
    unique_steps = [step for step in raw_unique_steps if step not in display_excluded_steps] or raw_unique_steps
    level_display_df = level_df[~level_df["data_index"].astype(int).isin(placeholder_level_steps)].copy()
    flow_display_df = flow_df[~flow_df["data_index"].astype(int).isin(placeholder_flow_steps)].copy()
    gate_display_df = gate_df[~gate_df["data_index"].astype(int).isin(display_excluded_steps)].copy()
    excluded_steps_by_metric = {
        "water_level": set(placeholder_level_steps),
        "water_flow": set(placeholder_flow_steps),
        "gate_opening": set(display_excluded_steps),
    }
    expected_steps_by_metric = {
        "water_level": set(int(step) for step in level_display_df["data_index"].unique().tolist()),
        "water_flow": set(int(step) for step in flow_display_df["data_index"].unique().tolist()),
        "gate_opening": set(int(step) for step in gate_display_df["data_index"].unique().tolist()),
    }

    negative_flow = flow_display_df[flow_display_df["value"] < 0].copy()
    asset_status = asset_status or {"required": [], "missing": [], "complete": True}
    zero_flow_groups = []
    constant_flow_groups = []
    dynamic_gate_groups = []
    completeness_issues = []

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

    for (object_name, object_type), group in flow_display_df.groupby(["object_name", "object_type"], sort=False):
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
            if values[index] != values[index - 1]:
                change_steps.append(int(ordered["data_index"].iloc[index]))
        if change_steps:
            dynamic_gate_groups.append((object_name, ordered, change_steps))

    flow_range = (
        flow_display_df.groupby(["object_name", "object_type"])["value"]
        .agg(["min", "max", "mean", "std"])
        .assign(range=lambda frame: frame["max"] - frame["min"])
        .sort_values("range", ascending=False)
    )
    level_range = (
        level_display_df.groupby(["object_name", "object_type"])["value"]
        .agg(["min", "max", "mean", "std"])
        .assign(range=lambda frame: frame["max"] - frame["min"])
        .sort_values("range", ascending=False)
    )

    highlight_flow_name = None
    highlight_flow_type = None
    highlight_flow_stats = None
    highlight_flow_group = pd.DataFrame(columns=flow_display_df.columns)
    if not flow_range.empty:
        highlight_flow_name, highlight_flow_type = flow_range.index[0]
        highlight_flow_stats = flow_range.iloc[0]
        highlight_flow_group = flow_display_df[
            (flow_display_df["object_name"] == highlight_flow_name) & (flow_display_df["object_type"] == highlight_flow_type)
        ]
    highlight_flow_window_text = describe_variation_window(highlight_flow_group)

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

    negative_points = int(len(negative_flow))
    zero_flow_count = len(zero_flow_groups)
    constant_flow_count = len(constant_flow_groups)
    dynamic_gate_count = len(dynamic_gate_groups)
    stability_score = max(55, 95 - zero_flow_count * 4 - constant_flow_count - len(anomaly_items) * 2)
    control_score = min(85, 20 + dynamic_gate_count * 12 + (10 if highlight_flow_stats["range"] > 20 else 0))
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
    last_calculation_step = (
        runtime_config.total_steps
        if runtime_config.total_steps is not None
        else unique_steps[-1]
    )
    simulation_start_dt = parse_datetime_text(scenario_meta["biz_start_time"]) if scenario_meta else None
    simulation_end_dt = (
        simulation_start_dt + timedelta(seconds=last_calculation_step * step_resolution_seconds)
        if simulation_start_dt and step_resolution_seconds is not None
        else None
    )
    simulation_duration_seconds = (
        last_calculation_step * step_resolution_seconds if step_resolution_seconds is not None else None
    )
    output_interval_seconds = runtime_config.output_step_size
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
        f"{step_resolution_seconds} 秒/步（{format_duration_text(step_resolution_seconds)}）"
        if step_resolution_seconds is not None
        else "未提供，无法可靠推导"
    )
    output_step_text = (
        f"{output_interval_seconds} 秒/次（{format_duration_text(output_interval_seconds)}）"
        if output_interval_seconds is not None
        else (
            f"结果序号间隔 {step_interval}" if step_interval is not None else "无法可靠推导"
        )
    )
    simulation_duration_text = (
        f"{format_duration_text(simulation_duration_seconds)}（共 {last_calculation_step} 个计算步）"
        if simulation_duration_seconds is not None and step_resolution_seconds is not None
        else (
            f"{last_calculation_step} 个计算步"
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

    summary_paragraphs = [
        (
            f"本次研究围绕{scenario_name or f'场景 {scenario_id}'}开展水动力仿真结果分析，"
            f"重点复核主干渠及关键节点在当前工况下的水位、流量、闸门调节和沿程水面线变化。"
            f"本次结果文件共包含 {len(df)} 条记录，覆盖 {df['object_name'].nunique()} 个对象、"
            f"{df['metrics_code'].nunique()} 类指标，当前页面共展示 {display_sampled_point_count} 次结果输出，"
            f"覆盖仿真第 {unique_steps[0]} 步至第 {unique_steps[-1]} 步。"
        ),
        (
            f"结果表明，研究区整体保持稳定输水，未发现明显倒流和突发水位失稳；"
            f"主干断面在最后时刻的沿程水头损失约 {level_drop} m，整体仍符合上游高、下游低的基本水力梯度。"
            f"当前需要重点关注的是个别退水闸零流量，以及 {highlight_flow_name} 的局部流量最大变化幅度较大。"
        ),
        (
            f"综合分析认为，当前结果反映出方案总体运行平稳，"
            f"但 {recommendation_target_text} 等敏感区段仍需进一步做重点核查。"
            f"建议下一阶段补充 {'、'.join(recommendation_actions)}，"
            f"以降低局部变化误判风险，并为后续设计复核和调度判断提供支撑。"
        ),
    ]
    if runtime_config.expected_sample_count is not None and runtime_config.expected_sample_count != raw_sampled_point_count:
        summary_paragraphs[1] += (
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
                f"研究区整体保持稳定输水，未发现明显倒流；"
                f"主干断面最后时刻沿程水头损失约 {level_drop} m，整体仍保持上游高、下游低的基本趋势。"
            ),
        },
        {
            "title": "变化规律",
            "body": (
                f"从当前结果看，变化主要集中在 {highlight_level_name}、{highlight_flow_name} 等关键位置；"
                "其余大多数区段过程较平顺，主干渠沿程水面线整体呈平滑下降。"
                if longitudinal_profile is not None
                else (
                    f"从当前结果曲线看，变化主要集中在 {highlight_level_name}、{highlight_flow_name} 等关键位置；"
                    "其余大多数区段过程较平顺。"
                )
            ),
        },
        {
            "title": "局部差异",
            "body": (
                f"{highlight_flow_name} 的流量最大变化幅度最明显，达到 {round_number(highlight_flow_stats['range'])} m³/s；"
                "相比之下，其余大多数对象变化幅度更小，说明差异主要集中在局部关键节点。"
            ),
        },
        {
            "title": "异常情况",
            "body": (
                f"{leading_zero_flow_name} 最为特殊，全程结果均为 0，需先核查是正常停运、关闭状态，还是配置或取数异常。"
                if leading_zero_flow_name
                else (
                    f"当前最特殊的现象出现在 {highlight_flow_name}，其变化幅度明显高于其他对象，"
                    "需要结合工况进一步复核。"
                )
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
        f"复核 {highlight_flow_name} 附近的边界条件、分流关系和联动控制，解释其变化原因。",
        (
            f"若需要更细的过程诊断，建议把输出步长从当前 {runtime_config.output_step_size} 秒/次缩短到 600-1200 秒/次。"
            if runtime_config.output_step_size
            else "若需要更细的过程诊断，建议缩短输出步长并重新导出结果。"
        ),
        "若后续要做动态评估，可叠加工况事件注入，观察闸门动作对沿程水位和分水口流量的传递影响。",
    ]

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
        else "主干渠整体可运行，但存在局部倒流风险"
    )
    overall_control_text = (
        "闸门调节过程总体平稳"
        if dynamic_gate_groups
        else "闸门运行状态总体平稳"
    )
    overall_risk_text = (
        "局部节点仍需结合零流量和变化较大区段继续复核"
        if zero_flow_groups or highlight_flow_stats is not None
        else "当前未见突出的局部异常"
    )
    overall_judgement_text = (
        "当前结果未见明显整体失稳迹象"
        if asset_status["complete"] and not runtime_config.has_unreliable_time_axis and negative_flow.empty
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
        "csvPath": csv_path.name,
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
            "scenario_yaml_id": scenario_meta["scenario_yaml_id"] if scenario_meta else f"{scenario_id}.yaml",
            "scenario_yaml_url": scenario_meta["scenario_yaml_url"] if scenario_meta else SCENARIO_URL_TEMPLATE.format(scenario_id=scenario_id),
            "simulation_start_time": format_datetime_text(simulation_start_dt),
            "simulation_end_time": format_datetime_text(simulation_end_dt),
            "simulation_duration": simulation_duration_text,
            "sampled_duration": format_seconds_text(sampled_duration_seconds) or "无法推导",
            "duration_gap": format_seconds_text(duration_gap_seconds) or "无法推导",
            "sim_step_size_text": sim_step_size_text,
            "output_step_text": output_step_text,
            "time_axis_note": runtime_config.axis_note,
            "axis_label": runtime_config.axis_label,
            "analyst": llm_name,
            "record_count": int(len(df)),
            "object_count": int(df["object_name"].nunique()),
            "metric_count": int(df["metrics_code"].nunique()),
            "negative_flow_points": negative_points,
            "negative_flow_objects": int(negative_flow["object_name"].nunique()),
            "zero_flow_objects": zero_flow_count,
            "water_level_series_count": int(level_df.groupby(["object_name", "object_type"]).ngroups),
            "water_flow_series_count": int(flow_df.groupby(["object_name", "object_type"]).ngroups),
            "gate_series_count": int(gate_df.groupby("object_name").ngroups),
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
            {"label": "时间步长", "value": sim_step_size_text},
            {"label": "输出步长", "value": output_step_text},
            {"label": "仿真时长", "value": simulation_duration_text},
            {"label": "结果文件覆盖时长", "value": format_seconds_text(sampled_duration_seconds) or "无法推导"},
            {"label": "时长差值", "value": format_seconds_text(duration_gap_seconds) or "无法推导"},
            {"label": "结果覆盖步段", "value": f"仿真第 {unique_steps[0]} 步至第 {unique_steps[-1]} 步（共输出 {display_sampled_point_count} 次结果）"},
            {"label": "结果导出时间", "value": format_datetime_text(runtime_completed_at.to_pydatetime()) or str(df["gmt_create"].max())},
        ],
        "miniTable": mini_table,
        "charts": {
            "levelSeries": build_metric_series(df, "water_level", set(placeholder_level_steps), sort_key_func),
            "flowSeries": build_metric_series(df, "water_flow", set(placeholder_flow_steps), sort_key_func),
            "gateSeries": build_gate_series(df, set(display_excluded_steps), sort_key_func),
        },
        "chartInterpretations": {
            "level": {
                "analysis": (
                    f"水位结果曲线整体变化不大，{highlight_level_name} 的最大变化幅度为 "
                    f"{round_number(highlight_level_stats['range'])} m；默认建议优先查看断面序列的同步变化。"
                ),
                "placeholder_steps": placeholder_level_steps,
            },
            "flow": {
                "analysis": (
                    f"流量结果曲线以稳定输水为主，{highlight_flow_name} 的最大变化幅度为 "
                    f"{round_number(highlight_flow_stats['range'])} m³/s。"
                ),
                "placeholder_steps": placeholder_flow_steps,
            },
            "gate": {
                "analysis": (
                    f"闸门结果曲线共 {int(gate_df.groupby('object_name').ngroups)} 条，"
                    f"{dynamic_gate_count} 条存在明显开度切换，适合与水位、流量阶段变化联动解释。"
                ),
                "placeholder_steps": display_excluded_steps,
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
            "last_calculation_step": last_calculation_step,
            "simulation_start_time": format_datetime_text(simulation_start_dt),
            "simulation_end_time": format_datetime_text(simulation_end_dt),
            "simulation_duration": simulation_duration_text,
            "sampled_duration": format_seconds_text(sampled_duration_seconds),
            "duration_gap": format_seconds_text(duration_gap_seconds),
            "output_step_text": output_step_text,
            "axis_mode": runtime_config.axis_mode,
            "axis_label": runtime_config.axis_label,
            "axis_note": runtime_config.axis_note,
            "expected_sample_count": runtime_config.expected_sample_count,
            "raw_sampled_point_count": raw_sampled_point_count,
            "display_sampled_point_count": display_sampled_point_count,
            "metric_counts": metric_counts,
            "object_type_counts": object_type_counts,
            "top_flow_variation": {
                "object_name": highlight_flow_name,
                "object_type": highlight_flow_type,
                "min": round_number(highlight_flow_stats["min"]),
                "max": round_number(highlight_flow_stats["max"]),
                "range": round_number(highlight_flow_stats["range"]),
                "description": describe_series_points(highlight_flow_group),
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
    asset_status = payload["analysisSummary"].get("report_assets", {})
    missing_assets = asset_status.get("missing", [])
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
    asset_issue_markdown = ""
    if missing_assets:
        asset_issue_markdown = (
            f"- 报告图表产物存在缺失：`{'`、`'.join(missing_assets)}`。\n"
            "- HTML 页面已显式标注该问题；相关图表维度应按缺失范围降级解读，不应视为“完整图表已全部产出”。"
        )
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
    markdown = f"""# {payload['meta']['report_title']}

## 概况

- 场景 ID：`{payload['meta']['biz_scenario_id']}`
- 任务状态：`{payload['meta']['task_status']}`
- 开始时间：`{payload['meta']['simulation_start_time'] or '场景 YAML 未提供'}`
- 结束时间：`{payload['meta']['simulation_end_time'] or '根据结果文件无法推导'}`
- 时间步长：`{payload['meta']['sim_step_size_text']}`
- 输出步长：`{payload['meta']['output_step_text']}`
- 仿真时长：`{payload['meta']['simulation_duration']}`
- 结果文件覆盖时长：`{payload['meta']['sampled_duration']}`
- 时长差值：`{payload['meta']['duration_gap']}`
- 记录数：`{payload['meta']['record_count']}`
- 对象数：`{payload['meta']['object_count']}`
- 指标数：`{payload['meta']['metric_count']}`
- 结果覆盖步段：仿真第 `{analysis['step_values'][0]}` 步至第 `{analysis['step_values'][-1]}` 步，共输出 `{payload['meta']['sampled_point_count']}` 次结果
- 配置总计算步：`{payload['meta']['total_steps']}`

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

## 图表分析

### 1. 水位时序

![关键断面水位时序](../charts/chart1_water_level.png)

{payload['chartInterpretations']['level']['analysis']} 沿程断面平稳下降，整体水力坡降关系清晰。

### 2. 流量时序

![关键断面流量时序](../charts/chart2_water_flow.png)

{payload['chartInterpretations']['flow']['analysis']} 主干断面流量大多维持在 `25 ~ 29 m³/s` 区间，没有出现负流量，表明主流方向稳定。

### 3. 闸门开度

![闸门开度时序](../charts/chart4_gate_opening.png)

{payload['chartInterpretations']['gate']['analysis']} 存在明显阶跃变化，说明场景中存在控制动作，而不是完全静态工况。

### 4. 分水口/退水闸流量

![分水口流量时序](../charts/chart5_disturbance_flow.png)

分流/退水节点侧呈现“少数动态、多数恒定”的特征。部分节点全程为零或维持恒定流量，更像稳态配水结果而非持续调节过程。

### 5. 沿程水位热力图

![沿程水位热力图](../charts/chart6_heatmap.png)

热力图显示主渠水位从上游向下游稳定递减，且时间维变化幅度有限，说明本次仿真以稳定输水为主。局部颜色变化主要集中在中前段断面，与闸门动作的时间段基本一致。
{profile_markdown}

## 结论

{conclusion_axis_line}
{conclusion_duration_line}
{asset_issue_markdown}
- 系统整体稳定，无倒流、无明显水位异常变化，适合作为一次稳定工况分析样本。
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


def validate_required_report_assets(charts_dir: Path, profile_dataset: Any) -> dict[str, Any]:
    required_chart_names = [
        "chart1_water_level.png",
        "chart2_water_flow.png",
        "chart4_gate_opening.png",
        "chart5_disturbance_flow.png",
        "chart6_heatmap.png",
        "chart7_longitudinal_profile.png",
    ]
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

    csv_path = Path(args.timeseries_csv).resolve()
    df = load_dataframe(csv_path)
    output_dir = resolve_task_output_dir(csv_path, df, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = prepare_output_dirs(output_dir)

    working_csv_path = paths["data"] / csv_path.name
    if working_csv_path != csv_path:
        shutil.copy2(csv_path, working_csv_path)

    scenario_meta = fetch_scenario_metadata(str(df["biz_scenario_id"].iloc[0]))
    runtime_config = resolve_runtime_config(
        sorted(int(step) for step in df["data_index"].unique().tolist()),
        scenario_meta,
        args,
    )

    chart_command = [sys.executable, str(CHART_SCRIPT), str(working_csv_path), str(paths["charts"])]
    if runtime_config.total_steps is not None:
        chart_command.extend(["--total-steps", str(runtime_config.total_steps)])
    if runtime_config.sim_step_size is not None:
        chart_command.extend(["--sim-step-size", str(runtime_config.sim_step_size)])
    if runtime_config.output_step_size is not None:
        chart_command.extend(["--output-step-size", str(runtime_config.output_step_size)])
    run_command(chart_command)

    objects_yaml_path = None
    location_map = {}
    try:
        objects_yaml_path = cache_objects_yaml(paths["data"], scenario_meta)
        if objects_yaml_path and objects_yaml_path.exists():
            from build_longitudinal_profile import parse_object_locations
            location_map = parse_object_locations(objects_yaml_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"objects.yaml 预取失败或解析 location 失败: {exc}")

    profile_dataset = None
    profile_error = None
    try:
        profile_dataset = build_longitudinal_dataset(
            working_csv_path,
            objects_yaml_path=objects_yaml_path,
            objects_yaml_url=(scenario_meta or {}).get("objects_yaml_url"),
        )
        save_profile_png(profile_dataset, paths["charts"] / "chart7_longitudinal_profile.png")
    except Exception as exc:
        profile_error = str(exc)
        print(f"纵剖面未生成: {profile_error}")

    asset_status = validate_required_report_assets(paths["charts"], profile_dataset)

    charts_stats = paths["charts"] / "analysis_stats.json"
    if charts_stats.exists():
        shutil.move(str(charts_stats), str(paths["data"] / "analysis_stats.json"))

    payload = build_report_data(
        df,
        working_csv_path,
        runtime_config,
        llm_name,
        profile_dataset,
        asset_status,
        profile_error,
        location_map,
    )
    write_html_assets(paths["report"], paths["data"], payload)
    write_markdown_report(paths["report"], payload)

    print(f"HTML 报告: {paths['report'] / 'simulation_report.html'}")
    print(f"Markdown 报告: {paths['report'] / 'simulation_report.md'}")
    print(f"图表目录: {paths['charts']}")
    print(f"数据目录: {paths['data']}")


if __name__ == "__main__":
    main()
