---
name: hydros-engine-skill-analyst
description: |
  水力仿真结果准确性验证工具。将 MCP 仿真引擎输出的结果文件（CSV / XLSX）与用户显式提供目录或路径中的历史实测 Excel 数据进行对比，
  计算误差统计指标（RMSE、MAE、最大偏差、相关系数等），生成对比曲线图和验证报告。
  当用户提到以下内容时触发：仿真验证、结果对比、精度验证、仿真 vs 实测、accuracy validation、
  误差分析、RMSE、对比曲线、仿真准确性、历史数据对比、节制闸数据对比、验证仿真结果。
  即使用户只是说"对比一下结果"、"看看仿真准不准"、"和实测数据比一下"，也应触发此 skill。
---

# Hydros Engine Skill Analyst

## 概述

本 skill 将 MCP 仿真引擎输出的时序结果文件（CSV / XLSX）与用户显式提供的历史实测数据（Excel）进行对比，验证仿真精度。整个流程分为 4 个阶段。

## 沟通语言

始终使用 **中文** 与用户沟通。

---

## 阶段一：数据加载

### 输入来源硬规则

历史实测 Excel 必须来自用户显式提供的文件路径或目录路径，不得从仓库根目录、当前工作目录、下载目录或历史输出目录中自动猜测。即使本地只发现一个看起来像历史实测数据的 `.xlsx`，也不能默认使用，除非用户明确指定它或明确指定包含它的目录。

执行对比前必须确认三类输入：

| 输入 | 来源规则 |
|------|----------|
| 仿真结果文件 | 可使用当前仿真任务目录中的 `SIM_*.xlsx` / `.csv`，或用户显式提供的结果文件 |
| 历史实测数据 | 必须由用户显式提供 `.xlsx` / `.xlsm` 文件路径，或显式提供包含历史实测 Excel 的目录 |
| MDM 模型 | 优先使用当前仿真任务目录中的 `objects.yaml`，或用户显式提供的 MDM 模型文件 |

如果用户只说“生成对比报告”“对比一下结果”，但没有提供历史实测 Excel 文件或目录：

1. 先说明当前缺少历史实测数据来源。
2. 只询问用户提供历史实测 Excel 文件路径或所在目录。
3. 不要运行 `find . -name '*.xlsx'` 或类似命令去全仓库搜索并自行选择。
4. 不要使用仓库根目录下偶然存在的 Excel 文件作为默认实测数据。

当用户提供的是目录时，只在该目录内查找候选文件：

```bash
find "<用户提供的目录>" -maxdepth 2 -type f \( -name '*.xlsx' -o -name '*.xlsm' \)
```

- 如果恰好找到 1 个候选历史 Excel，可使用该文件，并在报告中写明“历史实测目录由用户提供，命中文件为 ...”。
- 如果找到多个候选文件，列出文件名、大小和修改时间，请用户选定一个，不要自动选择最大、最新或名称最像的文件。
- 如果没有找到候选文件，停止并要求用户重新提供正确目录或文件。

### MCP 仿真数据（结果文件）

结果文件来自 `hydros-engine-simulation-skill` 的阶段五输出，或通过 `get_timeseries_data` MCP 工具获取。

**关键字段：**

| 字段 | 说明 |
|------|------|
| `object_name` | 对象名称，如 `QD-5#断面#001`、`ZM1-节制闸#1`、`FSK2-北易水退水闸` |
| `object_type` | `CrossSection`（断面）、`Gate`（闸门）、`DisturbanceNode`（分水口/退水闸） |
| `metrics_code` | `water_level`、`water_flow`、`gate_opening` |
| `data_index` | 仿真步序号（从 1 开始） |
| `value` | 数值 |

**时间转换**：仿真步到实际时间的转换需要 `step_resolution`（秒/步）。如果结果文件中没有此信息，向用户询问。常见值为 120 秒/步。仿真起始时间通常为 `biz_start_time`（如 2025/01/01 00:00:00）。

### 历史实测数据（Excel）

Excel 文件包含多个 Sheet，每个 Sheet 代表一个节制闸。

**标准列结构：**

| 列 | 对应 MCP 指标 |
|----|-------------|
| 日期 | 时间戳 |
| 闸前水位 | `water_level`（闸前） |
| 闸后水位 | `water_level`（闸后） |
| 1~4号闸门开度 | `gate_opening`（单位可能是 mm，需要转换） |
| 流量 | `water_flow` |

Excel 还可能包含分水口/退水闸流量列（名称不固定，位于流量列之后）和设计水位列。

### 加载步骤

1. 用 `pandas` 读取 MCP 仿真结果文件：`.csv` 用 `pd.read_csv(...)`，`.xlsx` 用 `pd.read_excel(..., sheet_name=0)`
2. 确认历史实测 Excel 来自用户显式提供的文件或目录；若未提供，停止并要求用户提供
3. 用 `openpyxl` 或 `pandas` 读取 Excel 各 Sheet：`pd.read_excel(excel_path, sheet_name=None)`
4. 展示两边的数据摘要：对象数、指标、时间范围
5. 确认 `step_resolution`（默认 120s）

---

## 阶段二：对象匹配

MCP 仿真对象和 Excel Sheet（节制闸）之间的对应关系必须优先通过 **MDM 元数据** 建立，不能默认用静态映射表或直接用仿真对象名猜测。

### MDM 优先匹配逻辑

对于每个历史 Excel Sheet：

1. 从 Sheet 名提取闸站关键词，例如去掉 `倒虹吸出口节制闸`、`渡槽进口节制闸`、`节制闸` 等后缀。
2. 调用 `hydros-engine-mdm.fetch_gate_info(waterway_id, station_name)` 查询闸站。
3. 从 MDM 返回中读取：
   - `station_front_section`：闸前断面
   - `station_back_section`：闸后断面
4. 用 MDM 断面的 `object_id` 到仿真结果 Excel 中精确匹配 `object_id`。
5. 仿真侧按 `metrics_code` 读取 `water_level` / `water_flow`。
6. 历史侧在同一个 Sheet 内优先精确读取 `闸前水位`、`闸后水位`、`流量`。

**匹配规则（按优先级）：**

1. **MDM object_id 精确匹配**：`station_front_section.object_id` / `station_back_section.object_id` 命中仿真结果 `object_id`，置信度最高。
2. **MDM object_name 精确匹配**：当 ID 未命中时，用 MDM 断面名称匹配仿真结果 `object_name`。
3. **历史列名精确匹配**：默认读取 `闸前水位`、`闸后水位`、`流量`；只有精确列名不存在时，才允许用 `上游水位`、`下游水位`、`过闸流量` 等别名兜底。
4. **失败显式输出**：MDM 查不到、断面缺失、仿真序列缺失或历史列缺失时，必须在报告的映射诊断表中写明状态和原因，不得静默套用旧映射。

### 指标映射

| Excel 列 | MCP metrics_code | 说明 |
|----------|-----------------|------|
| 闸前水位 | `water_level` | MDM `station_front_section` 的水位 |
| 闸后水位 | `water_level` | MDM `station_back_section` 的水位 |
| 流量 | `water_flow` | 默认使用 MDM `station_front_section` 的断面流量 |
| N号闸门开度 | `gate_opening` | 注意单位：Excel 可能是 mm，MCP 可能是 m |

### 展示与确认

以 markdown 表格展示匹配结果，让用户确认或修正：

```
| 序号 | Excel Sheet | MCP 对象 | 匹配指标 | 置信度 |
|------|------------|---------|---------|--------|
| 1 | 北易水倒虹吸出口节制闸 | FSK2-北易水退水闸 | water_level, water_flow | 高 |
| 2 | 北拒马 | FSK6-北拒马退水闸 | water_level, water_flow | 高 |
| 3 | 沙河（北）倒虹吸出口节制闸 | (待确认) | - | 低 |
```

用户确认后进入数据质量检查。

### 通用映射脚本

优先使用 `scripts/build_mdm_gate_map.py` 生成映射文件。该脚本以当前仿真结果为入口，读取 `waterway_id` / `biz_scenario_id`，用 MDM 水网模型中的 GateStation 过滤当前仿真实际存在的闸前/闸后断面，再从历史 Excel 全量 Sheet 中反查观测 Sheet，避免把不属于当前仿真范围的历史 Sheet 引入对比。

典型流程：

```bash
python3 scripts/build_mdm_gate_map.py \
  --simulation-file SIM_TASK.xlsx \
  --history-excel 节制闸数据.xlsx \
  --mdm-model objects.yaml \
  --output mdm_gate_map.json \
  --diagnostics mapping_diagnostics.csv

python3 scripts/forebay_water_level_report.py \
  SIM_TASK.xlsx \
  节制闸数据.xlsx \
  output_dir \
  --mdm-map-json mdm_gate_map.json
```

---

## 阶段 2.5：数据质量检查（恒定值检测）

在进行对比计算之前，必须先检查两边数据是否存在恒定不变的情况。恒定数据意味着仿真未真正运行或传感器故障，对比没有意义。

### 为什么要做这个检查

仿真引擎可能因为初始化问题、边界条件缺失或智能体未响应，导致某些对象的输出值在整个仿真过程中完全不变（方差为 0）。如果不检测就直接算 RMSE/NSE，会得到误导性的结果——比如一个恒定值恰好接近实测均值，NSE 看起来还不错，但实际上仿真根本没有动态响应。

### 检测规则

对每个匹配的对象-指标组合，分别检查仿真侧和实测侧：

| 检测项 | 判定条件 | 处理方式 |
|--------|---------|---------|
| 仿真值恒定 | 方差 = 0 或 max - min < 1e-6 | 标记为 `SIM_CONSTANT`，在报告中警告，跳过 NSE 计算（NSE 对恒定值无意义） |
| 实测值恒定 | 方差 = 0 或 max - min < 1e-6 | 标记为 `OBS_CONSTANT`，可能是传感器故障或停机期间，建议换时间段 |
| 仿真值全为 0 | 所有值 = 0 | 标记为 `SIM_ZERO`，说明该对象在仿真中未激活（如退水闸关闭），单独列出 |
| 仿真值近似恒定 | 标准差 / 均值 < 0.1%（变异系数极小） | 标记为 `SIM_NEAR_CONSTANT`，提示用户关注 |

### 检测步骤

1. 对仿真结果文件中每个对象-指标组合，计算 `std`, `min`, `max`, `mean`
2. 对 Excel 中对应时间段的数据做同样计算
3. 按上表规则标记
4. 在报告中单独列出数据质量问题表：

```
## 数据质量检查

| 对象 | 指标 | 数据源 | 问题 | 详情 |
|------|------|--------|------|------|
| FSK2-北易水退水闸 | water_flow | 仿真 | SIM_ZERO | 所有值为 0，退水闸未激活 |
| ZM1-入口断面 | water_level | 仿真 | SIM_NEAR_CONSTANT | std=0.002, CV=0.003% |
```

5. 对标记为 `SIM_CONSTANT` 或 `SIM_ZERO` 的组合，仍然生成对比图表（方便用户直观看到问题），但在误差统计表中标注"不适用"而非给出误导性的 NSE 值

### 恒定值的常见原因

| 原因 | 表现 | 建议 |
|------|------|------|
| 退水闸/分水口未激活 | flow = 0 恒定 | 检查仿真场景配置中该对象是否被启用 |
| 边界条件缺失 | 水位恒定在初始值 | 检查上游/下游边界条件是否正确加载 |
| 智能体未响应 | 闸门开度恒定 | 检查闸站智能体是否正常运行 |
| 仿真步数不足 | 前几步恒定，后面才变化 | 增加仿真步数或检查预热期 |
| 历史数据停机 | 实测值恒定 | 换一个有正常运行数据的时间段 |

---

## 阶段三：时间对齐与对比计算

### 时间对齐策略

仿真时间（如 2025/01/01 起）和历史实测时间（如 2017~2024）通常不在同一时间窗口。采用以下策略之一（询问用户选择）：

1. **相对时间对齐**（推荐）：忽略绝对日期，按仿真的相对时长（如前 3 天）截取历史数据中任意连续 3 天的数据做对比。让用户指定历史数据的起始日期。
2. **全量统计对比**：不做逐步对齐，而是比较两边数据的统计分布特征（均值、标准差、范围）。
3. **用户指定映射**：用户提供仿真时间 ↔ 历史时间的明确对应关系。

### 对比计算

对每一对匹配的对象-指标组合，使用 `scripts/compare_timeseries.py` 计算：

| 指标 | 公式 | 说明 |
|------|------|------|
| RMSE | √(Σ(sim-obs)²/n) | 均方根误差，越小越好 |
| MAE | Σ|sim-obs|/n | 平均绝对误差 |
| Max Deviation | max(|sim-obs|) | 最大偏差 |
| MAPE | Σ(|sim-obs|/|obs|)/n × 100% | 平均绝对百分比误差 |
| R² | 1 - Σ(sim-obs)²/Σ(obs-mean)² | 决定系数，1.0 为完美拟合 |
| NSE | 1 - Σ(sim-obs)²/Σ(obs-mean)² | Nash-Sutcliffe 效率系数（水文领域常用） |
| Bias | mean(sim) - mean(obs) | 系统偏差（正=仿真偏高，负=仿真偏低） |

### 单位转换注意

- 闸门开度：Excel 中可能是 mm，MCP 中可能是 m，需要统一
- 如果数值量级差异大于 100 倍，自动提示用户检查单位

---

## 阶段四：生成图表与报告

### 图表（使用 `scripts/compare_timeseries.py`）

对每一对匹配的对象-指标，生成：

1. **时序对比图** — 仿真值和实测值叠加在同一张图上
   - 双 Y 轴（如量级差异大）或单 Y 轴
   - 标注 RMSE 和 NSE 值在图表标题或角落
2. **误差时序图** — 每个时间步的仿真-实测偏差
   - 标注零线和 ±标准差带
3. **散点图** — 仿真值 vs 实测值
   - 画 1:1 参考线
   - 标注 R² 值
4. **综合误差汇总柱状图** — 所有匹配对象的 RMSE/NSE 对比

输出目录必须参考 `hydros-engine-skill-executor` 的层级组织，不再把 HTML、payload、CSV 平铺在 `output_dir` 根目录：

| 子目录 | 内容 |
| --- | --- |
| `report/` | `validation.html` 等可直接打开或上传的报告文件 |
| `data/` | `mdm_gate_validation_payload.json`、`mdm_gate_validation_metrics.csv`、映射诊断等数据产物 |
| `charts/` | 后续如生成 PNG/SVG 图表时统一放入该目录 |

如果旧版本已在根目录生成 `validation.html`、`mdm_gate_validation_payload.json`、`mdm_gate_validation_metrics.csv`，重新生成时应清理这些旧平铺产物，避免用户误打开过期文件。

### HTML 报告模板

验证报告页面模板必须沉淀在 `assets/validation-report-template/index.html`。生成脚本负责读取模板并替换占位符，避免把整页 HTML/CSS/JS 长期硬编码在脚本里。

当前规范：生成的 `report/validation.html` 采用 **数据内联**，即在 HTML 内直接写入报告 payload，保证单文件可打开和可上传。`assets/validation-report-template/validation.data.js` 仅作为未来外置数据模式的保留文件，当前不要在 HTML 中引用，也不要要求用户额外携带该文件。

报告中的长表必须提供分类过滤：误差统计表至少支持按指标和结果判断过滤，映射诊断表至少支持按指标和匹配结果过滤，数据质量检查表至少支持按指标和数据提示过滤。报告面向业务人员时，表头和单元格不要直接暴露 `MDM_OBJECT_ID`、`SIM_NEAR_CONSTANT`、`OBS_COLUMN_MISSING` 等内部状态码，应转换为“按仿真断面编号匹配”“仿真变化过小”“缺少实测列”等通俗中文。

### 统计报告

生成 `output/validation/validation_report.md`（或 Word），包含：

1. **对比概况** — 数据来源、匹配对象数、时间范围
2. **逐对象误差表** — 每个匹配对的 RMSE / MAE / NSE / R² 汇总
3. **图表** — 插入所有对比图
4. **精度评级** — 根据 NSE 值给出评级：
   - NSE > 0.75：优秀
   - 0.50 < NSE ≤ 0.75：良好
   - 0.25 < NSE ≤ 0.50：一般
   - NSE ≤ 0.25：较差
5. **结论与建议** — 哪些对象仿真精度高/低，可能的原因分析

---

## 快捷入口

| 用户说 | 动作 |
|--------|------|
| "对比仿真结果" / "验证精度" | 从阶段一开始；如果缺少历史实测 Excel 文件或目录，先要求用户提供，不要自动搜索本地 Excel |
| "这两个文件对比一下" | 从阶段一开始，识别文件格式自动分配；若其中没有历史实测 Excel，则要求用户补充历史实测文件或目录 |
| "用这个目录做实测数据" | 只在用户提供的目录内查找 `.xlsx` / `.xlsm`；单个候选可直接使用，多个候选必须让用户选定 |
| "匹配结果不对" / "修正映射" | 回到阶段二重新匹配 |
| "换个时间段" / "重新对齐" | 回到阶段三调整时间 |
| "只看图表" / "生成报告" | 跳到阶段四 |

---

## 依赖

- Python: `pandas`, `openpyxl`, `matplotlib`, `numpy`
- 安装：`pip3 install pandas openpyxl matplotlib numpy --break-system-packages` 或使用 venv
- 中文字体设置同 `hydros-engine-simulation-skill`

---

## 京石段对象映射参考表

以下是已知的 MCP 仿真对象与中线工程节制闸的映射关系（持续更新）：

| MCP 对象 | object_type | Excel Sheet | 可对比指标 | 备注 |
|----------|------------|-------------|-----------|------|
| ZM1-入口断面 | CrossSection | 北易水倒虹吸出口节制闸 | water_level ↔ 闸前水位, water_flow ↔ 流量 | ZM1 节制闸的上游断面 |
| ZM2-入口断面 | CrossSection | 坟庄河倒虹吸出口节制闸 | water_level ↔ 闸前水位, water_flow ↔ 流量 | ZM2 节制闸的上游断面 |
| FSK2-北易水退水闸 | DisturbanceNode | 北易水倒虹吸出口节制闸 | water_level ↔ 闸前水位 | water_flow 常为 0（退水闸未激活），对比流量无意义 |
| FSK6-北拒马退水闸 | DisturbanceNode | 北拒马 | water_level ↔ 闸前水位 | water_flow 常为 0 或极小值 |

### 已知数据质量问题

| 对象 | 指标 | 问题 | 说明 |
|------|------|------|------|
| FSK2-北易水退水闸 | water_flow | SIM_ZERO | 仿真中退水闸关闭，流量恒为 0 |
| FSK6-北拒马退水闸 | water_flow | SIM_NEAR_CONSTANT | 流量接近 0（~0.5 m³/s），与实测差异大 |
| ZM1-节制闸#1/#2 | gate_opening | 无实测对照 | Excel 中闸门开度单位为 mm，MCP 为 m，且 ZM 对象只有 gate_opening 无 water_level |
| ZM2-节制闸#1/#2 | gate_opening | 无实测对照 | 同上 |

当匹配到新的对应关系或发现新的数据质量问题时，应更新此表。
