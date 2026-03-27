#!/usr/bin/env python3
"""
基于京石段 objects.yaml 与仿真 CSV 生成纵剖面 HTML 页面。
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None


OBJECTS_URL = "http://47.97.1.45:9000/hydros/mdm/%E4%BA%AC%E7%9F%B3%E6%AE%B5/hydro_modeling/objects.yaml"

if plt is not None:
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti TC", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False


def fetch_objects_yaml() -> str:
    with urllib.request.urlopen(OBJECTS_URL, timeout=20) as response:
        return response.read().decode("utf-8")


def gate_sort_key(name: str) -> tuple[str, int, str]:
    station = name.split("-", 1)[0]
    match = re.search(r"#(\d+)", name)
    gate_number = int(match.group(1)) if match else 999
    return (station, gate_number, name)


def parse_cross_sections(text: str) -> list[dict]:
    sections = []
    block_pattern = re.compile(
        r"\n -\n  id:\s*(?P<id>\d+)\n  type:\s*CrossSection\n  name:\s*(?P<name>.+?)\n(?P<body>.*?)(?=\n -\n  id:|\Z)",
        re.S,
    )
    for match in block_pattern.finditer("\n" + text):
        body = match.group("body")
        top_match = re.search(r"\n    (?:t_top_elevation|top_elevation):\s*(?P<value>[-\d.]+)", body)
        bottom_match = re.search(r"\n    bottom_elevation:\s*(?P<value>[-\d.]+)", body)
        location_match = re.search(r"\n    location:\s*(?P<value>[-\d.]+)", body)
        point_elevations = [
            float(value)
            for value in re.findall(r"\n\s*-\s*\n\s*-\s*[-\d.]+\n\s*-\s*([-\d.]+)", body)
        ]
        if not location_match:
            continue
        if not bottom_match and not point_elevations:
            continue

        bottom_elevation = float(bottom_match.group("value")) if bottom_match else min(point_elevations)
        if top_match:
            top_elevation = float(top_match.group("value"))
        elif point_elevations:
            top_elevation = max(point_elevations)
        else:
            top_elevation = bottom_elevation
        sections.append(
            {
                "id": int(match.group("id")),
                "name": match.group("name").strip(),
                "bottom_elevation": bottom_elevation,
                "top_elevation": top_elevation,
                "location": float(location_match.group("value")),
            }
        )
    sections.sort(key=lambda item: item["location"])
    return sections


def build_dataset(csv_path: Path) -> dict:
    yaml_text = fetch_objects_yaml()
    sections = parse_cross_sections(yaml_text)

    df = pd.read_csv(csv_path)
    df["value"] = pd.to_numeric(df["value"])
    df["data_index"] = pd.to_numeric(df["data_index"])
    last_step = int(df["data_index"].max())
    gate_df = df[(df["object_type"] == "Gate") & (df["metrics_code"] == "gate_opening")].copy()

    last_water = df[(df["metrics_code"] == "water_level") & (df["data_index"] == last_step)][
        ["object_name", "value"]
    ].copy()
    water_map = {row.object_name: round(float(row.value), 3) for row in last_water.itertuples(index=False)}

    profile_points = []
    for item in sections:
        water = water_map.get(item["name"])
        profile_points.append(
            {
                "name": item["name"],
                "location": round(item["location"] / 1000, 3),
                "bottom_elevation": round(item["bottom_elevation"], 3),
                "top_elevation": round(item["top_elevation"], 3),
                "water_level": water,
                "depth": round(water - item["bottom_elevation"], 3) if water is not None else None,
            }
        )

    matched = [item for item in profile_points if item["water_level"] is not None]
    min_bed = min(item["bottom_elevation"] for item in profile_points)
    max_water = max(item["water_level"] for item in matched)
    max_top = max(item["top_elevation"] for item in profile_points)
    start = matched[0]
    end = matched[-1]

    gate_markers = [
        {
            "name": "ZM1-入口断面",
            "short_name": "ZM1",
            "location": next(item["location"] for item in profile_points if item["name"] == "ZM1-入口断面"),
        },
        {
            "name": "ZM2-入口断面",
            "short_name": "ZM2",
            "location": next(item["location"] for item in profile_points if item["name"] == "ZM2-入口断面"),
        },
    ]
    default_gate_names = {
        "ZM1": ["ZM1-节制闸#1", "ZM1-节制闸#2"],
        "ZM2": ["ZM2-节制闸#1", "ZM2-节制闸#2"],
    }
    gate_names_by_station: dict[str, list[str]] = {}
    for gate_name in sorted(gate_df["object_name"].dropna().unique().tolist(), key=gate_sort_key):
        gate_names_by_station.setdefault(gate_name.split("-", 1)[0], []).append(gate_name)

    gate_stations = [
        {
            "short_name": "ZM1",
            "name": "ZM1-北易水倒虹吸出口节制闸",
            "location": gate_markers[0]["location"],
            "inlet_section": "ZM1-入口断面",
            "gates": gate_names_by_station.get("ZM1", default_gate_names["ZM1"]),
            "role": "北易水倒虹吸出口控制点",
        },
        {
            "short_name": "ZM2",
            "name": "ZM2-坟庄河倒虹吸出口节制闸",
            "location": gate_markers[1]["location"],
            "inlet_section": "ZM2-入口断面",
            "gates": gate_names_by_station.get("ZM2", default_gate_names["ZM2"]),
            "role": "坟庄河倒虹吸出口控制点",
        },
    ]

    return {
        "meta": {
            "csv_path": str(csv_path),
            "last_step": last_step,
            "section_count": len(profile_points),
            "matched_water_level_sections": len(matched),
            "distance_km": round(profile_points[-1]["location"] - profile_points[0]["location"], 3),
            "water_drop_m": round(start["water_level"] - end["water_level"], 3),
            "min_bed_elevation": round(min_bed, 3),
            "max_water_level": round(max_water, 3),
            "max_top_elevation": round(max_top, 3),
            "flow_direction": "左侧上游（QD-1） → 右侧下游（QD-14）",
            "gate_station_count": len(gate_stations),
        },
        "profile_points": profile_points,
        "gate_markers": gate_markers,
        "gate_stations": gate_stations,
        "highlights": {
            "start": start,
            "end": end,
            "deepest": max(matched, key=lambda item: item["depth"]),
            "shallowest": min(matched, key=lambda item: item["depth"]),
        },
    }


def save_profile_png(dataset: dict, output_png: Path) -> None:
    if plt is None:
        raise RuntimeError("matplotlib 未安装，无法输出纵剖面 PNG")

    matched = [item for item in dataset["profile_points"] if item["water_level"] is not None]
    x_data = [item["location"] for item in matched]
    bed_data = [item["bottom_elevation"] for item in matched]
    top_data = [item["top_elevation"] for item in matched]
    water_data = [item["water_level"] for item in matched]

    y_top = max(max(water_data), max(top_data)) + 0.38
    y_bottom = min(bed_data) - 0.35
    x_left = min(x_data)
    x_right = max(x_data)
    x_padding = max((x_right - x_left) * 0.02, 0.15)

    fig, ax = plt.subplots(figsize=(14, 6.4))
    ax.fill_between(x_data, y_bottom, bed_data, color="#87603d", alpha=0.16)
    ax.plot(x_data, top_data, color="#637487", linewidth=2, linestyle="--", label="断面顶高程")
    ax.plot(x_data, bed_data, color="#87603d", linewidth=2.4, label="断面底高程")
    ax.plot(x_data, water_data, color="#1c7fb5", linewidth=3, label="末步水位")
    ax.fill_between(x_data, bed_data, water_data, color="#1c7fb5", alpha=0.12)

    for station in dataset["gate_stations"]:
        ax.axvline(station["location"], color="#a5456f", linestyle="--", linewidth=1.4, alpha=0.82)
        ax.text(
            station["location"],
            y_top - 0.02,
            station["short_name"],
            ha="center",
            va="bottom",
            fontsize=10,
            color="#874564",
            fontweight="bold",
        )

    ax.annotate(
        "",
        xy=(x_data[-1], y_top - 0.18),
        xytext=(x_data[0], y_top - 0.18),
        arrowprops={"arrowstyle": "->", "color": "#1c7fb5", "lw": 2},
    )
    ax.text(x_data[0], y_top - 0.12, "上游", fontsize=10, color="#1c7fb5", ha="left")
    ax.text(x_data[-1], y_top - 0.12, "下游", fontsize=10, color="#1c7fb5", ha="right")

    ax.set_title("京石段渠道纵剖面", fontsize=15, fontweight="bold", pad=10)
    ax.set_xlabel("里程 (km)", fontsize=12, labelpad=8)
    ax.set_ylabel("高程 / 水位 (m)", fontsize=12, labelpad=10)
    ax.xaxis.set_label_coords(0.5, -0.065)
    ax.yaxis.set_label_coords(-0.08, 0.5)
    ax.set_xlim(x_left - x_padding, x_right + x_padding)
    ax.set_ylim(y_bottom, y_top + 0.06)
    ax.grid(True, alpha=0.24)
    ax.legend(loc="upper right")
    fig.subplots_adjust(left=0.1, right=0.985, top=0.9, bottom=0.14)
    fig.savefig(output_png, dpi=180)
    plt.close(fig)
    print(f"纵剖面图: {output_png}")


def build_html(dataset: dict) -> str:
    data_json = json.dumps(dataset, ensure_ascii=False)
    html = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>京石段纵剖面</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
    <style>
      :root {{
        --bg: #eff4f6;
        --ink: #11293b;
        --muted: #5b7385;
        --line: #d5e1e8;
        --panel: rgba(255,255,255,0.84);
        --shadow: 0 24px 48px rgba(17, 41, 59, 0.08);
        --water: #1c7fb5;
        --bed: #87603d;
        --depth: rgba(28,127,181,0.16);
        --gate: #a5456f;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: Inter, "PingFang SC", Arial, sans-serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(28,127,181,0.12), transparent 30%),
          radial-gradient(circle at top right, rgba(135,96,61,0.08), transparent 26%),
          var(--bg);
      }}
      .shell {{ max-width: 1500px; margin: 0 auto; padding: 24px; }}
      .hero, .panel {{
        background: var(--panel);
        border: 1px solid rgba(255,255,255,0.92);
        border-radius: 30px;
        box-shadow: var(--shadow);
        backdrop-filter: blur(14px);
      }}
      .hero {{ padding: 28px 30px; }}
      .eyebrow {{
        margin: 0;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.28em;
        text-transform: uppercase;
        color: rgba(28,127,181,0.72);
      }}
      h1 {{ margin: 10px 0 12px; font-size: clamp(32px, 4vw, 50px); line-height: 1.04; }}
      .hero p:last-child {{ margin: 0; color: var(--muted); line-height: 1.8; max-width: 940px; }}
      .meta {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 14px;
        margin-top: 22px;
      }}
      .card {{
        padding: 16px 18px;
        border-radius: 22px;
        background: rgba(255,255,255,0.84);
        border: 1px solid var(--line);
      }}
      .card small {{
        display: block;
        font-size: 12px;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.16em;
      }}
      .card strong {{ display: block; margin-top: 8px; font-size: 26px; }}
      .layout {{
        display: grid;
        grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.7fr);
        gap: 18px;
        margin-top: 18px;
      }}
      .panel {{ padding: 22px; }}
      .panel h2 {{ margin: 0 0 8px; font-size: 24px; }}
      .subtle {{ margin: 0; color: var(--muted); line-height: 1.7; }}
      #chart {{ margin-top: 18px; height: 620px; }}
      .legend {{
        display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px;
      }}
      .legend span {{
        display: inline-flex; align-items: center; gap: 8px;
        padding: 8px 12px; border-radius: 999px;
        background: rgba(255,255,255,0.78); border: 1px solid var(--line); font-size: 13px;
      }}
      .flow-direction {{
        display: flex;
        align-items: center;
        gap: 12px;
        margin-top: 14px;
        padding: 12px 16px;
        border-radius: 18px;
        background: rgba(28,127,181,0.08);
        border: 1px solid rgba(28,127,181,0.18);
        color: var(--ink);
        font-size: 14px;
        font-weight: 600;
      }}
      .flow-direction .arrow-line {{
        position: relative;
        flex: 1;
        height: 3px;
        border-radius: 999px;
        background: linear-gradient(90deg, rgba(28,127,181,0.35), rgba(28,127,181,0.92));
      }}
      .flow-direction .arrow-line::after {{
        content: "";
        position: absolute;
        right: -1px;
        top: -5px;
        border-top: 6px solid transparent;
        border-bottom: 6px solid transparent;
        border-left: 11px solid rgba(28,127,181,0.92);
      }}
      .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
      .note {{
        margin-top: 16px; padding: 16px 18px; border-radius: 22px;
        background: rgba(255,255,255,0.78); border: 1px solid var(--line); line-height: 1.75; color: var(--muted);
      }}
      .detail {{
        border-radius: 28px;
        background: linear-gradient(180deg, rgba(17,41,59,0.98), rgba(24,48,72,0.94));
        padding: 22px;
        color: white;
      }}
      .detail h3 {{ margin: 8px 0 6px; font-size: 28px; }}
      .detail p {{ margin: 0; line-height: 1.8; color: rgba(255,255,255,0.8); }}
      .detail-grid {{
        display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 18px;
      }}
      .detail-grid div {{
        padding: 14px 16px; border-radius: 20px; background: rgba(255,255,255,0.08);
      }}
      .detail-grid small {{
        display: block; text-transform: uppercase; letter-spacing: 0.14em; color: rgba(255,255,255,0.58); font-size: 11px;
      }}
      .detail-grid strong {{ display: block; margin-top: 6px; font-size: 18px; }}
      .station-list {{
        display: grid;
        gap: 12px;
        margin-top: 18px;
      }}
      .station-card {{
        padding: 16px 18px;
        border-radius: 22px;
        background: rgba(255,255,255,0.82);
        border: 1px solid var(--line);
      }}
      .station-card h4 {{
        margin: 0 0 6px;
        font-size: 18px;
      }}
      .station-card p {{
        margin: 0;
        color: var(--muted);
        line-height: 1.7;
      }}
      .station-card .station-meta {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 10px;
      }}
      .station-card .station-meta span {{
        padding: 6px 10px;
        border-radius: 999px;
        font-size: 12px;
        background: rgba(165,69,111,0.08);
        color: #874564;
      }}
      .table-wrap {{
        margin-top: 18px;
        border-radius: 24px;
        overflow: hidden;
        border: 1px solid var(--line);
        background: rgba(255,255,255,0.82);
      }}
      table {{ width: 100%; border-collapse: collapse; }}
      th, td {{ padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }}
      th {{ font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); background: rgba(239,244,246,0.9); }}
      tr:last-child td {{ border-bottom: 0; }}
      @media (max-width: 1100px) {{ .layout {{ grid-template-columns: 1fr; }} }}
    </style>
  </head>
  <body>
    <div class="shell">
      <section class="hero">
        <p class="eyebrow">Hydros Longitudinal Profile</p>
        <h1>京石段纵剖面图</h1>
        <p>
          纵剖面基于 `objects.yaml` 中的断面里程与底高程构建，并叠加 `timeseries_data_100001_v5.csv`
          在末步 `data_index=__LAST_STEP__` 的水位线。这样可以同时观察沿程床面变化和当前工况下的水面线走势。
        </p>
        <div class="meta">
          <div class="card"><small>总里程</small><strong>__DISTANCE_KM__ km</strong></div>
          <div class="card"><small>匹配水位断面</small><strong>__MATCHED_COUNT__</strong></div>
          <div class="card"><small>沿程水位降幅</small><strong>__WATER_DROP__ m</strong></div>
          <div class="card"><small>最低底高程</small><strong>__MIN_BED__ m</strong></div>
          <div class="card"><small>闸站数量</small><strong>__GATE_STATION_COUNT__</strong></div>
          <div class="card"><small>水流流向</small><strong style="font-size: 18px;">__FLOW_DIRECTION__</strong></div>
        </div>
      </section>

      <div class="layout">
        <section class="panel">
          <h2>床面线与水面线</h2>
          <p class="subtle">
            灰色虚线表示断面顶高程，棕色线表示断面底高程，蓝色线表示末步水位。蓝色阴影仅填充到床面线，
            棕色阴影填充到坐标轴底部，用来同时表达过水断面和床面起伏。
            图中额外用竖线标出 `ZM1` 和 `ZM2` 两个闸站入口位置。
          </p>
          <div class="legend">
            <span><i class="dot" style="background: #637487"></i>断面顶高程</span>
            <span><i class="dot" style="background: var(--bed)"></i>断面底高程</span>
            <span><i class="dot" style="background: var(--water)"></i>末步水位</span>
            <span><i class="dot" style="background: var(--gate)"></i>闸站位置</span>
          </div>
          <div class="flow-direction">
            <span>上游</span>
            <div class="arrow-line"></div>
            <span>下游</span>
          </div>
          <div id="chart"></div>
          <div class="note">
            这张图主要服务工程理解：如果水面线整体高于床面线且沿程平滑下降，通常说明渠道主流方向正常、工况稳定。
            如果某段水面突然抬升或贴近床面，就值得重点复核该段的边界条件、闸门动作或局部阻力变化。
          </div>
        </section>

        <aside class="panel">
          <div class="detail">
            <p class="eyebrow" style="color: rgba(255,255,255,0.58);">Profile Highlights</p>
            <h3>关键观察</h3>
            <p>
              末步水面线从 <strong>__START_NAME__</strong> 的
              <strong>__START_WATER__ m</strong> 下降到
              <strong>__END_NAME__</strong> 的
              <strong>__END_WATER__ m</strong>，
              说明主干渠整体维持稳定下泄。
            </p>
            <div class="detail-grid">
              <div>
                <small>最大水深</small>
                <strong>__DEEPEST_NAME__</strong>
              </div>
              <div>
                <small>水深</small>
                <strong>__DEEPEST_DEPTH__ m</strong>
              </div>
              <div>
                <small>最小水深</small>
                <strong>__SHALLOWEST_NAME__</strong>
              </div>
              <div>
                <small>水深</small>
                <strong>__SHALLOWEST_DEPTH__ m</strong>
              </div>
            </div>
          </div>

          <div class="station-list" id="stationList"></div>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>断面</th>
                  <th>里程 (km)</th>
                  <th>顶高程 (m)</th>
                  <th>底高程 (m)</th>
                  <th>水位 (m)</th>
                  <th>水深 (m)</th>
                </tr>
              </thead>
              <tbody id="tableBody"></tbody>
            </table>
          </div>
        </aside>
      </div>
    </div>

    <script>
      const dataset = __DATA_JSON__;
      const chart = echarts.init(document.getElementById("chart"));

      const points = dataset.profile_points;
      const matched = points.filter((item) => item.water_level !== null);
      const xData = matched.map((item) => item.location);
      const bedData = matched.map((item) => item.bottom_elevation);
      const topData = matched.map((item) => item.top_elevation);
      const waterData = matched.map((item) => item.water_level);
      const rawMin = Math.min(...bedData);
      const rawMax = Math.max(...topData, ...waterData);
      const yMin = Number((rawMin - 0.35).toFixed(3));
      const yMax = Number((rawMax + 0.38).toFixed(3));
      const xMin = Math.min(...xData);
      const xMax = Math.max(...xData);
      const xPadding = Math.max((xMax - xMin) * 0.02, 0.15);
      const bedFillSegments = matched.slice(0, -1).map((item, index) => [
        item.location,
        matched[index + 1].location,
        yMin,
        item.bottom_elevation,
        matched[index + 1].bottom_elevation
      ]);
      const waterFillSegments = matched.slice(0, -1).map((item, index) => [
        item.location,
        matched[index + 1].location,
        item.bottom_elevation,
        matched[index + 1].bottom_elevation,
        item.water_level,
        matched[index + 1].water_level
      ]);

      const gateLines = dataset.gate_markers.map((item) => ({
        xAxis: item.location,
        name: item.short_name,
        label: {{ formatter: item.short_name, color: '#a5456f' }},
        lineStyle: {{ color: '#a5456f', type: 'dashed', width: 1.5 }}
      }));

      chart.setOption({
        animationDuration: 500,
        grid: {{ left: 92, right: 44, top: 92, bottom: 82, containLabel: true }},
        tooltip: {{
          trigger: 'axis',
          axisPointer: {{ type: 'cross' }},
          formatter: (params) => {{
            const idx = params[0]?.dataIndex ?? 0;
            const row = matched[idx];
            return [
              `<strong>${{row.name}}</strong>`,
              `里程: ${{row.location}} km`,
              `顶高程: ${{row.top_elevation}} m`,
              `底高程: ${{row.bottom_elevation}} m`,
              `水位: ${{row.water_level}} m`,
              `水深: ${{row.depth}} m`
            ].join('<br>');
          }}
        }},
        legend: {{
          top: 12,
          left: 96,
          right: 40,
          data: ['断面顶高程', '断面底高程', '末步水位', '闸站位置'],
          textStyle: {{ color: '#5b7385' }}
        }},
        xAxis: {{
          type: 'value',
          name: '里程 (km)',
          min: Number((xMin - xPadding).toFixed(3)),
          max: Number((xMax + xPadding).toFixed(3)),
          nameLocation: 'middle',
          nameGap: 44,
          axisLabel: {{ color: '#5b7385', margin: 14 }},
          splitLine: {{ lineStyle: {{ color: '#e3ebef' }} }}
        }},
        yAxis: {{
          type: 'value',
          name: '高程 / 水位 (m)',
          min: yMin,
          max: yMax,
          nameLocation: 'middle',
          nameRotate: 90,
          nameGap: 72,
          axisLabel: {{ color: '#5b7385', margin: 14 }},
          splitLine: {{ lineStyle: {{ color: '#e3ebef' }} }}
        }},
        series: [
          {{
            type: 'custom',
            silent: true,
            tooltip: {{ show: false }},
            z: 1,
            renderItem: (params, api) => {{
              const polygon = [
                api.coord([api.value(0), api.value(2)]),
                api.coord([api.value(0), api.value(3)]),
                api.coord([api.value(1), api.value(4)]),
                api.coord([api.value(1), api.value(2)])
              ];
              return {{
                type: 'polygon',
                shape: {{ points: polygon }},
                style: {{ fill: 'rgba(135,96,61,0.16)', stroke: 'none' }}
              }};
            }},
            data: bedFillSegments
          }},
          {{
            type: 'custom',
            silent: true,
            tooltip: {{ show: false }},
            z: 2,
            renderItem: (params, api) => {{
              const polygon = [
                api.coord([api.value(0), api.value(2)]),
                api.coord([api.value(0), api.value(4)]),
                api.coord([api.value(1), api.value(5)]),
                api.coord([api.value(1), api.value(3)])
              ];
              return {{
                type: 'polygon',
                shape: {{ points: polygon }},
                style: {{ fill: 'rgba(28,127,181,0.12)', stroke: 'none' }}
              }};
            }},
            data: waterFillSegments
          }},
          {{
            name: '断面顶高程',
            type: 'line',
            smooth: false,
            showSymbol: true,
            symbolSize: 5,
            lineStyle: {{ color: '#637487', width: 2, type: 'dashed' }},
            itemStyle: {{ color: '#637487' }},
            z: 4,
            data: matched.map((item) => [item.location, item.top_elevation])
          }},
          {{
            name: '断面底高程',
            type: 'line',
            smooth: false,
            showSymbol: true,
            symbolSize: 6,
            lineStyle: {{ color: '#87603d', width: 2.2 }},
            itemStyle: {{ color: '#87603d' }},
            z: 5,
            data: matched.map((item) => [item.location, item.bottom_elevation])
          }},
          {{
            name: '末步水位',
            type: 'line',
            smooth: true,
            showSymbol: true,
            symbolSize: 7,
            lineStyle: {{ color: '#1c7fb5', width: 3 }},
            itemStyle: {{ color: '#1c7fb5' }},
            z: 6,
            data: matched.map((item) => [item.location, item.water_level])
          }},
          {{
            name: '闸站位置',
            type: 'line',
            silent: true,
            showSymbol: false,
            lineStyle: {{ opacity: 0 }},
            itemStyle: {{ opacity: 0 }},
            tooltip: {{ show: false }},
            markLine: {{
              symbol: 'none',
              silent: true,
              data: gateLines
            }},
            data: []
          }}
        ]
      });

      const stationList = document.getElementById('stationList');
      stationList.innerHTML = dataset.gate_stations.map((item) => `
        <div class="station-card">
          <h4>${item.short_name} · ${item.name}</h4>
          <p>${item.role}</p>
          <div class="station-meta">
            <span>里程 ${item.location} km</span>
            <span>入口断面 ${item.inlet_section}</span>
            ${item.gates.map((gate) => `<span>${gate}</span>`).join('')}
          </div>
        </div>
      `).join('');

      const tableBody = document.getElementById('tableBody');
      tableBody.innerHTML = matched.map((item) => `
        <tr>
          <td>${item.name}</td>
          <td>${item.location}</td>
          <td>${item.top_elevation}</td>
          <td>${item.bottom_elevation}</td>
          <td>${item.water_level}</td>
          <td>${item.depth}</td>
        </tr>
      `).join('');

      window.addEventListener('resize', () => chart.resize());
    </script>
  </body>
</html>
"""
    html = html.replace("{{", "{").replace("}}", "}")
    return (
        html.replace("__DATA_JSON__", data_json)
        .replace("__LAST_STEP__", str(dataset["meta"]["last_step"]))
        .replace("__DISTANCE_KM__", str(dataset["meta"]["distance_km"]))
        .replace("__MATCHED_COUNT__", str(dataset["meta"]["matched_water_level_sections"]))
        .replace("__WATER_DROP__", str(dataset["meta"]["water_drop_m"]))
        .replace("__MIN_BED__", str(dataset["meta"]["min_bed_elevation"]))
        .replace("__GATE_STATION_COUNT__", str(dataset["meta"]["gate_station_count"]))
        .replace("__FLOW_DIRECTION__", str(dataset["meta"]["flow_direction"]))
        .replace("__START_NAME__", str(dataset["highlights"]["start"]["name"]))
        .replace("__START_WATER__", str(dataset["highlights"]["start"]["water_level"]))
        .replace("__END_NAME__", str(dataset["highlights"]["end"]["name"]))
        .replace("__END_WATER__", str(dataset["highlights"]["end"]["water_level"]))
        .replace("__DEEPEST_NAME__", str(dataset["highlights"]["deepest"]["name"]))
        .replace("__DEEPEST_DEPTH__", str(dataset["highlights"]["deepest"]["depth"]))
        .replace("__SHALLOWEST_NAME__", str(dataset["highlights"]["shallowest"]["name"]))
        .replace("__SHALLOWEST_DEPTH__", str(dataset["highlights"]["shallowest"]["depth"]))
    )


def main() -> None:
    if len(sys.argv) < 2:
      print("用法: python build_longitudinal_profile.py <timeseries_csv> [output_html]")
      raise SystemExit(1)

    csv_path = Path(sys.argv[1]).resolve()
    output_html = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else csv_path.parent / "waterway50_longitudinal_profile.html"

    dataset = build_dataset(csv_path)
    output_html.write_text(build_html(dataset), encoding="utf-8")
    print(f"纵剖面页面: {output_html}")


if __name__ == "__main__":
    main()
