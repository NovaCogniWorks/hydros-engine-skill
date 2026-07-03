#!/usr/bin/env python3
"""
水力仿真结果图表生成脚本

用法:
    python generate_charts.py <timeseries_data.json> [output_dir]
        [--total-steps N] [--sim-step-size SECONDS] [--output-step-size SECONDS]

生成 6 张分析图表:
  1. 关键断面水位时序图
  2. 关键断面流量时序图
  3. 负流量专项分析图
  4. 闸门开度时序图
  5. 分水口流量分析图
  6. 水轮机出力时序图
"""

import json
import csv
import sys
import os
import argparse
from collections import defaultdict, Counter
from pathlib import Path

from lib.timeseries_loader import load_timeseries_records

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    print("ERROR: matplotlib 和 numpy 未安装，请运行:")
    print("  pip3 install matplotlib numpy")
    sys.exit(1)

# 中文字体配置
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC', 'Heiti TC', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False


def parse_args(argv):
    parser = argparse.ArgumentParser(description="水力仿真结果图表生成脚本")
    parser.add_argument("data_path")
    parser.add_argument("output_dir", nargs="?")
    parser.add_argument("--total-steps", type=int, default=None)
    parser.add_argument("--sim-step-size", type=int, default=None, help="计算步长，单位秒")
    parser.add_argument("--output-step-size", type=int, default=None, help="输出步长，单位秒")
    parser.add_argument("--mpc-results-json", default=None, help="get_mpc_simulation_results JSON response")
    parser.add_argument("--objects-yaml", default=None, help="objects.yaml path used to derive station inflow proxy")
    return parser.parse_args(argv)


def resolve_axis_info(records, total_steps=None, sim_step_size=None, output_step_size=None):
    indices = sorted(set(r['data_index'] for r in records))
    intervals = sorted(set(b - a for a, b in zip(indices, indices[1:])))
    stable_interval = intervals[0] if len(intervals) == 1 else None
    if total_steps:
        expected_sample_count = total_steps + 1 if indices and min(indices) == 0 else total_steps
    else:
        expected_sample_count = None
    duration_note = (
        f'按参数推导的仿真覆盖总时长为 {total_steps * output_step_size} 秒'
        if total_steps is not None and output_step_size is not None
        else None
    )

    label = '结果输出序号'
    note = '结果文件时间信息不足，图表横轴按结果输出顺序展示。'
    if stable_interval is not None and stable_interval > 1:
        label = '计算步'
        note = '结果文件中的 data_index 已表现为计算步号，图表横轴按计算步展示。'
    elif expected_sample_count is not None and stable_interval == 1:
        if abs(expected_sample_count - len(indices)) <= 1:
            label = '输出序号'
            note = '结果文件中的 data_index 更像输出序号，图表横轴按输出序号展示。'
            if duration_note:
                note += duration_note + '；sim_step_size 仅表示内部计算步长。'
        else:
            note = (
                f'结果文件目前仅有 {len(indices)} 个采样点，但按参数应约有 {expected_sample_count} 个输出点；'
                '图表横轴仅保留结果输出顺序。'
            )
    return {
        'label': label,
        'note': note,
        'indices': indices,
    }


def load_data(filepath):
    """加载并解析时序数据 JSON、CSV 或 XLSX"""
    records = load_timeseries_records(filepath)
    suffix = os.path.splitext(filepath)[1].lower()
    if suffix == '.json':
        print(f"从 JSON 加载了 {len(records)} 条记录")
    elif suffix == '.csv':
        print(f"从结果文件 CSV 加载了 {len(records)} 条记录")
    else:
        print(f"从结果文件 {suffix.upper().lstrip('.')} 加载了 {len(records)} 条记录")
    return records


def group_data(records):
    """按 (object_name, metrics_code, object_type) 分组"""
    groups = defaultdict(list)
    for r in records:
        key = (r['object_name'], r['metrics_code'], r['object_type'])
        groups[key].append((r['data_index'], r['value']))
    for k in groups:
        groups[k].sort(key=lambda x: x[0])
    return groups


def is_turbine_output_record(record):
    has_device_command = (
        str(record.get('device_type') or '') == 'Turbine'
        and str(record.get('command_type') or '') == 'output_power'
    )
    has_object_metric = (
        str(record.get('object_type') or '') == 'Turbine'
        and str(record.get('metrics_code') or '') == 'output_power'
    )
    return has_device_command or has_object_metric


def group_turbine_output(records):
    """按水轮机对象名称分组出力序列"""
    groups = defaultdict(list)
    for record in records:
        if not is_turbine_output_record(record):
            continue
        object_name = record.get('object_name') or record.get('device_name') or record.get('name') or '未命名水轮机'
        groups[str(object_name)].append((record['data_index'], record['value']))
    for key in groups:
        groups[key].sort(key=lambda item: item[0])
    return groups


def load_mpc_payload(mpc_results_json):
    if not mpc_results_json:
        return None
    with open(mpc_results_json, "r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if "result" in payload and isinstance(payload["result"], dict):
        content = payload["result"].get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text")
            if text:
                return json.loads(text)
    if "data" in payload:
        return payload
    return None


def infer_station_name_from_turbine_name(turbine_name):
    text = str(turbine_name or '')
    mapping = {
        '瀑布沟': '瀑布沟站(6机+3闸)',
        '深溪沟': '深溪沟站(4机+3闸)',
        '枕头坝': '枕头坝站(4机+5闸)',
        '沙坪': '沙坪站(6机+5闸)',
    }
    for keyword, station_name in mapping.items():
        if keyword in text:
            return station_name
    return None


def load_station_catalog(objects_yaml_path):
    if not objects_yaml_path:
        return {}
    try:
        from build_timeseries_report import (
            build_business_children,
            is_station_business_category,
            parse_business_objects,
        )
    except Exception:
        return {}

    yaml_path = Path(objects_yaml_path)
    if not yaml_path.exists():
        return {}

    objects_yaml_text = yaml_path.read_text(encoding='utf-8')
    business_catalog = parse_business_objects(objects_yaml_text)
    business_children = build_business_children(business_catalog)
    station_catalog = defaultdict(lambda: {'inflow_sections': set(), 'turbines': set()})
    for child in business_children:
        if not is_station_business_category(child.get('businessCategory')):
            continue
        station_name = str(child.get('businessObjectName') or '')
        if not station_name:
            continue
        if child.get('sourceObjectType') == 'CrossSection' and child.get('childRole') == '闸前断面':
            station_catalog[station_name]['inflow_sections'].add(str(child.get('sourceObjectName') or ''))
        if child.get('sourceObjectType') == 'Turbine':
            turbine_name = str(child.get('sourceObjectName') or '')
            if turbine_name:
                station_catalog[station_name]['turbines'].add(turbine_name)
            turbine_id = child.get('sourceObjectId')
            if turbine_id is not None:
                station_catalog[station_name]['turbines'].add(str(turbine_id))
    return dict(station_catalog)


def build_station_power_series(records, mpc_payload=None, objects_yaml_path=None):
    station_catalog = load_station_catalog(objects_yaml_path)
    inflow_section_to_station = {}
    turbine_to_station = {}
    for station_name, info in station_catalog.items():
        for section_name in info['inflow_sections']:
            inflow_section_to_station[str(section_name)] = station_name
        for turbine_name in info['turbines']:
            turbine_to_station[str(turbine_name)] = station_name

    power_by_station_step = defaultdict(lambda: defaultdict(float))
    flow_by_station_step = defaultdict(lambda: defaultdict(float))
    for record in records:
        step = record.get('data_index')
        value = record.get('value')
        if step is None or value is None:
            continue
        step = int(step)
        value = float(value)
        object_name = str(record.get('object_name') or '')
        if is_turbine_output_record(record):
            station_name = (
                turbine_to_station.get(object_name)
                or turbine_to_station.get(str(record.get('object_id') or ''))
                or infer_station_name_from_turbine_name(object_name)
            )
            if station_name:
                power_by_station_step[station_name][step] += value
            continue
        if str(record.get('metrics_code') or '') != 'water_flow':
            continue
        station_name = inflow_section_to_station.get(object_name)
        if station_name:
            flow_by_station_step[station_name][step] += value

    station_names = sorted(set(power_by_station_step) | set(flow_by_station_step))
    if station_names:
        station_series = defaultdict(lambda: {'power': [], 'flow': []})
        for station_name in station_names:
            for step in sorted(power_by_station_step.get(station_name, {})):
                station_series[station_name]['power'].append((step, power_by_station_step[station_name][step]))
            for step in sorted(flow_by_station_step.get(station_name, {})):
                station_series[station_name]['flow'].append((step, flow_by_station_step[station_name][step]))
        return dict(station_series)

    return build_station_power_series_from_mpc(mpc_payload)


def get_stats(records):
    """生成统计摘要"""
    metrics = Counter(r['metrics_code'] for r in records)
    obj_types = Counter(r['object_type'] for r in records)
    obj_names = Counter(r['object_name'] for r in records)
    indices = sorted(set(r['data_index'] for r in records))

    neg_flow = [r for r in records if r['metrics_code'] == 'water_flow' and r['value'] < 0]
    neg_objects = set(r['object_name'] for r in neg_flow)

    wl_vals = [r['value'] for r in records if r['metrics_code'] == 'water_level']
    wf_vals = [r['value'] for r in records if r['metrics_code'] == 'water_flow']
    turbine_vals = [r['value'] for r in records if is_turbine_output_record(r)]
    turbine_objects = sorted({
        str(r.get('object_name') or r.get('device_name') or r.get('name') or '未命名水轮机')
        for r in records
        if is_turbine_output_record(r)
    })

    return {
        'total_records': len(records),
        'total_steps': len(indices),
        'step_range': (min(indices), max(indices)),
        'total_objects': len(obj_names),
        'metrics_distribution': dict(metrics),
        'object_type_distribution': dict(obj_types),
        'water_level_range': (min(wl_vals), max(wl_vals)) if wl_vals else None,
        'water_flow_range': (min(wf_vals), max(wf_vals)) if wf_vals else None,
        'turbine_output_range': (min(turbine_vals), max(turbine_vals)) if turbine_vals else None,
        'turbine_output_objects': turbine_objects,
        'turbine_output_series_count': len(turbine_objects),
        'negative_flow_count': len(neg_flow),
        'negative_flow_objects': sorted(neg_objects),
        'min_negative_flow': min(r['value'] for r in neg_flow) if neg_flow else None,
    }


def auto_select_sections(groups, count=5):
    """自动选取沿程代表性断面（上游到下游均匀分布）"""
    all_sections = sorted(set(
        name for (name, metric, otype) in groups
        if otype == 'CrossSection' and metric == 'water_level' and name.startswith('QD-')
    ), key=lambda x: int(x.split('-')[1].split('#')[0]))

    if len(all_sections) <= count:
        return all_sections
    step = max(1, len(all_sections) // (count - 1))
    selected = [all_sections[i] for i in range(0, len(all_sections), step)]
    if all_sections[-1] not in selected:
        selected.append(all_sections[-1])
    return selected[:count]


def auto_select_coupled_sections(groups, count=4):
    candidates = []
    for (name, metric, otype), level_data in groups.items():
        if metric != 'water_level' or otype != 'CrossSection':
            continue
        flow_key = (name, 'water_flow', 'CrossSection')
        if flow_key not in groups:
            continue
        flow_values = [value for _, value in groups[flow_key]]
        level_values = [value for _, value in level_data]
        if not flow_values or not level_values:
            continue
        flow_range = max(flow_values) - min(flow_values)
        level_range = max(level_values) - min(level_values)
        score = abs(flow_range) + abs(level_range) * 10
        candidates.append((score, name))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in candidates[:count]]


def auto_detect_neg_flow_objects(groups):
    """自动识别出现负流量的断面"""
    neg_objects = []
    for (name, metric, otype), data in groups.items():
        if metric == 'water_flow':
            vals = [v for _, v in data]
            if any(v < 0 for v in vals):
                neg_objects.append(name)
    return sorted(neg_objects)


def auto_detect_gates(groups):
    """自动识别所有闸门"""
    return sorted(set(
        name for (name, metric, otype) in groups
        if otype == 'Gate' and metric == 'gate_opening'
    ))


def auto_detect_disturbance_nodes(groups):
    """自动识别分水口/退水闸"""
    return sorted(set(
        name for (name, metric, otype) in groups
        if otype == 'DisturbanceNode' and metric == 'water_flow'
    ))


def chart6_turbine_output(turbine_groups, output_dir, axis_label):
    """图6: 水轮机出力时序"""
    if not turbine_groups:
        print("图6 跳过: 未检测到水轮机出力数据")
        return
    fig, ax = plt.subplots(figsize=(14, 6))
    turbine_names = sorted(turbine_groups)
    color_map = plt.get_cmap('tab20', max(len(turbine_names), 1))
    for index, name in enumerate(turbine_names):
        steps, vals = zip(*turbine_groups[name])
        ax.plot(steps, vals, label=name, linewidth=1.8, color=color_map(index))
    ax.axhline(y=0, color='#B91C1C', linestyle='--', alpha=0.4, label='零出力线')
    ax.set_xlabel(axis_label, fontsize=12)
    ax.set_ylabel('出力', fontsize=12)
    ax.set_title('水轮机出力时序变化', fontsize=14, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart6_turbine_output_power.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图6 已生成: {path}")


def build_station_power_series_from_mpc(mpc_payload):
    if not mpc_payload:
        return {}

    station_labels = {
        20100: '瀑布沟站',
        20300: '深溪沟站',
        20500: '枕头坝站',
        20700: '沙坪坝站',
    }
    turbine_ids_by_station = defaultdict(set)
    for item in mpc_payload.get('data') or []:
        for detail in item.get('hydro_mpc_details') or []:
            if (
                str(detail.get('command_type') or '') == 'output_power'
                and detail.get('node_id') is not None
                and detail.get('object_id') is not None
            ):
                turbine_ids_by_station[int(detail['node_id'])].add(int(detail['object_id']))

    station_series = defaultdict(lambda: {'power': [], 'flow': []})
    for item in mpc_payload.get('data') or []:
        step = item.get('step')
        if step is None:
            continue
        power_by_station = defaultdict(float)
        flow_by_station = defaultdict(float)
        for detail in item.get('hydro_mpc_details') or []:
            node_id = detail.get('node_id')
            object_id = detail.get('object_id')
            value = detail.get('value')
            command_type = str(detail.get('command_type') or '')
            if node_id is None or object_id is None or value is None:
                continue
            node_id = int(node_id)
            object_id = int(object_id)
            value = float(value)
            if command_type == 'output_power':
                power_by_station[node_id] += value
            elif command_type == 'water_flow' and object_id in turbine_ids_by_station.get(node_id, set()):
                flow_by_station[node_id] += value

        for node_id in sorted(set(power_by_station) | set(flow_by_station)):
            label = station_labels.get(node_id, f'Node {node_id}')
            station_series[label]['power'].append((int(step), power_by_station.get(node_id, 0.0)))
            station_series[label]['flow'].append((int(step), flow_by_station.get(node_id, 0.0)))

    return dict(station_series)


def chart8_level_flow_coupling(groups, output_dir, axis_label):
    sections = auto_select_coupled_sections(groups)
    if not sections:
        print("图8 跳过: 未检测到可用的水位-流量联动断面")
        return

    cols = 2
    rows = (len(sections) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(15, 4.8 * rows))
    axes = np.atleast_1d(axes).flatten()
    for index, name in enumerate(sections):
        ax = axes[index]
        ax2 = ax.twinx()
        level_steps, level_vals = zip(*groups[(name, 'water_level', 'CrossSection')])
        flow_steps, flow_vals = zip(*groups[(name, 'water_flow', 'CrossSection')])
        ax.plot(level_steps, level_vals, color='#185b75', linewidth=2.0, label='水位')
        ax2.plot(flow_steps, flow_vals, color='#c66a1d', linewidth=1.8, linestyle='--', label='流量')
        ax2.axhline(y=0, color='#B91C1C', linestyle=':', alpha=0.4)
        ax.set_title(name, fontsize=11, fontweight='bold')
        ax.set_xlabel(axis_label, fontsize=10)
        ax.set_ylabel('水位 (m)', fontsize=10, color='#185b75')
        ax2.set_ylabel('流量 (m³/s)', fontsize=10, color='#c66a1d')
        ax.grid(True, alpha=0.25)
        lines = ax.get_lines() + ax2.get_lines()
        ax.legend(lines, [line.get_label() for line in lines], loc='best', fontsize=8)
    for index in range(len(sections), len(axes)):
        axes[index].set_visible(False)
    plt.suptitle('关键断面水位-流量联动对比', fontsize=14, fontweight='bold')
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart8_level_flow_coupling.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图8 已生成: {path}")


def chart9_station_inflow_power_comparison(records, mpc_payload, output_dir, axis_label, objects_yaml_path=None):
    station_series = build_station_power_series(records, mpc_payload, objects_yaml_path)
    if not station_series:
        print("图9 跳过: 未检测到可用的梯级电站来流-出力数据")
        return

    station_names = sorted(station_series)
    cols = 2
    rows = (len(station_names) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(15, 4.8 * rows))
    axes = np.atleast_1d(axes).flatten()
    for index, station_name in enumerate(station_names):
        ax = axes[index]
        ax2 = ax.twinx()
        flow_series = station_series[station_name]['flow']
        power_series = station_series[station_name]['power']
        if flow_series:
            flow_steps, flow_vals = zip(*flow_series)
            ax.plot(flow_steps, flow_vals, color='#0f8b8d', linewidth=2.0, label='来流代理')
        if power_series:
            power_steps, power_vals = zip(*power_series)
            ax2.plot(power_steps, power_vals, color='#b48b45', linewidth=1.8, linestyle='--', label='总出力')
        ax.set_title(station_name, fontsize=11, fontweight='bold')
        ax.set_xlabel(axis_label, fontsize=10)
        ax.set_ylabel('来流代理 (m³/s)', fontsize=10, color='#0f8b8d')
        ax2.set_ylabel('总出力', fontsize=10, color='#b48b45')
        ax.grid(True, alpha=0.25)
        lines = ax.get_lines() + ax2.get_lines()
        if lines:
            ax.legend(lines, [line.get_label() for line in lines], loc='best', fontsize=8)
    for index in range(len(station_names), len(axes)):
        axes[index].set_visible(False)
    plt.suptitle('梯级电站来流-出力对比', fontsize=14, fontweight='bold')
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart9_station_inflow_power_comparison.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图9 已生成: {path}")


def chart10_station_output_composition(records, mpc_payload, output_dir, axis_label, objects_yaml_path=None):
    station_series = build_station_power_series(records, mpc_payload, objects_yaml_path)
    station_names = sorted(name for name, series in station_series.items() if series.get('power'))
    if not station_names:
        print("图10 跳过: 未检测到可用于梯级总出力构成分析的站级出力数据")
        return

    step_values = sorted({step for name in station_names for step, _ in station_series[name]['power']})
    if not step_values:
        print("图10 跳过: 梯级总出力构成缺少有效时间步")
        return

    power_matrix = []
    total_output = np.zeros(len(step_values))
    color_map = plt.get_cmap('tab10')
    for station_name in station_names:
        series_map = {int(step): float(value) for step, value in station_series[station_name]['power']}
        values = np.array([series_map.get(step, 0.0) for step in step_values], dtype=float)
        power_matrix.append(values)
        total_output += values

    fig, ax = plt.subplots(figsize=(15, 6.5))
    colors = [color_map(index % 10) for index, _ in enumerate(station_names)]
    ax.stackplot(step_values, power_matrix, labels=station_names, colors=colors, alpha=0.88)
    ax.plot(step_values, total_output, color='#1f2937', linewidth=2.2, label='梯级总出力')
    ax.set_xlabel(axis_label, fontsize=12)
    ax.set_ylabel('总出力', fontsize=12)
    ax.set_title('梯级总出力构成与协同分配', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.25)
    ax.legend(loc='upper left', fontsize=9, ncol=2)
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart10_station_output_composition.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图10 已生成: {path}")


def chart11_turbine_dispatch_heatmap(turbine_groups, output_dir, axis_label):
    if not turbine_groups:
        print("图11 跳过: 未检测到可用于机组分组堆叠面积图的水轮机出力数据")
        return

    turbine_names = sorted(turbine_groups)
    step_values = sorted({int(step) for name in turbine_names for step, _ in turbine_groups[name]})
    if not step_values:
        print("图11 跳过: 机组分组堆叠面积图缺少有效时间步")
        return

    total_by_turbine = {
        name: float(sum(float(value) for _, value in turbine_groups[name]))
        for name in turbine_names
    }
    ranked_names = sorted(turbine_names, key=lambda name: (-total_by_turbine[name], name))
    series_values = []
    for name in ranked_names:
        value_map = {int(step): float(value) for step, value in turbine_groups[name]}
        series_values.append(np.array([value_map.get(step, 0.0) for step in step_values], dtype=float))

    total_output = np.sum(series_values, axis=0) if series_values else np.zeros(len(step_values))
    fig, ax = plt.subplots(figsize=(15, 6.8))
    color_map = plt.get_cmap('tab20', max(len(ranked_names), 1))
    colors = [color_map(index) for index, _ in enumerate(ranked_names)]
    ax.stackplot(step_values, series_values, labels=ranked_names, colors=colors, alpha=0.9)
    ax.plot(step_values, total_output, color='#1f2937', linewidth=2.2, label='机组总出力')
    ax.set_title('机组分组堆叠面积图', fontsize=14, fontweight='bold')
    ax.set_xlabel(axis_label, fontsize=12)
    ax.set_ylabel('出力', fontsize=12)
    ax.grid(True, alpha=0.25)
    ax.legend(loc='upper left', fontsize=8, ncol=2)
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart11_turbine_dispatch_heatmap.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图11 已生成: {path}")


def chart1_water_level(groups, output_dir, axis_label, sections=None):
    """图1: 关键断面水位时序"""
    if sections is None:
        sections = auto_select_sections(groups)
    fig, ax = plt.subplots(figsize=(14, 6))
    for name in sections:
        key = (name, 'water_level', 'CrossSection')
        if key in groups:
            steps, vals = zip(*groups[key])
            ax.plot(steps, vals, label=name, linewidth=1.5)
    ax.set_xlabel(axis_label, fontsize=12)
    ax.set_ylabel('水位 (m)', fontsize=12)
    ax.set_title('关键断面水位时序变化', fontsize=14, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart1_water_level.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图1 已生成: {path}")


def chart2_water_flow(groups, output_dir, axis_label, sections=None):
    """图2: 关键断面流量时序"""
    if sections is None:
        sections = auto_select_sections(groups)
    fig, ax = plt.subplots(figsize=(14, 6))
    for name in sections:
        key = (name, 'water_flow', 'CrossSection')
        if key in groups:
            steps, vals = zip(*groups[key])
            ax.plot(steps, vals, label=name, linewidth=1.5)
    ax.axhline(y=0, color='red', linestyle='--', alpha=0.5, label='零流量线')
    ax.set_xlabel(axis_label, fontsize=12)
    ax.set_ylabel('流量 (m³/s)', fontsize=12)
    ax.set_title('关键断面流量时序变化（负值=倒流）', fontsize=14, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart2_water_flow.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图2 已生成: {path}")


def chart3_negative_flow(groups, output_dir, axis_label):
    """图3: 负流量专项分析"""
    neg_objects = auto_detect_neg_flow_objects(groups)
    if not neg_objects:
        print("图3 跳过: 未检测到负流量")
        return
    fig, ax = plt.subplots(figsize=(14, 6))
    for name in neg_objects[:7]:  # 最多展示7个
        for otype in ['CrossSection', 'DisturbanceNode', 'Gate']:
            key = (name, 'water_flow', otype)
            if key in groups:
                steps, vals = zip(*groups[key])
                ax.plot(steps, vals, label=name, linewidth=1.5)
                break
    ax.axhline(y=0, color='red', linestyle='--', linewidth=2, alpha=0.7, label='零流量线')
    ax.set_xlabel(axis_label, fontsize=12)
    ax.set_ylabel('流量 (m³/s)', fontsize=12)
    ax.set_title('下游断面负流量（倒流）专项分析', fontsize=14, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart3_negative_flow.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图3 已生成: {path}")


def chart4_gate_opening(groups, output_dir, axis_label):
    """图4: 闸门开度时序"""
    gates = auto_detect_gates(groups)
    if not gates:
        print("图4 跳过: 未检测到闸门数据")
        return
    n = len(gates)
    cols = min(n, 2)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(14, 4 * rows))
    if n == 1:
        axes = np.array([axes])
    axes = axes.flatten()
    for i, name in enumerate(gates):
        ax = axes[i]
        key = (name, 'gate_opening', 'Gate')
        if key in groups:
            steps, vals = zip(*groups[key])
            ax.plot(steps, vals, color='#2196F3', linewidth=2)
            ax.fill_between(steps, vals, alpha=0.2, color='#2196F3')
        ax.set_title(name, fontsize=11, fontweight='bold')
        ax.set_xlabel(axis_label, fontsize=10)
        ax.set_ylabel('开度 (m)', fontsize=10)
        ax.grid(True, alpha=0.3)
    for j in range(i + 1, len(axes)):
        axes[j].set_visible(False)
    plt.suptitle('闸门开度时序变化', fontsize=14, fontweight='bold')
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart4_gate_opening.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图4 已生成: {path}")


def chart5_disturbance_flow(groups, output_dir, axis_label):
    """图5: 分水口流量分析"""
    nodes = auto_detect_disturbance_nodes(groups)
    if not nodes:
        print("图5 跳过: 未检测到分水口数据")
        return
    fig, ax = plt.subplots(figsize=(14, 6))
    for name in nodes:
        key = (name, 'water_flow', 'DisturbanceNode')
        if key in groups:
            steps, vals = zip(*groups[key])
            ax.plot(steps, vals, label=name, linewidth=1.5, marker='o', markersize=2)
    ax.set_xlabel(axis_label, fontsize=12)
    ax.set_ylabel('流量 (m³/s)', fontsize=12)
    ax.set_title('分水口/退水闸流量时序', fontsize=14, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart5_disturbance_flow.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图5 已生成: {path}")


def main():
    args = parse_args(sys.argv[1:])

    data_path = args.data_path
    output_dir = args.output_dir if args.output_dir else os.path.dirname(data_path) or '.'
    os.makedirs(output_dir, exist_ok=True)

    records = load_data(data_path)
    groups = group_data(records)
    turbine_groups = group_turbine_output(records)
    mpc_payload = load_mpc_payload(args.mpc_results_json)
    stats = get_stats(records)
    axis_info = resolve_axis_info(
        records,
        total_steps=args.total_steps,
        sim_step_size=args.sim_step_size,
        output_step_size=args.output_step_size,
    )
    stats['axis_label'] = axis_info['label']
    stats['axis_note'] = axis_info['note']
    if args.total_steps is not None:
        stats['configured_total_steps'] = args.total_steps
    if args.sim_step_size is not None:
        stats['configured_sim_step_size'] = args.sim_step_size
    if args.output_step_size is not None:
        stats['configured_output_step_size'] = args.output_step_size

    # 保存统计摘要
    stats_path = os.path.join(output_dir, 'analysis_stats.json')
    # 序列化 set/tuple
    serializable_stats = {k: (list(v) if isinstance(v, (set, tuple)) else v) for k, v in stats.items()}
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(serializable_stats, f, ensure_ascii=False, indent=2)
    print(f"\n统计摘要已保存: {stats_path}")
    print(f"  总记录: {stats['total_records']}, 步数: {stats['total_steps']}, 对象数: {stats['total_objects']}")
    print(f"  横轴口径: {axis_info['note']}")
    print(f"  水位范围: {stats['water_level_range']}")
    print(f"  流量范围: {stats['water_flow_range']}")
    print(f"  水轮机出力范围: {stats['turbine_output_range']}")
    print(f"  负流量: {stats['negative_flow_count']} 条, 涉及 {len(stats['negative_flow_objects'])} 个对象")

    # 生成图表
    sections = auto_select_sections(groups)
    print(f"\n自动选取代表断面: {sections}\n")

    chart1_water_level(groups, output_dir, axis_info['label'], sections)
    chart2_water_flow(groups, output_dir, axis_info['label'], sections)
    chart3_negative_flow(groups, output_dir, axis_info['label'])
    chart4_gate_opening(groups, output_dir, axis_info['label'])
    chart5_disturbance_flow(groups, output_dir, axis_info['label'])
    chart6_turbine_output(turbine_groups, output_dir, axis_info['label'])
    chart8_level_flow_coupling(groups, output_dir, axis_info['label'])
    chart9_station_inflow_power_comparison(records, mpc_payload, output_dir, axis_info['label'], args.objects_yaml)
    chart10_station_output_composition(records, mpc_payload, output_dir, axis_info['label'], args.objects_yaml)
    chart11_turbine_dispatch_heatmap(turbine_groups, output_dir, axis_info['label'])

    print(f"\n所有图表已生成到: {output_dir}")


if __name__ == '__main__':
    main()
