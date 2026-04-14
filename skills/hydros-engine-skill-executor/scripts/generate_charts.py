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
  6. 沿程水位热力图
"""

import json
import csv
import sys
import os
import argparse
from collections import defaultdict, Counter

from lib.timeseries_loader import load_timeseries_records

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

    return {
        'total_records': len(records),
        'total_steps': len(indices),
        'step_range': (min(indices), max(indices)),
        'total_objects': len(obj_names),
        'metrics_distribution': dict(metrics),
        'object_type_distribution': dict(obj_types),
        'water_level_range': (min(wl_vals), max(wl_vals)) if wl_vals else None,
        'water_flow_range': (min(wf_vals), max(wf_vals)) if wf_vals else None,
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


def chart6_heatmap(groups, output_dir):
    """图6: 沿程水位热力图"""
    all_sections = sorted(
        [name for (name, metric, otype) in groups
         if otype == 'CrossSection' and metric == 'water_level' and name.startswith('QD-')
         and '#001' in name],
        key=lambda x: int(x.split('-')[1].split('#')[0])
    )
    if not all_sections:
        print("图6 跳过: 未检测到断面水位数据")
        return

    sampled_steps = set()
    for name in all_sections:
        key = (name, 'water_level', 'CrossSection')
        if key in groups:
            for s, _ in groups[key]:
                sampled_steps.add(s)
    sampled_steps = sorted(sampled_steps)
    if not sampled_steps:
        print("图6 跳过: 未检测到有效采样步")
        return

    # 剔除类似 step 0 的占位零值列，避免热力图出现误导性的整列异常颜色。
    placeholder_steps = []
    for step in sampled_steps:
        step_values = []
        for name in all_sections:
            key = (name, 'water_level', 'CrossSection')
            if key not in groups:
                continue
            value_dict = dict(groups[key])
            if step in value_dict:
                step_values.append(value_dict[step])
        if step_values:
            zero_ratio = sum(1 for value in step_values if value == 0) / len(step_values)
            if zero_ratio >= 0.5:
                placeholder_steps.append(step)

    plot_steps = [step for step in sampled_steps if step not in placeholder_steps]
    if not plot_steps:
        plot_steps = sampled_steps

    matrix = []
    for name in all_sections:
        key = (name, 'water_level', 'CrossSection')
        if key in groups:
            val_dict = dict(groups[key])
            row = [val_dict.get(step, np.nan) for step in plot_steps]
            matrix.append(row)

    matrix = np.array(matrix, dtype=float)

    fig, ax = plt.subplots(figsize=(14, 6.8))
    im = ax.imshow(matrix, aspect='auto', cmap='coolwarm', interpolation='nearest')
    ax.set_yticks(range(len(all_sections)))
    ax.set_yticklabels(all_sections, fontsize=9)
    tick_count = min(len(plot_steps), 8)
    tick_positions = np.linspace(0, len(plot_steps) - 1, num=tick_count, dtype=int)
    tick_positions = np.unique(tick_positions)
    ax.set_xticks(tick_positions)
    ax.set_xticklabels([str(plot_steps[index]) for index in tick_positions], fontsize=10)
    ax.set_xlabel('采样步', fontsize=12)
    ax.set_title('沿程断面水位热力图', fontsize=14, fontweight='bold')
    plt.colorbar(im, ax=ax, label='水位 (m)')
    plt.tight_layout()
    path = os.path.join(output_dir, 'chart6_heatmap.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"图6 已生成: {path}")


def main():
    args = parse_args(sys.argv[1:])

    data_path = args.data_path
    output_dir = args.output_dir if args.output_dir else os.path.dirname(data_path) or '.'
    os.makedirs(output_dir, exist_ok=True)

    records = load_data(data_path)
    groups = group_data(records)
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
    print(f"  负流量: {stats['negative_flow_count']} 条, 涉及 {len(stats['negative_flow_objects'])} 个对象")

    # 生成图表
    sections = auto_select_sections(groups)
    print(f"\n自动选取代表断面: {sections}\n")

    chart1_water_level(groups, output_dir, axis_info['label'], sections)
    chart2_water_flow(groups, output_dir, axis_info['label'], sections)
    chart3_negative_flow(groups, output_dir, axis_info['label'])
    chart4_gate_opening(groups, output_dir, axis_info['label'])
    chart5_disturbance_flow(groups, output_dir, axis_info['label'])
    chart6_heatmap(groups, output_dir)

    print(f"\n所有图表已生成到: {output_dir}")


if __name__ == '__main__':
    main()
