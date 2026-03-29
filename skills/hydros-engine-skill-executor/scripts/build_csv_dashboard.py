#!/usr/bin/env python3
"""
从 Hydros 时序 CSV 生成 dashboard 工作台页面。

用法:
    python build_csv_dashboard.py <timeseries_csv> [output_dir]
        [--total-steps N] [--sim-step-size SECONDS] [--output-step-size SECONDS]
"""

from __future__ import annotations

import json
import sys
import argparse
import math
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_HTML = ROOT / "assets" / "hydros-dashboard-template" / "index.html"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 Hydros 时序 CSV 生成 dashboard 工作台页面")
    parser.add_argument("timeseries_csv")
    parser.add_argument("output_dir", nargs="?")
    parser.add_argument("--total-steps", type=int, default=None)
    parser.add_argument("--sim-step-size", type=int, default=None, help="计算步长，单位秒")
    parser.add_argument("--output-step-size", type=int, default=None, help="输出步长，单位秒")
    return parser.parse_args(argv)


def load_dataframe(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["value"] = pd.to_numeric(df["value"])
    df["data_index"] = pd.to_numeric(df["data_index"])
    return df


def round_number(value: Any, digits: int = 3) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    return value


def format_seconds_text(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if secs or not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)


def build_default_render_objects(df: pd.DataFrame) -> list[str]:
    picks: list[str] = []

    qd_objects = sorted(
        {
            name
            for name in df["object_name"].unique().tolist()
            if str(name).startswith("QD-") and "#001" in str(name)
        },
        key=lambda item: int(item.split("-")[1].split("#")[0]),
    )
    if qd_objects:
        picks.extend([qd_objects[0], qd_objects[min(len(qd_objects) - 1, 6)], qd_objects[-1]])

    for name in ["FSK2-北易水退水闸", "QD-14#断面#002", "ZM1-节制闸#1", "ZM2-节制闸#2"]:
        if name in df["object_name"].values and name not in picks:
            picks.append(name)

    return picks[:8]


def build_events(df: pd.DataFrame, axis_note: str) -> list[dict[str, str]]:
    steps = sorted(int(step) for step in df["data_index"].unique().tolist())
    total_steps = len(steps)
    return [
        {"status": "INIT", "message": "已装载 CSV 原始记录并完成字段标准化。"},
        {"status": "READY", "message": f"识别到 {df['object_name'].nunique()} 个对象、{df['metrics_code'].nunique()} 类指标。"},
        {"status": "STEPPING", "message": axis_note},
        {"status": "CHECKING", "message": "已完成负流量、零流量和恒定序列的工作台侧异常检查。"},
        {"status": "COMPLETED", "message": f"工作台可用，当前加载 {len(df)} 条记录和 {total_steps} 个采样点。"},
    ]


def build_payload(df: pd.DataFrame, args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "object_name": str(row["object_name"]),
                "object_type": str(row["object_type"]),
                "metrics_code": str(row["metrics_code"]),
                "data_index": int(row["data_index"]),
                "value": round_number(float(row["value"])),
                "tenant_id": str(row["tenant_id"]),
                "waterway_id": str(row["waterway_id"]),
            }
        )

    unique_steps = sorted(int(step) for step in df["data_index"].unique().tolist())
    axis_label = "CSV 采样序号"
    axis_note = "图表横轴按 CSV 采样序号展示。"
    expected_sample_count = None
    simulation_duration_seconds = None
    sampled_duration_seconds = None
    duration_gap_seconds = None
    csv_issue = None

    if args.total_steps is not None and args.sim_step_size is not None and args.output_step_size is not None:
        simulation_duration_seconds = args.total_steps * args.sim_step_size
        expected_sample_count = math.floor(args.total_steps / (args.output_step_size / args.sim_step_size)) + 1
        sampled_duration_seconds = max(len(unique_steps) - 1, 0) * args.output_step_size
        duration_gap_seconds = max(simulation_duration_seconds - sampled_duration_seconds, 0)
        axis_note = (
            f"时间轴优先按用户参数 total_steps={args.total_steps}, sim_step_size={args.sim_step_size}, "
            f"output_step_size={args.output_step_size} 解释；CSV 横轴仍保留采样序号。"
        )
        if expected_sample_count != len(unique_steps):
            csv_issue = (
                f"按参数应约有 {expected_sample_count} 个输出点、覆盖 {format_seconds_text(simulation_duration_seconds)}；"
                f"但 CSV 实际只有 {len(unique_steps)} 个采样点，最多覆盖 {format_seconds_text(sampled_duration_seconds)}。"
            )
            axis_note = f"{axis_note}{csv_issue}"
    meta = {
        "biz_scene_instance_id": str(df["biz_scenario_instance_id"].iloc[0]),
        "biz_scenario_id": str(df["biz_scenario_id"].iloc[0]),
        "total_steps": args.total_steps if args.total_steps is not None else len(unique_steps),
        "sampled_point_count": len(unique_steps),
        "sim_step_size": args.sim_step_size,
        "output_step_size": args.output_step_size,
        "task_status": "COMPLETED",
        "default_render_objects": build_default_render_objects(df),
        "events": build_events(df, axis_note),
        "record_count": int(len(df)),
        "object_count": int(df["object_name"].nunique()),
        "metric_count": int(df["metrics_code"].nunique()),
        "step_values": unique_steps,
        "step_interval": int(unique_steps[1] - unique_steps[0]) if len(unique_steps) > 1 else 0,
        "axis_label": axis_label,
        "time_axis_note": axis_note,
        "expected_sample_count": expected_sample_count,
        "simulation_duration": format_seconds_text(simulation_duration_seconds),
        "sampled_duration": format_seconds_text(sampled_duration_seconds),
        "duration_gap": format_seconds_text(duration_gap_seconds),
        "csv_issue": csv_issue,
    }
    return rows, meta


def build_dashboard_html() -> str:
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    inject = '    <script src="./dashboard.data.js"></script>\n'
    marker = "    <script>\n      const sampleData = ["
    if marker not in html:
        raise RuntimeError("未找到 dashboard 模板注入点")
    return html.replace(marker, inject + marker, 1)


def main() -> None:
    args = parse_args(sys.argv[1:])

    csv_path = Path(args.timeseries_csv).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else csv_path.parent / f"{csv_path.stem}_dashboard"
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_dataframe(csv_path)
    timeseries_data, sim_meta = build_payload(df, args)

    dashboard_js = (
        "window.HYDROS_TIMESERIES_DATA = "
        + json.dumps(timeseries_data, ensure_ascii=False, indent=2)
        + ";\nwindow.HYDROS_SIM_META = "
        + json.dumps(sim_meta, ensure_ascii=False, indent=2)
        + ";\n"
    )
    (output_dir / "dashboard.data.js").write_text(dashboard_js, encoding="utf-8")
    (output_dir / "dashboard_summary.json").write_text(
        json.dumps(sim_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(build_dashboard_html(), encoding="utf-8")

    print(f"Dashboard HTML: {output_dir / 'index.html'}")
    print(f"Dashboard 数据: {output_dir / 'dashboard.data.js'}")


if __name__ == "__main__":
    main()
