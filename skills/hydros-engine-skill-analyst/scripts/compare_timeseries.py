#!/usr/bin/env python3
"""
仿真结果 vs 历史实测数据 对比计算与图表生成脚本。

输出:
  - validation_report.md:  Markdown 格式对比报告
  - validation_report.html: 动态交互式 HTML 对比报告
  - charts/*.png:           静态对比图表
  - validation_metrics.json: 详细指标 JSON
  - mapping.json:           对象映射关系

用法:
    python compare_timeseries.py <mcp_result_file> <excel_file> <output_dir> \
        [--mapping mapping.json] [--step-resolution 120] \
        [--hist-start 2024-01-01] [--hist-days 3]
"""

import argparse
import base64
import json
import os
import sys
import warnings
from io import BytesIO

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC', 'Heiti TC', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False


# ────────────────────────── 数据加载 ──────────────────────────

def load_mcp_data(csv_path):
    suffix = os.path.splitext(csv_path)[1].lower()
    if suffix == '.csv':
        df = pd.read_csv(csv_path)
    elif suffix in {'.xlsx', '.xls', '.xlsm'}:
        df = pd.read_excel(csv_path, sheet_name=0, engine='openpyxl')
    else:
        raise ValueError(f"不支持的仿真结果文件格式: {csv_path}")
    print(f"[MCP] {len(df)} 条记录, {df['object_name'].nunique()} 对象, "
          f"指标={sorted(df['metrics_code'].unique())}, 步={df['data_index'].min()}-{df['data_index'].max()}")
    return df


def load_excel_data(excel_path):
    sheets = pd.read_excel(excel_path, sheet_name=None, engine='openpyxl')
    print(f"[Excel] {len(sheets)} 个 Sheet")
    return sheets


# ────────────────────────── 对象匹配 ──────────────────────────

# 京石段已知映射：MCP对象 → Excel Sheet 名称
KNOWN_MAPPING = {
    'ZM1-入口断面': '北易水倒虹吸出口节制闸',
    'ZM2-入口断面': '坟庄河倒虹吸出口节制闸',
    'FSK2-北易水退水闸': '北易水倒虹吸出口节制闸',
    'FSK6-北拒马退水闸': '北拒马',
}

SHEET_KEYWORDS = {
    '沙河（北）倒虹吸出口节制闸': ['沙河', '沙河北', '沙河（北）'],
    '漠道沟倒虹吸出口节制闸': ['漠道沟'],
    '唐河倒虹吸出口节制闸': ['唐河'],
    '放水河渡槽进口节制闸': ['放水河'],
    '蒲阳河倒虹吸出口节制闸': ['蒲阳河'],
    '瀑河倒虹吸出口节制闸': ['瀑河'],
    '北易水倒虹吸出口节制闸': ['北易水'],
    '坟庄河倒虹吸出口节制闸': ['坟庄'],
    '北拒马': ['北拒马', '拒马'],
    '磁河倒虹吸出口节制闸': ['磁河'],
    '滹沱河倒虹吸出口节制闸': ['滹沱河'],
    '洨河倒虹吸出口节制闸': ['洨河'],
    '午河渡槽进口节制闸': ['午河'],
}


def extract_keyword(sheet_name):
    suffixes = [
        '倒虹吸出口节制闸', '倒虹吸进口节制闸', '渡槽进口节制闸',
        '涵洞进口节制闸', '涵洞式渡槽进口节制闸', '暗渠进口节制闸',
        '隧洞进口节制闸', '节制闸'
    ]
    name = sheet_name.strip()
    for suffix in suffixes:
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name.strip()


def auto_match(mcp_df, sheets):
    """
    匹配逻辑：
    1. 先用已知映射表 KNOWN_MAPPING
    2. 再用关键词匹配（只匹配有 water_level 或 water_flow 的 MCP 对象）
    """
    # 筛选有 water_level 或 water_flow 的 MCP 对象
    wl_wf = mcp_df[mcp_df['metrics_code'].isin(['water_level', 'water_flow'])]
    mcp_objects = wl_wf[['object_name', 'object_type']].drop_duplicates()
    mcp_obj_names = set(mcp_objects['object_name'])

    matches = []
    matched_sheets = set()
    matched_mcp = set()

    # 1) 已知映射
    for mcp_obj, sheet_name in KNOWN_MAPPING.items():
        if mcp_obj in mcp_obj_names and sheet_name in sheets:
            obj_type = mcp_objects[mcp_objects['object_name'] == mcp_obj]['object_type'].iloc[0]
            matches.append({
                'excel_sheet': sheet_name,
                'mcp_object': mcp_obj,
                'mcp_type': obj_type,
                'confidence': '高（已知映射）',
            })
            matched_sheets.add(sheet_name)
            matched_mcp.add(mcp_obj)

    # 2) 关键词匹配（补充）
    for sheet_name in sheets:
        if sheet_name in matched_sheets:
            continue
        keyword = extract_keyword(sheet_name)
        if not keyword or len(keyword) < 2:
            continue
        for _, row in mcp_objects.iterrows():
            obj_name = row['object_name']
            if obj_name in matched_mcp:
                continue
            # 检查关键词是否出现在 MCP 对象名中
            obj_label = obj_name.split('-', 1)[-1] if '-' in obj_name else obj_name
            if keyword in obj_label or any(kw in obj_label for kw in SHEET_KEYWORDS.get(sheet_name, [])):
                matches.append({
                    'excel_sheet': sheet_name,
                    'mcp_object': obj_name,
                    'mcp_type': row['object_type'],
                    'confidence': '中（关键词匹配）',
                })
                matched_sheets.add(sheet_name)
                matched_mcp.add(obj_name)

    return matches


# ────────────────────────── 时间对齐 ──────────────────────────

def align_time(mcp_metric_data, excel_df, step_resolution, hist_start=None, hist_days=3):
    mcp_data = mcp_metric_data.copy()
    mcp_data['time_hours'] = (mcp_data['data_index'] - 1) * step_resolution / 3600.0

    date_col = '日期'
    if date_col not in excel_df.columns:
        for col in excel_df.columns:
            if '日期' in str(col) or 'date' in str(col).lower():
                date_col = col
                break

    edf = excel_df.copy()
    edf[date_col] = pd.to_datetime(edf[date_col], errors='coerce')
    edf = edf.dropna(subset=[date_col])

    start = pd.to_datetime(hist_start) if hist_start else edf[date_col].min()
    end = start + pd.Timedelta(days=hist_days)
    edf = edf[(edf[date_col] >= start) & (edf[date_col] < end)].copy()

    if edf.empty:
        return None, None
    edf['time_hours'] = (edf[date_col] - start).dt.total_seconds() / 3600.0
    return mcp_data, edf


# ────────────────────────── 指标计算 ──────────────────────────

def compute_metrics(sim, obs):
    sim, obs = np.asarray(sim, float), np.asarray(obs, float)
    valid = ~(np.isnan(sim) | np.isnan(obs))
    sim, obs = sim[valid], obs[valid]
    if len(sim) < 2:
        return {}

    diff = sim - obs
    obs_mean = np.mean(obs)
    rmse = np.sqrt(np.mean(diff ** 2))
    mae = np.mean(np.abs(diff))
    max_dev = np.max(np.abs(diff))
    bias = np.mean(diff)
    ss_res = np.sum(diff ** 2)
    ss_tot = np.sum((obs - obs_mean) ** 2)
    nse = 1 - ss_res / ss_tot if ss_tot > 0 else float('nan')
    nonzero = np.abs(obs) > 1e-6
    mape = np.mean(np.abs(diff[nonzero]) / np.abs(obs[nonzero])) * 100 if nonzero.sum() > 0 else float('nan')
    corr = np.corrcoef(sim, obs)[0, 1] if np.std(sim) > 0 and np.std(obs) > 0 else float('nan')

    return {k: round(v, 4) if isinstance(v, float) else v for k, v in {
        'RMSE': rmse, 'MAE': mae, 'Max_Deviation': max_dev,
        'MAPE_%': round(mape, 2) if not np.isnan(mape) else mape,
        'NSE': nse, 'R2': nse, 'Bias': bias, 'Correlation': corr,
        'N_points': int(len(sim)),
        'sim_mean': np.mean(sim), 'obs_mean': obs_mean,
        'sim_range': f"{np.min(sim):.2f}~{np.max(sim):.2f}",
        'obs_range': f"{np.min(obs):.2f}~{np.max(obs):.2f}",
    }.items()}


def nse_rating(nse):
    if isinstance(nse, str) or np.isnan(nse):
        return '无法评估'
    if nse > 0.75: return '优秀'
    if nse > 0.50: return '良好'
    if nse > 0.25: return '一般'
    return '较差'


# ────────────────────────── 静态图表 ──────────────────────────

def plot_comparison(sim_h, sim_v, obs_h, obs_v, title, ylabel, metrics_dict, output_path):
    fig, axes = plt.subplots(3, 1, figsize=(14, 12), gridspec_kw={'height_ratios': [3, 1.5, 2]})

    ax1 = axes[0]
    ax1.plot(sim_h, sim_v, 'b-', lw=1.2, label='仿真值', alpha=0.8)
    ax1.plot(obs_h, obs_v, 'r--', lw=1.2, label='实测值', alpha=0.8)
    ax1.set_ylabel(ylabel); ax1.set_title(title); ax1.legend(); ax1.grid(True, alpha=0.3)
    info = f"RMSE={metrics_dict.get('RMSE','N/A')}\nNSE={metrics_dict.get('NSE','N/A')}\nBias={metrics_dict.get('Bias','N/A')}"
    ax1.text(0.02, 0.95, info, transform=ax1.transAxes, va='top', fontsize=9,
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    # 插值到公共 2h 网格
    max_h = min(np.max(sim_h), np.max(obs_h))
    common = np.arange(0, max_h, 2.0)
    if len(common) >= 2:
        si = np.interp(common, sim_h, sim_v)
        oi = np.interp(common, obs_h, obs_v)
        err = si - oi
        axes[1].bar(common, err, width=1.5, color=['#4472C4' if e >= 0 else '#C44444' for e in err], alpha=0.6)
        axes[1].axhline(0, color='k', lw=0.5); axes[1].set_ylabel('偏差'); axes[1].set_title('仿真-实测偏差'); axes[1].grid(True, alpha=0.3)

        axes[2].scatter(oi, si, alpha=0.5, s=20, c='steelblue')
        lims = [min(oi.min(), si.min()), max(oi.max(), si.max())]
        axes[2].plot(lims, lims, 'r--', lw=1, label='1:1 线')
        axes[2].set_xlabel('实测值'); axes[2].set_ylabel('仿真值')
        axes[2].set_title(f"散点图 (R²={metrics_dict.get('R2','N/A')})"); axes[2].legend(); axes[2].grid(True, alpha=0.3)

    axes[1].set_xlabel('时间 (小时)')
    plt.tight_layout(); plt.savefig(output_path, dpi=150, bbox_inches='tight'); plt.close()


def fig_to_base64(fig):
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return b64


# ────────────────────────── Markdown 报告 ──────────────────────────

def generate_md_report(all_results, matches, output_path):
    lines = [
        '# 仿真结果验证报告\n',
        '## 对比概况\n',
        f'- 匹配对象数: {len(matches)}',
        f'- 对比指标组数: {len(all_results)}\n',
        '## 逐对象误差统计\n',
        '| 对象 | 指标 | RMSE | MAE | NSE | Bias | 仿真均值 | 实测均值 | 评级 |',
        '|------|------|------|-----|-----|------|---------|---------|------|',
    ]
    for r in all_results:
        m = r['metrics']
        rating = nse_rating(m.get('NSE', float('nan')))
        lines.append(
            f"| {r['mcp_object']} | {r['excel_col']} | "
            f"{m.get('RMSE','-')} | {m.get('MAE','-')} | {m.get('NSE','-')} | "
            f"{m.get('Bias','-')} | {m.get('sim_mean','-')} | {m.get('obs_mean','-')} | {rating} |"
        )

    lines += [
        '\n## 对比图表\n',
        '图表保存在 `charts/` 目录下，每个匹配对象-指标组合一张。\n',
        '## 精度评级标准\n',
        '| NSE 范围 | 评级 |', '|---------|------|',
        '| > 0.75 | 优秀 |', '| 0.50~0.75 | 良好 |', '| 0.25~0.50 | 一般 |', '| ≤ 0.25 | 较差 |',
    ]
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"[MD] 报告已保存: {output_path}")


# ────────────────────────── 动态 HTML 报告 ──────────────────────────

def generate_html_report(all_results, chart_data, output_path):
    """生成交互式 HTML 报告，内嵌图表和指标切换"""

    # 构建 JSON 数据供 JS 使用
    js_data = []
    for r in all_results:
        entry = {
            'label': r['label'],
            'mcp_object': r['mcp_object'],
            'excel_sheet': r['excel_sheet'],
            'excel_col': r['excel_col'],
            'metrics': r['metrics'],
            'rating': nse_rating(r['metrics'].get('NSE', float('nan'))),
            'chart_b64': r.get('chart_b64', ''),
            'sim_hours': r.get('sim_hours', []),
            'sim_values': r.get('sim_values', []),
            'obs_hours': r.get('obs_hours', []),
            'obs_values': r.get('obs_values', []),
        }
        js_data.append(entry)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>仿真结果验证报告</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; background:#f5f7fa; color:#333; }}
.header {{ background:linear-gradient(135deg, #1a73e8, #0d47a1); color:#fff; padding:30px 40px; }}
.header h1 {{ font-size:28px; margin-bottom:8px; }}
.header p {{ opacity:0.85; font-size:14px; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}
.summary {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px,1fr)); gap:16px; margin:20px 0; }}
.card {{ background:#fff; border-radius:12px; padding:20px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }}
.card .num {{ font-size:32px; font-weight:700; color:#1a73e8; }}
.card .desc {{ font-size:13px; color:#666; margin-top:4px; }}
.tabs {{ display:flex; gap:8px; margin:20px 0 0; }}
.tab {{ padding:10px 20px; border:none; border-radius:8px 8px 0 0; cursor:pointer; font-size:14px; background:#e8eaf0; color:#555; }}
.tab.active {{ background:#fff; color:#1a73e8; font-weight:600; box-shadow:0 -2px 8px rgba(0,0,0,0.06); }}
.panel {{ display:none; background:#fff; border-radius:0 12px 12px 12px; padding:24px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }}
.panel.active {{ display:block; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ background:#f0f2f5; padding:10px 12px; text-align:left; font-weight:600; position:sticky; top:0; }}
td {{ padding:10px 12px; border-bottom:1px solid #eee; }}
tr:hover td {{ background:#f8f9fb; }}
.badge {{ display:inline-block; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:600; }}
.badge.good {{ background:#e6f4ea; color:#1e7e34; }}
.badge.ok {{ background:#fef3cd; color:#856404; }}
.badge.bad {{ background:#f8d7da; color:#721c24; }}
.badge.na {{ background:#e2e3e5; color:#495057; }}
.obj-selector {{ margin:16px 0; }}
.obj-selector select {{ padding:8px 16px; font-size:14px; border-radius:8px; border:1px solid #ccc; }}
.chart-container {{ text-align:center; margin:16px 0; }}
.chart-container img {{ max-width:100%; border-radius:8px; box-shadow:0 2px 12px rgba(0,0,0,0.1); }}
.metric-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(140px,1fr)); gap:12px; margin:16px 0; }}
.metric-item {{ background:#f8f9fb; border-radius:8px; padding:12px; text-align:center; }}
.metric-item .val {{ font-size:20px; font-weight:700; color:#1a73e8; }}
.metric-item .name {{ font-size:11px; color:#888; margin-top:2px; }}
</style>
</head>
<body>

<div class="header">
  <h1>仿真结果验证报告</h1>
  <p>MCP 仿真引擎输出 vs 历史实测数据 | 生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p>
</div>

<div class="container">
  <div class="summary">
    <div class="card"><div class="num">{len(all_results)}</div><div class="desc">对比指标组数</div></div>
    <div class="card"><div class="num">{len(set(r['mcp_object'] for r in all_results))}</div><div class="desc">匹配对象数</div></div>
    <div class="card"><div class="num">{sum(1 for r in all_results if r['metrics'].get('NSE',0) > 0.5)}</div><div class="desc">NSE > 0.5 组数</div></div>
    <div class="card"><div class="num">{sum(1 for r in all_results if r['metrics'].get('NSE',0) > 0.75)}</div><div class="desc">NSE > 0.75 组数</div></div>
  </div>

  <div class="tabs">
    <button class="tab active" onclick="showPanel('table')">汇总表格</button>
    <button class="tab" onclick="showPanel('detail')">逐对象详情</button>
  </div>

  <div id="panel-table" class="panel active">
    <table>
      <thead>
        <tr><th>MCP 对象</th><th>Excel Sheet</th><th>对比指标</th><th>RMSE</th><th>MAE</th><th>NSE</th><th>Bias</th><th>仿真均值</th><th>实测均值</th><th>评级</th></tr>
      </thead>
      <tbody>
"""

    for r in all_results:
        m = r['metrics']
        rating = nse_rating(m.get('NSE', float('nan')))
        badge_cls = 'good' if rating == '优秀' else 'ok' if rating in ('良好','一般') else 'bad' if rating == '较差' else 'na'
        html += f"""        <tr>
          <td>{r['mcp_object']}</td><td>{r['excel_sheet']}</td><td>{r['excel_col']}</td>
          <td>{m.get('RMSE','-')}</td><td>{m.get('MAE','-')}</td><td>{m.get('NSE','-')}</td>
          <td>{m.get('Bias','-')}</td><td>{m.get('sim_mean','-')}</td><td>{m.get('obs_mean','-')}</td>
          <td><span class="badge {badge_cls}">{rating}</span></td>
        </tr>\n"""

    html += """      </tbody>
    </table>
  </div>

  <div id="panel-detail" class="panel">
    <div class="obj-selector">
      <label>选择对象: </label>
      <select id="obj-select" onchange="showDetail()">
"""
    for i, r in enumerate(all_results):
        html += f'        <option value="{i}">{r["label"]}</option>\n'

    html += """      </select>
    </div>
    <div id="detail-content"></div>
  </div>
</div>

<script>
const DATA = """ + json.dumps(js_data, ensure_ascii=False) + """;

function showPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  event.target.classList.add('active');
  if (name === 'detail') showDetail();
}

function showDetail() {
  const idx = document.getElementById('obj-select').value;
  const d = DATA[idx];
  const m = d.metrics;
  const rating = d.rating;
  const badgeCls = rating === '优秀' ? 'good' : (rating === '良好' || rating === '一般') ? 'ok' : rating === '较差' ? 'bad' : 'na';

  let html = '<div class="metric-grid">';
  const items = [
    ['RMSE', m.RMSE], ['MAE', m.MAE], ['NSE', m.NSE],
    ['Bias', m.Bias], ['R²', m.R2], ['MAPE %', m['MAPE_%']],
    ['相关系数', m.Correlation], ['最大偏差', m.Max_Deviation], ['数据点', m.N_points]
  ];
  items.forEach(([name, val]) => {
    html += '<div class="metric-item"><div class="val">' + (val !== undefined && val !== null ? val : '-') + '</div><div class="name">' + name + '</div></div>';
  });
  html += '</div>';
  html += '<div style="margin:12px 0"><strong>精度评级: </strong><span class="badge ' + badgeCls + '">' + rating + '</span>';
  html += '  |  仿真范围: ' + (m.sim_range || '-') + '  |  实测范围: ' + (m.obs_range || '-') + '</div>';

  if (d.chart_b64) {
    html += '<div class="chart-container"><img src="data:image/png;base64,' + d.chart_b64 + '"></div>';
  }

  document.getElementById('detail-content').innerHTML = html;
}

// 初始化
if (DATA.length > 0) showDetail();
</script>
</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"[HTML] 报告已保存: {output_path}")


# ────────────────────────── 主流程 ──────────────────────────

def main():
    parser = argparse.ArgumentParser(description='仿真结果 vs 历史实测数据对比')
    parser.add_argument('mcp_csv', help='MCP 仿真结果文件（CSV 或 XLSX）')
    parser.add_argument('excel_file', help='历史实测数据 Excel')
    parser.add_argument('output_dir', help='输出目录')
    parser.add_argument('--mapping', help='映射 JSON 文件')
    parser.add_argument('--step-resolution', type=int, default=120)
    parser.add_argument('--hist-start', help='历史截取起始日期')
    parser.add_argument('--hist-days', type=int, default=3)
    args = parser.parse_args()

    os.makedirs(os.path.join(args.output_dir, 'charts'), exist_ok=True)

    mcp_df = load_mcp_data(args.mcp_csv)
    sheets = load_excel_data(args.excel_file)

    # 匹配
    if args.mapping and os.path.exists(args.mapping):
        with open(args.mapping) as f:
            matches = json.load(f)
        print(f"\n使用预定义映射: {len(matches)} 组")
    else:
        matches = auto_match(mcp_df, sheets)
        print(f"\n自动匹配: {len(matches)} 组")
        for m in matches:
            print(f"  {m['excel_sheet']} <-> {m['mcp_object']} [{m['confidence']}]")
        with open(os.path.join(args.output_dir, 'mapping.json'), 'w', encoding='utf-8') as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)

    if not matches:
        print("\n未找到匹配对象。"); sys.exit(1)

    # 对比
    metric_map = {'闸前水位': 'water_level', '流量': 'water_flow'}
    all_results = []

    for match in matches:
        sheet_name, mcp_obj = match['excel_sheet'], match['mcp_object']
        if sheet_name not in sheets:
            continue
        excel_df = sheets[sheet_name]
        mcp_obj_data = mcp_df[mcp_df['object_name'] == mcp_obj]
        if mcp_obj_data.empty:
            continue

        print(f"\n=== {sheet_name} <-> {mcp_obj} ===")

        for excel_col, mcp_metric in metric_map.items():
            if excel_col not in excel_df.columns:
                continue
            mcp_m = mcp_obj_data[mcp_obj_data['metrics_code'] == mcp_metric]
            if mcp_m.empty:
                continue

            aligned = align_time(mcp_m, excel_df, args.step_resolution, args.hist_start, args.hist_days)
            if aligned[0] is None or aligned[1] is None or aligned[1].empty:
                print(f"  {excel_col}: 对齐失败"); continue

            mcp_a, excel_a = aligned
            sim_h = mcp_a['time_hours'].values
            sim_v = mcp_a['value'].astype(float).values
            obs_h = excel_a['time_hours'].values
            obs_v = excel_a[excel_col].astype(float).values

            # 插值到 2h 网格
            max_h = min(sim_h.max(), obs_h.max())
            common = np.arange(0, max_h, 2.0)
            if len(common) < 2:
                continue
            si = np.interp(common, sim_h, sim_v)
            oi = np.interp(common, obs_h, obs_v)

            metrics = compute_metrics(si, oi)
            label = f"{mcp_obj} vs {sheet_name} ({excel_col})"
            print(f"  {excel_col}: RMSE={metrics.get('RMSE')}, NSE={metrics.get('NSE')}, Bias={metrics.get('Bias')}")

            # 静态图表
            safe = f"{mcp_obj}_{mcp_metric}".replace('#', '_').replace(' ', '_')
            chart_path = os.path.join(args.output_dir, 'charts', f'{safe}.png')
            plot_comparison(sim_h, sim_v, obs_h, obs_v,
                           title=label, ylabel=excel_col, metrics_dict=metrics, output_path=chart_path)

            # 内嵌图表 base64
            fig, ax = plt.subplots(figsize=(12, 4))
            ax.plot(sim_h, sim_v, 'b-', lw=1, label='仿真', alpha=0.8)
            ax.plot(obs_h, obs_v, 'r--', lw=1, label='实测', alpha=0.8)
            ax.set_xlabel('时间(h)'); ax.set_ylabel(excel_col); ax.set_title(label)
            ax.legend(); ax.grid(True, alpha=0.3)
            chart_b64 = fig_to_base64(fig)

            all_results.append({
                'label': label, 'mcp_object': mcp_obj, 'excel_sheet': sheet_name,
                'excel_col': excel_col, 'metric': mcp_metric, 'metrics': metrics,
                'chart_b64': chart_b64,
                'sim_hours': sim_h.tolist(), 'sim_values': sim_v.tolist(),
                'obs_hours': obs_h.tolist(), 'obs_values': obs_v.tolist(),
            })

    if not all_results:
        print("\n没有成功对比的数据。"); sys.exit(1)

    # 输出
    generate_md_report(all_results, matches, os.path.join(args.output_dir, 'validation_report.md'))
    generate_html_report(all_results, None, os.path.join(args.output_dir, 'validation_report.html'))

    # JSON
    json_results = [{k: v for k, v in r.items() if k not in ('chart_b64', 'sim_hours', 'sim_values', 'obs_hours', 'obs_values')} for r in all_results]
    with open(os.path.join(args.output_dir, 'validation_metrics.json'), 'w', encoding='utf-8') as f:
        json.dump(json_results, f, ensure_ascii=False, indent=2)

    print(f"\n完成! 共 {len(all_results)} 组对比结果。")


if __name__ == '__main__':
    main()
