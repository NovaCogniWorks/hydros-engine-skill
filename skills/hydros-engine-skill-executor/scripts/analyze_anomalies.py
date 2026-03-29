#!/usr/bin/env python3
"""
水力仿真结果异常检测脚本

用法:
    python analyze_anomalies.py <timeseries_data.json> [output_dir]

检测项目:
  1. 负流量（倒流）
  2. 水位异常波动
  3. 零流量节点
  4. 恒定流量（无动态变化）
  5. 数据缺失
"""

import json
import csv
import sys
import os
from collections import defaultdict


def load_data(filepath):
    """加载 JSON 或 CSV 格式的时序数据"""
    if filepath.endswith('.csv'):
        records = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append({
                    'data_index': int(row['data_index']),
                    'metrics_code': row['metrics_code'],
                    'object_name': row['object_name'],
                    'object_type': row['object_type'],
                    'value': float(row['value']),
                    'object_id': row['object_id']
                })
        print(f"从 CSV 加载了 {len(records)} 条记录")
        return records
    else:
        with open(filepath, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        return raw['result']['data']


def group_by_object_metric(records):
    groups = defaultdict(list)
    for r in records:
        key = (r['object_name'], r['metrics_code'], r['object_type'])
        groups[key].append((r['data_index'], r['value']))
    for k in groups:
        groups[k].sort()
    return groups


def detect_negative_flow(groups):
    """检测负流量（倒流）"""
    issues = []
    for (name, metric, otype), data in groups.items():
        if metric != 'water_flow':
            continue
        neg_vals = [(step, val) for step, val in data if val < 0]
        if neg_vals:
            min_val = min(v for _, v in neg_vals)
            issues.append({
                'severity': 'HIGH',
                'type': '负流量（倒流）',
                'object': name,
                'object_type': otype,
                'count': len(neg_vals),
                'total_points': len(data),
                'ratio': f"{len(neg_vals)/len(data)*100:.1f}%",
                'min_value': round(min_val, 2),
                'first_step': neg_vals[0][0],
                'description': f"{name} 出现 {len(neg_vals)} 个负流量数据点（占 {len(neg_vals)/len(data)*100:.1f}%），最大反向流量 {min_val:.2f} m³/s"
            })
    return sorted(issues, key=lambda x: x['min_value'])


def detect_water_level_anomaly(groups, threshold=0.5):
    """检测水位异常波动（相邻步差值超过阈值）"""
    issues = []
    for (name, metric, otype), data in groups.items():
        if metric != 'water_level':
            continue
        vals = [v for _, v in data]
        jumps = []
        for i in range(1, len(vals)):
            diff = abs(vals[i] - vals[i-1])
            if diff > threshold:
                jumps.append((data[i][0], diff))
        if jumps:
            max_jump = max(j[1] for j in jumps)
            issues.append({
                'severity': 'MEDIUM',
                'type': '水位异常波动',
                'object': name,
                'object_type': otype,
                'count': len(jumps),
                'max_jump': round(max_jump, 2),
                'description': f"{name} 出现 {len(jumps)} 次水位突变（阈值 {threshold}m），最大跳变 {max_jump:.2f} m"
            })
    return sorted(issues, key=lambda x: -x['max_jump'])


def detect_zero_flow(groups):
    """检测零流量节点"""
    issues = []
    for (name, metric, otype), data in groups.items():
        if metric != 'water_flow':
            continue
        vals = [v for _, v in data]
        if all(v == 0 for v in vals):
            issues.append({
                'severity': 'MEDIUM',
                'type': '零流量节点',
                'object': name,
                'object_type': otype,
                'count': len(vals),
                'description': f"{name} 流量在所有 {len(vals)} 个仿真步中均为 0，可能未启用或配置错误"
            })
    return issues


def detect_constant_flow(groups):
    """检测恒定流量（标准差为 0）"""
    issues = []
    for (name, metric, otype), data in groups.items():
        if metric != 'water_flow':
            continue
        vals = [v for _, v in data]
        if len(set(vals)) == 1 and vals[0] != 0:  # 排除零流量（已在上面检测）
            issues.append({
                'severity': 'LOW',
                'type': '恒定流量',
                'object': name,
                'object_type': otype,
                'constant_value': round(vals[0], 2),
                'count': len(vals),
                'description': f"{name} 流量恒定为 {vals[0]:.2f} m³/s，缺乏动态变化"
            })
    return issues


def detect_data_gaps(groups, expected_steps=None):
    """检测数据缺失。

    默认把 CSV 中实际出现过的全局采样步集合视为期望步集合，
    这样稀疏输出（如每 30 个计算步输出一次）不会被误判为缺失。
    """
    if expected_steps is None:
        expected = set()
        for data in groups.values():
            expected.update(s for s, _ in data)
    elif isinstance(expected_steps, int):
        expected = set(range(1, expected_steps + 1))
    else:
        expected = set(expected_steps)

    total_expected = len(expected)
    issues = []
    for (name, metric, otype), data in groups.items():
        actual_steps = set(s for s, _ in data)
        missing = expected - actual_steps
        if missing:
            issues.append({
                'severity': 'LOW',
                'type': '数据缺失',
                'object': name,
                'metrics_code': metric,
                'object_type': otype,
                'missing_steps': len(missing),
                'total_expected': total_expected,
                'description': f"{name}/{metric} 缺少 {len(missing)} 个采样步的数据（期望 {total_expected} 个采样步）"
            })
    return sorted(issues, key=lambda x: -x['missing_steps'])


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_anomalies.py <timeseries_data.json> [output_dir]")
        sys.exit(1)

    data_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(data_path) or '.'
    os.makedirs(output_dir, exist_ok=True)

    records = load_data(data_path)
    groups = group_by_object_metric(records)

    print("=" * 60)
    print("水力仿真结果异常检测报告")
    print("=" * 60)

    all_issues = []

    # 1. 负流量
    neg_issues = detect_negative_flow(groups)
    all_issues.extend(neg_issues)
    print(f"\n[HIGH] 负流量检测: 发现 {len(neg_issues)} 个对象存在倒流")
    for issue in neg_issues[:5]:
        print(f"  - {issue['description']}")

    # 2. 水位异常
    wl_issues = detect_water_level_anomaly(groups)
    all_issues.extend(wl_issues)
    print(f"\n[MEDIUM] 水位异常波动: 发现 {len(wl_issues)} 个对象")
    for issue in wl_issues[:5]:
        print(f"  - {issue['description']}")

    # 3. 零流量
    zero_issues = detect_zero_flow(groups)
    all_issues.extend(zero_issues)
    print(f"\n[MEDIUM] 零流量节点: 发现 {len(zero_issues)} 个")
    for issue in zero_issues:
        print(f"  - {issue['description']}")

    # 4. 恒定流量
    const_issues = detect_constant_flow(groups)
    all_issues.extend(const_issues)
    print(f"\n[LOW] 恒定流量: 发现 {len(const_issues)} 个")
    for issue in const_issues[:5]:
        print(f"  - {issue['description']}")

    # 5. 数据缺失
    gap_issues = detect_data_gaps(groups)
    all_issues.extend(gap_issues)
    print(f"\n[LOW] 数据缺失: 发现 {len(gap_issues)} 个")
    for issue in gap_issues[:5]:
        print(f"  - {issue['description']}")

    # 保存完整报告
    report = {
        'summary': {
            'total_issues': len(all_issues),
            'high': len([i for i in all_issues if i['severity'] == 'HIGH']),
            'medium': len([i for i in all_issues if i['severity'] == 'MEDIUM']),
            'low': len([i for i in all_issues if i['severity'] == 'LOW']),
        },
        'issues': all_issues
    }
    report_path = os.path.join(output_dir, 'anomaly_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"检测完成: HIGH={report['summary']['high']}, MEDIUM={report['summary']['medium']}, LOW={report['summary']['low']}")
    print(f"完整报告: {report_path}")


if __name__ == '__main__':
    main()
