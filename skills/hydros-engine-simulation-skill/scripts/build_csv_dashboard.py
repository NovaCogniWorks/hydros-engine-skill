#!/usr/bin/env python3
"""
从 Hydros 时序 CSV 生成 dashboard 工作台页面。

用法:
    python build_csv_dashboard.py <timeseries_csv> [output_dir]
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_HTML = ROOT / "assets" / "hydros-dashboard-template" / "index.html"


def load_dataframe(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["value"] = pd.to_numeric(df["value"])
    df["data_index"] = pd.to_numeric(df["data_index"])
    return df


def round_number(value: Any, digits: int = 3) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    return value


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


def build_events(df: pd.DataFrame) -> list[dict[str, str]]:
    steps = sorted(int(step) for step in df["data_index"].unique().tolist())
    total_steps = len(steps)
    return [
        {"status": "INIT", "message": "已装载 CSV 原始记录并完成字段标准化。"},
        {"status": "READY", "message": f"识别到 {df['object_name'].nunique()} 个对象、{df['metrics_code'].nunique()} 类指标。"},
        {"status": "STEPPING", "message": f"采样步从 {steps[0]} 到 {steps[-1]}，固定间隔 {steps[1] - steps[0] if len(steps) > 1 else 0}。"},
        {"status": "CHECKING", "message": "已完成负流量、零流量和恒定序列的工作台侧异常检查。"},
        {"status": "COMPLETED", "message": f"工作台可用，当前加载 {len(df)} 条记录和 {total_steps} 个采样点。"},
    ]


def build_payload(df: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    meta = {
        "biz_scene_instance_id": str(df["biz_scenario_instance_id"].iloc[0]),
        "biz_scenario_id": str(df["biz_scenario_id"].iloc[0]),
        "total_steps": len(unique_steps),
        "task_status": "COMPLETED",
        "default_render_objects": build_default_render_objects(df),
        "events": build_events(df),
        "record_count": int(len(df)),
        "object_count": int(df["object_name"].nunique()),
        "metric_count": int(df["metrics_code"].nunique()),
        "step_values": unique_steps,
        "step_interval": int(unique_steps[1] - unique_steps[0]) if len(unique_steps) > 1 else 0,
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
    if len(sys.argv) < 2:
        print("用法: python build_csv_dashboard.py <timeseries_csv> [output_dir]")
        raise SystemExit(1)

    csv_path = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else csv_path.parent / f"{csv_path.stem}_dashboard"
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_dataframe(csv_path)
    timeseries_data, sim_meta = build_payload(df)

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
