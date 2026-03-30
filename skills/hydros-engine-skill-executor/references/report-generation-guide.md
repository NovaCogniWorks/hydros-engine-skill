# Hydros 报告生成指南

本文档提供详细的报告生成规范和最佳实践。

## 报告类型

### HTML 报告
- **用途**：正式汇报、截图、归档、结果复盘
- **模板**：`assets/hydros-report-template/index.html`
- **数据模式**：`index.html + report.data.js` 分离模式
- **特性**：纵剖面与时序曲线联动、播放、拖拽、暂停、继续

### Markdown 报告
- **用途**：文档归档、版本控制、快速浏览
- **格式**：图文并茂，用 `![描述](相对路径)` 嵌入 PNG 图表
- **要求**：每张图表前后都应有对应的文字分析

### Word 报告
- **用途**：正式文档（仅当用户明确要求时）
- **触发条件**：用户明确要求正式文档

## 报告生成流程

### 1. 数据获取与验证

**获取数据**：
```python
# 调用 get_timeseries_data 获取 resource_uri
# 调用 read_mcp_resource 读取 CSV 文本
# 落盘为本地 .csv 文件
```

**建模文件兼容**：
- 如果场景 YAML 或 `objects.yaml` 的远程地址包含中文路径，脚本层应先做 URL 编码规范化，再发起请求。
- 一旦已缓存本地 `objects.yaml`，后续报告和纵剖面优先复用本地文件，不要重复拉取同一资源。

**参数传递**：
- 优先使用用户显式提供的 `total_steps`、`sim_step_size`、`output_step_size`
- 其次使用场景 YAML 配置
- 避免写死默认步长

**数据验证**：
- 比较期望总时长与 CSV 实际覆盖时长
- 比较期望输出点数与 CSV 实际采样点数
- 如有不一致，在报告中单列说明

### 2. 生成统计摘要

必需指标：
- 总记录数
- 采样步数
- 对象数
- 指标数
- 异常点数量

### 3. 生成图表

使用 `scripts/generate_charts.py` 生成：
- `chart1_water_level.png` - 水位时序图
- `chart2_water_flow.png` - 流量时序图
- `chart4_gate_opening.png` - 闸门开度图
- `chart5_disturbance_flow.png` - 分水口流量图
- `chart6_heatmap.png` - 热力图
- `chart7_longitudinal_profile.png` - 纵剖面图

**图表要求**：
- y 轴根据实际数值自动收紧范围，避免小幅波动被压扁
- 热力图按真实采样步绘制，不展开稀疏采样
- 识别并剔除占位零值（尤其是首步）

### 4. 异常分析

使用 `scripts/analyze_anomalies.py` 检测：
- 负压
- 流速异常
- 水头损失
- 其他异常信号

## HTML 报告规范

### 目录结构
```
report/
  ├── index.html          # 报告主文件
  └── report.data.js      # 数据 payload
charts/
  ├── chart1_water_level.png
  ├── chart2_water_flow.png
  ├── chart4_gate_opening.png
  ├── chart5_disturbance_flow.png
  ├── chart6_heatmap.png
  └── chart7_longitudinal_profile.png
data/
  ├── result.csv          # 原始数据
  └── summary.json        # 统计摘要
```

### 报告结构

**Task Snapshot**：
- 开始时间、结束时间
- 时间步长、输出步长
- 仿真时长
- 场景 YAML ID
- 任务状态（中文显示，如"已完成"）

**完整曲线区块**：
- 必须配套文字解读
- 解释波动范围、重点对象、控制动作
- 提供建议关注点

**纵剖面区块**：
- 展示断面顶高程、底高程、水面线
- 水位阴影填充到底高程线
- 底高程阴影延伸到坐标轴底部
- 闸站位置用稳定虚线标识

**数据质量说明**：
- CSV 时间轴可靠性
- 数据完整性
- 缺失图表及影响范围

**后续建议动作**：
- 统一命名为"后续建议动作"

### 时间口径优先级

1. 用户显式参数（`total_steps` / `sim_step_size` / `output_step_size`）
2. 场景 YAML 配置
3. CSV 自身可靠字段（如非空的 `step_index`）
4. 仅展示离散采样序号

**禁止**：写死 `120 秒/步` 之类的默认值

### 图表缺失处理

如果以下图表缺失：
- `chart1_water_level.png`
- `chart2_water_flow.png`
- `chart4_gate_opening.png`
- `chart5_disturbance_flow.png`
- `chart6_heatmap.png`
- `chart7_longitudinal_profile.png`

**处理方式**：
1. 仍可交付 HTML 报告
2. 在报告正文显式写明缺失项
3. 说明缺失原因
4. 标注影响范围
5. 不要只在聊天回复里解释

## Markdown 报告规范

### 图表嵌入顺序

1. 水位时序图 → 2-3 句分析
2. 流量时序图 → 2-3 句分析
3. 闸门开度图 → 2-3 句分析
4. 分水口流量图 → 2-3 句分析
5. 热力图 → 2-3 句分析

### 文字分析要求

- 解释图中的关键发现和趋势
- 不要只列数据表格
- 每张图后紧跟分析说明

## 纵剖面生成

### 数据来源

**高程字段优先级**：
1. 显式的 `t_top_elevation` 和 `bottom_elevation`
2. 根据 `cross_section_geometry.data_points` 推导

**objects.yaml 获取要求**：
- 优先使用阶段二已缓存的本地 `objects.yaml`
- 如果需要远程回源，先对 URL 做编码规范化，兼容中文路径

### 页面要求

- x 轴和 y 轴根据实际数值范围自适应收紧
- 不使用过宽的固定范围
- 支持闸站展示和流向标识

### 闸站展示

当用户要求"增加闸站展示"或"展示水流流向"时：
- 展示闸站卡片
- 展示闸门组成
- 标识"上游 -> 下游"流向

## 拓扑可视化

### 数据来源

- 场景 YAML
- `hydros_objects_modeling_url` 指向的 `objects.yaml`

### 展示内容

- `connections` 关系
- 对象类型
- 关键节点关系

## 模板选择

### 选择报告模板
- 用户要求"报告页""汇报页""导出截图""完整曲线"
- 正式汇报需求
- 模板：`assets/hydros-report-template/index.html`

## 常见问题

### CSV 时间轴不可靠

**识别条件**：
- `data_index` 看起来只是输出序号
- `step_index` 为空
- `source_time` 出现异常未来时间

**处理方式**：
- 明确告诉用户"CSV 时间轴不可靠"
- 不要误解释成真实计算步号

### 占位零值处理

**识别位置**：
- `water_level`、`water_flow` 的首步

**处理方式**：
- 展示时剔除或明确说明
- 不要误判为真实异常或真实停流

### 参数与实际不一致

**场景**：
- 用户参数推导的总时长 ≠ CSV 覆盖的总时长

**处理方式**：
- 报告中明确写出两者差值
- 视为 CSV 导出问题或数据质量问题
- 在"异常与建议"或"数据质量"区块说明
