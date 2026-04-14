# Hydros 数据契约与分析口径

在以下场景读取本文件：

- 需要把原始时序数据转成图表、摘要卡片或异常表。
- 需要生成 HTML 报告、Markdown 报告或 Word 报告。
- 需要解释 `object_type`、`metrics_code`、`data_index` 的含义。

## 原始记录结构

典型记录：

```json
{
  "object_name": "QD-5#断面#001",
  "object_type": "CrossSection",
  "metrics_code": "water_level",
  "data_index": 1,
  "value": 62.86,
  "tenant_id": "1111",
  "waterway_id": "50"
}
```

字段解释：

- `object_name`
  对象名称。图表图例、表格主键和筛选器都优先使用它。
- `object_type`
  对象类型。常见值：
  - `CrossSection`：断面
  - `Gate`：闸门
  - `DisturbanceNode`：分水口/退水闸
- `metrics_code`
  指标编码。决定图表类型和异常规则。
- `data_index`
  优先视为离散时间轴。但在部分结果文件导出里，它可能是“输出序号”而不是真实计算步号；如果用户显式给出了 `total_steps`、`sim_step_size`、`output_step_size`，必须优先使用这些参数作为时间口径，不要写死默认值。仿真覆盖总时长固定按 `total_steps * output_step_size` 推导，不使用 `sim_step_size`。
- `step_index`
  计算时间步。若存在且非空，优先把它视为真实计算步号。若整列为空，不要强行拿它推时间轴。
- `source_time`
  源业务时间。仅当值看起来是正常业务时间时才使用。若出现明显异常时间（例如远未来年份），应判定该字段不可靠并退回到参数口径或离散步号展示。
- `value`
  指标值。
- `tenant_id`
  租户标识。
- `waterway_id`
  所属河道/渠段。

## 推荐聚合维度

### 1. 全局摘要

用于顶部卡片或报告摘要：

- 总记录数
- 总时间步数
- 对象数
- 指标数
- 异常数

计算建议：

- 时间步数：`data_index` 去重计数
- 对象数：`object_name` 去重计数
- 指标数：`metrics_code` 去重计数

### 2. 对象维度

适合做对象排行榜、对象详情和对比视图：

- 每个对象的数据点数
- 每个对象的指标列表
- 每个对象每个指标的最小值、最大值、均值、波动范围

建议主键：

```text
object_name + metrics_code
```

### 3. 指标维度

适合做概览图、热力图和异常聚焦：

- `water_level`
- `water_flow`
- `gate_opening` 或同类开度指标
- 其他场景特定指标

## 推荐图表映射

### water_level

- 主图：折线图
- 对比：多对象折线图
- 空间分布：热力图

### water_flow

- 主图：折线图或面积图
- 关键要求：必须显示 0 参考线
- 异常高亮：负流量使用 danger 色

### gate/opening 类指标

- 主图：阶梯图或柱状图
- 对比：多闸门并列柱状图

## 推荐异常规则

### 高优先级

- 负流量（倒流）
  规则：`metrics_code` 属于流量指标且 `value < 0`
- 水位异常波动
  规则：相邻步差值超过默认阈值

### 中优先级

- 零流量节点
  规则：整个序列恒为 0
- 恒定流量
  规则：方差为 0 或低于阈值

### 低优先级

- 数据缺失
  规则：对象在部分 `data_index` 缺少记录

## 展示语言建议

- 对用户展示中文标签。
- 保留原始编码用于 tooltip 或详情。
- 同时展示中文说明和原始 code 时，优先格式：

```text
水位 (water_level)
流量 (water_flow)
闸门 (Gate)
断面 (CrossSection)
```

## 输出前检查

- 是否先确认任务已 `COMPLETED`
- 是否说明当前数据来源：MCP 拉取或本地文件
- 是否优先使用用户显式提供的 `total_steps`、`sim_step_size`、`output_step_size`
- 是否按 `total_steps * output_step_size` 推导仿真覆盖总时长，且没有误用 `sim_step_size`
- 是否比较了“按参数推导的期望总时长 / 期望输出点数”和“结果文件实际可覆盖时长 / 实际采样点数”
- 当结果文件中的 `data_index`、`step_index`（计算时间步）、`source_time`（源业务时间）互相矛盾时，是否明确标注“时间轴无法可靠还原”
- 是否在图表里区分正常波动和异常点
- 是否给出“下一步建议”，例如换场景、缩短步数或检查 tenant
