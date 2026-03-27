# Hydros HTML 界面 Prompt 参考

在以下场景读取本文件：

- 用户要“做个页面看仿真数据”
- 用户要 HTML 仪表板、分析工作台、结果面板、可视化页面
- 用户要拓扑图、场景拓扑、渠道拓扑、waterway 拓扑
- 用户要纵剖面、纵剖面图、水面线图
- 需要把仿真结果包装成更友好的交互式界面

## 这类前端 prompt 的稳定规律

把 prompt 写成“可执行规格”，而不是抽象描述。

推荐顺序：

1. 用一句话锁定页面目标、技术栈、风格
2. 定义设计系统与 token
3. 定义数据契约
4. 定义页面信息架构
5. 定义交互与图表映射
6. 定义实现约束与禁区

高质量 prompt 通常有三个特点：

- 低自由度：明确字体、颜色、间距、区块和状态
- 数据优先：先写数据结构，再写视觉
- 有禁区：明确不要营销 hero、不要假图、不要装饰性噪声

## Hydros 场景下的推荐视觉方向

目标不是“酷炫官网”，而是“友好但专业的工程分析台”。

建议：

- 风格：calm industrial / clean data dashboard
- 主色：蓝灰、中性色、少量青色或琥珀色强调
- 背景：浅色或深色都可以，但保持低噪音
- 字体：Inter / system-ui
- 卡片：中等圆角、轻阴影、弱边框
- 动效：只做轻量淡入和图表过渡

避免：

- 视频背景
- 巨大营销标语
- 过度玻璃拟态
- 与数据无关的装饰插画

## 推荐技术选择

### 单文件快速交付

适用于单独页面、演示稿、临时分析页：

- `index.html`
- Tailwind CDN
- ECharts CDN

优点：

- 快速
- 易分享
- 不依赖构建

### 集成到现有前端

适用于项目已有 React/Vite 时：

- React
- Tailwind CSS
- ECharts / Recharts

## 两种页面模式

### 工作台模式

适用于需要筛选、联动、对象钻取和长时间交互分析的场景。

优先复用：

- [../assets/hydros-dashboard-template/index.html](../assets/hydros-dashboard-template/index.html)

### 报告页模式

适用于汇报、截图、导出 PDF、归档和一次性结果复盘。

优先复用：

- [../assets/hydros-report-template/index.html](../assets/hydros-report-template/index.html)
- [../assets/hydros-report-template/report.data.js](../assets/hydros-report-template/report.data.js)

报告页模式建议采用 `index.html + report.data.js` 分离结构：

- `index.html` 负责排版、样式和图表渲染函数
- `report.data.js` 负责元数据、摘要文案、异常列表和完整曲线序列
- 接入真实结果时，优先只替换 `report.data.js`，不要把真实数据硬编码进 HTML
- 默认输出符合模板的完整版报告页，不要另起自定义轻量页、单页汇报版或仅摘要页。

## 推荐页面结构

### 报告页推荐结构

适合完整版报告页的顺序：

1. 顶部任务摘要与元信息
2. 执行摘要与关键结论
3. 完整曲线区
4. 异常与建议表
5. 风险等级
6. 后续动作
7. 样本明细

### 完整曲线要求

当用户明确要求“显示完整曲线，而不是关键曲线”时：

- 不要只挑代表对象，应该展示指定指标的全部序列
- 图例使用 `legend.type = "scroll"`
- 时间轴启用 `dataZoom`
- 水位和流量优先使用全宽大图，避免多图挤压
- 流量图保留 `0` 参考线
- 闸门开度优先使用阶梯线，突出平台期和切换点
- 系列过多时，优先通过滚动图例和缩放保留全量，不要擅自删线

### 1. 顶部任务概览栏

展示：

- 场景名称 / 场景 ID
- 任务 ID
- 任务状态
- 总步数
- 对象数
- 指标数

### 2. 左侧筛选器

至少包含：

- `object_type`
- `metrics_code`
- `object_name`
- 仅看异常对象

### 3. 主时序图

用于展示当前筛选条件下的主要变化趋势。

### 4. 对象对比区

用于比较多个断面、闸门或分水口的同类指标。

### 5. 异常检测面板

至少列出：

- 负流量
- 异常波动
- 零流量
- 数据缺失

### 6. 状态时间线

展示：

```text
INIT -> WAITING_AGENTS -> READY -> STEPPING -> COMPLETED/FAILED
```

### 7. 原始数据表

支持搜索、分页或折叠。

## 推荐 prompt 模板

```text
创建一个单页 hydros 水力仿真分析 HTML 工作台，用于分析仿真任务输出数据。界面需要友好、专业、可读，优先服务工程分析而不是营销展示。

Tech:
- 单文件 HTML
- Tailwind CSS CDN
- ECharts
- 不依赖构建工具
- 接收一个 timeseriesData 数组和一个 simMeta 对象作为输入

1. Data Contract
timeseriesData 的每条记录包含:
object_name, object_type, metrics_code, data_index, value, tenant_id, waterway_id
simMeta 包含:
biz_scene_instance_id, biz_scenario_id, total_steps, task_status, default_render_objects

2. Design System
- 视觉风格: calm industrial, clean data dashboard
- 不要视频背景，不要营销 hero
- 使用 HSL CSS variables 定义 semantic tokens:
  --background, --foreground, --card, --muted, --border, --primary, --success, --warning, --danger
- 圆角 16px，卡片有轻微阴影，hover 克制
- 字体: Inter / system-ui

3. Layout
- 顶部: 仿真任务概览栏
- 左侧: 筛选面板
- 主区:
  A. 摘要卡片
  B. 主时序图
  C. 对象对比图
  D. 异常检测表
  E. SSE/状态时间线
  F. 原始数据表

4. Visualization Rules
- water_level: 蓝色系平滑折线
- water_flow: 青色折线，0 线明显
- 负流量区域和点使用 danger 色高亮
- gate/opening 类指标使用阶梯图或柱状图
- tooltip 显示 object_name、metrics_code、step、value

5. Interaction Rules
- 默认先展示全局概览
- 点击对象时，主图、摘要卡、异常表联动
- 支持切换“按对象看”与“按指标看”
- 支持只看异常对象
- 支持导出当前筛选结果为 CSV

6. Implementation Constraints
- 所有图表必须由真实数据聚合生成
- 所有颜色来自 semantic tokens
- 所有模块必须有空状态、加载状态、无异常状态
- 代码拆成小函数: aggregateMetrics, buildAnomalyList, buildSeries, renderSummaryCards
```

## 报告页 prompt 模板

```text
创建一个 hydros 仿真分析报告 HTML，用于汇报和复盘真实仿真结果。页面应偏报告而不是工作台，适合直接截图、导出 PDF 或归档，并且必须对齐 hydros-report-template/index.html 的完整版结构。

Tech:
- 单文件 HTML
- Tailwind CSS CDN
- ECharts
- 外部数据文件 report.data.js

目标:
- 展示真实仿真结果，不要营销 hero
- 展示完整曲线，而不是只展示关键曲线
- 默认支持较多序列时的滚动图例和时间缩放

页面结构:
- 顶部摘要区: 任务标题、场景、任务 ID、状态、记录数、对象数
- 执行摘要: 结论段落 + 关键 bullet
- 完整曲线区:
  - 完整 water_level 曲线
  - 完整 water_flow 曲线
  - 完整 gate_opening 曲线
- 异常与建议表
- 风险等级条
- 后续动作
- 样本明细

Visualization Rules:
- water_level: 多条折线，全量展示
- water_flow: 多条折线，全量展示，保留 0 参考线，负流量曲线可加粗
- gate_opening: 多条阶梯线，全量展示
- legend 使用 scroll
- x 轴启用 dataZoom
- tooltip 显示对象名、指标、step、value

Implementation Constraints:
- 模板使用 index.html + report.data.js 分离结构
- HTML 里不要硬编码真实数据
- 报告文案、异常表和图表都由 payload 驱动
- 所有模块必须有数据为空时的兜底提示
- 不要实现自定义轻量页、单页汇报版或摘要页来替代模板完整版
```

## 写 prompt 时最重要的三条

- 先把输入数据说清楚，再谈图表和布局
- 先把工程分析任务说清楚，再谈“好看”
- 明确禁区，减少模型走向营销页的概率

## 交付优先级建议

### 第一优先级

- 可读的摘要卡片
- 稳定的时序图
- 清晰的异常表

### 第二优先级

- 联动筛选
- 状态时间线
- 原始数据表

### 第三优先级

- 导出
- 主题切换
- 细致动画

## 快速交付建议

如果用户只要一个能打开就看的页面：

1. 直接复用 [../assets/hydros-dashboard-template/index.html](../assets/hydros-dashboard-template/index.html)
2. 把真实数据挂到 `window.HYDROS_TIMESERIES_DATA` 和 `window.HYDROS_SIM_META`
3. 再做少量样式和字段定制

如果用户要“可汇报的结果页”：

1. 直接复用 [../assets/hydros-report-template/index.html](../assets/hydros-report-template/index.html)
2. 替换同目录 [../assets/hydros-report-template/report.data.js](../assets/hydros-report-template/report.data.js)
3. 优先保留完整曲线、滚动图例和 `dataZoom`
4. 不要缩减成单页轻量汇报版，默认保持模板完整版结构

## 拓扑页模式补充

适用于用户明确要“场景拓扑”“waterway 拓扑”“渠道拓扑图”的场景。

推荐数据来源：

- 场景 YAML
- `hydros_objects_modeling_url` 指向的 `objects.yaml`
- `connections`
- 闸站、闸门、断面等对象定义

推荐页面结构：

1. 顶部工程摘要与 waterway 元信息
2. 主链路拓扑图
3. 节点图例
4. 右侧节点详情
5. 连接关系表
6. 闸站和闸门组成表

推荐实现要点：

- 主渠、倒虹吸、分水/退水节点、闸站、闸门使用不同颜色
- 主链路方向明确，连接箭头从上游指向下游
- 闸站节点要突出展示，并支持查看内部闸门组成
- 所有节点和连接都来自真实配置，不使用假数据占位

## 纵剖面页模式补充

适用于用户明确要“纵剖面”“水面线图”“沿程剖面”的场景。

推荐数据来源：

- `objects.yaml` 中断面的 `location`
- `objects.yaml` 中断面的 `bottom_elevation`
- 仿真结果中的 `water_level`
- 可选的闸站位置、闸门组成和关键断面标签

推荐页面结构：

1. 顶部摘要与流向说明
2. 沿程纵剖面主图
3. 闸站展示卡片
4. 关键观察与工程解释
5. 断面明细表

推荐实现要点：

- 底高程使用棕色或深色折线
- 水位线使用蓝色折线
- 明确显示“上游 -> 下游”方向
- 闸站位置用垂线或标签标注
- 如果用户要求，额外展示水流流向条和闸站说明卡

## 拓扑页 prompt 模板

```text
创建一个 hydros 场景拓扑 HTML 页面，用于展示 waterway 的主链路、关键节点和连接关系。页面应偏工程可视化，不要做成营销页。

Tech:
- 单文件 HTML
- Tailwind CSS CDN
- 原生 SVG 或 Canvas 绘制拓扑
- 不依赖构建工具

Data Contract:
- 输入包含场景 YAML、objects.yaml 解析结果
- 至少包含对象列表、对象类型、connections、闸站与闸门映射

页面结构:
- 顶部摘要区: waterway 名称、场景 ID、主链路长度、节点数
- 主拓扑图区: 按上游到下游排列主链路
- 右侧详情区: 点击节点展示类型、名称、上下游关系
- 下方表格区: 连接关系表、闸站组成表

Visualization Rules:
- 主渠、倒虹吸、分水/退水节点、闸站、闸门使用不同颜色
- 主链路方向明确，连接箭头从上游指向下游
- 闸站节点要突出展示，并支持查看内部闸门组成
- 不使用假数据占位，所有节点和连接都来自真实配置
```

## 纵剖面页 prompt 模板

```text
创建一个 hydros 纵剖面 HTML 页面，用于展示沿程断面底高程、水位线、闸站位置和水流流向。页面应清晰、专业、适合工程解释和截图汇报。

Tech:
- 单文件 HTML
- Tailwind CSS CDN
- ECharts
- 不依赖构建工具

Data Contract:
- 输入包含断面 location、bottom_elevation
- 可选叠加仿真结果中的 water_level
- 可选输入闸站位置、闸门组成和关键断面标签

页面结构:
- 顶部摘要区: waterway 名称、时刻、断面数、流向说明
- 主图: 底高程线 + 水位线
- 闸站区: ZM1/ZM2 等闸站卡片，展示入口断面和闸门组成
- 观察区: 关键坡降、最大水深、最小水深、异常说明
- 明细区: 断面表

Visualization Rules:
- 底高程使用棕色或深色折线
- 水位线使用蓝色折线
- 明确显示“上游 -> 下游”方向
- 闸站位置用垂线或标签标注
- 如果用户要求，额外展示水流流向条和闸站说明卡
```
