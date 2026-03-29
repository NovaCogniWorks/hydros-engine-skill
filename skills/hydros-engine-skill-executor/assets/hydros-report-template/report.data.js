window.HYDROS_REPORT_DATA = {
  csvPath: "/path/to/timeseries_data.csv",
  meta: {
    report_title: "Hydros 仿真分析报告",
    biz_scene_instance_id: "SIM-DEMO-001",
    biz_scenario_id: "100001",
    tenant_id: "demo-tenant",
    task_status: "COMPLETED",
    total_steps: 180,
    completed_at: "2026-03-22T21:40:00+08:00",
    analyst: "Codex / Hydros Simulation",
    record_count: 2160,
    object_count: 8,
    metric_count: 3,
    negative_flow_points: 42,
    negative_flow_objects: 1,
    zero_flow_objects: 0,
    water_level_series_count: 3,
    water_flow_series_count: 3,
    gate_series_count: 2,
  },
  metaCards: [
    { label: "任务状态", value: "COMPLETED" },
    { label: "总步数", value: 180 },
    { label: "对象数量", value: 8 },
    { label: "异常条目", value: 3 },
  ],
  headlineCards: [
    {
      eyebrow: "执行结论",
      title: "总体稳定",
      body: "示例数据完整，整体趋势平滑，仅有局部倒流点需要进一步复核。",
    },
    {
      eyebrow: "最高优先级问题",
      title: "局部倒流",
      body: "QD-3#断面#001 在中段出现短时负流量，建议复核边界条件。",
    },
    {
      eyebrow: "总体稳定性",
      title: "良好",
      body: "水位曲线连续，闸门响应呈阶梯变化，适合用于报告模板演示。",
    },
    {
      eyebrow: "建议动作",
      title: "继续复核",
      body: "替换 report.data.js 后可直接生成真实汇报页或截图页。",
    },
  ],
  summaryParagraph:
    "该示例用于演示单页报告模板的数据结构。真实接入时，建议保留 index.html 不变，只替换同目录 report.data.js，并传入完整 water_level、water_flow、gate_opening 序列。",
  summaryBullets: [
    "模板采用 index.html + report.data.js 分离模式，便于替换真实仿真结果。",
    "报告页适合汇报、截图、导出 PDF，不适合重交互筛选场景。",
    "完整曲线场景建议启用可滚动图例和 dataZoom，避免全量序列挤压可读性。",
    "当存在负流量时，建议保留 0 参考线并提高异常曲线线宽。",
    "闸门开度建议使用阶梯线，突出平台期与策略切换点。",
  ],
  anomalies: [
    {
      priority: "高",
      object: "QD-3#断面#001",
      metric: "water_flow",
      finding: "60-90 步出现短时负流量，最小值 -1.8。",
      advice: "复核该断面的边界条件和上下游方向定义。",
    },
    {
      priority: "中",
      object: "ZM1-节制闸#1",
      metric: "gate_opening",
      finding: "120 步后开度从 0.62 阶跃到 0.92。",
      advice: "检查控制策略是否过于粗粒度。",
    },
    {
      priority: "低",
      object: "QD-1#断面#001",
      metric: "water_level",
      finding: "水位整体平滑上升，累计抬升约 1.2m。",
      advice: "可结合更多断面做沿程联动复核。",
    },
  ],
  actions: [
    "替换 report.data.js 中的 meta、摘要、异常和图表序列。",
    "接入真实数据时，优先保持图表字段结构稳定，不要改模板函数签名。",
    "如果序列很多，继续使用可滚动图例和 dataZoom，而不是删减曲线。",
    "如需扩展页面能力，优先在报告模板基础上做专题区块，而不是混入重型交互。",
  ],
  riskBars: [
    { label: "倒流风险", value: 38 },
    { label: "控制滞后", value: 44 },
    { label: "总体稳定性", value: 76 },
  ],
  miniTable: [
    { object_name: "QD-1#断面#001", metrics_code: "water_level", data_index: 1, value: 82.1 },
    { object_name: "QD-1#断面#001", metrics_code: "water_level", data_index: 60, value: 82.42 },
    { object_name: "QD-3#断面#001", metrics_code: "water_flow", data_index: 72, value: -1.8 },
    { object_name: "ZM1-节制闸#1", metrics_code: "gate_opening", data_index: 120, value: 0.92 },
    { object_name: "QD-2#断面#001", metrics_code: "water_flow", data_index: 180, value: 11.4 },
    { object_name: "ZM2-节制闸#1", metrics_code: "gate_opening", data_index: 180, value: 0.88 },
  ],
  charts: {
    levelSeries: [
      {
        name: "QD-1#断面#001",
        objectType: "CrossSection",
        data: [[1, 82.1], [30, 82.22], [60, 82.42], [90, 82.66], [120, 82.93], [150, 83.15], [180, 83.3]],
      },
      {
        name: "QD-2#断面#001",
        objectType: "CrossSection",
        data: [[1, 81.96], [30, 82.08], [60, 82.29], [90, 82.51], [120, 82.76], [150, 82.98], [180, 83.14]],
      },
      {
        name: "ZM2-入口断面",
        objectType: "CrossSection",
        data: [[1, 81.82], [30, 81.95], [60, 82.17], [90, 82.4], [120, 82.65], [150, 82.87], [180, 83.03]],
      },
    ],
    flowSeries: [
      {
        name: "QD-1#断面#001",
        objectType: "CrossSection",
        minValue: 8.6,
        data: [[1, 9.1], [30, 9.4], [60, 9.9], [90, 10.4], [120, 10.9], [150, 11.2], [180, 11.6]],
      },
      {
        name: "QD-2#断面#001",
        objectType: "CrossSection",
        minValue: 6.4,
        data: [[1, 8.2], [30, 8.5], [60, 8.9], [90, 9.3], [120, 9.8], [150, 10.2], [180, 10.7]],
      },
      {
        name: "QD-3#断面#001",
        objectType: "CrossSection",
        minValue: -1.8,
        data: [[1, 6.8], [30, 6.2], [60, 1.4], [72, -1.8], [90, 0.6], [120, 4.7], [150, 5.6], [180, 6.3]],
      },
    ],
    gateSeries: [
      {
        name: "ZM1-节制闸#1",
        objectType: "Gate",
        range: 0.3,
        data: [["Step 1", 0.62], ["Step 119", 0.62], ["Step 120", 0.92], ["Step 180", 0.92]],
      },
      {
        name: "ZM2-节制闸#1",
        objectType: "Gate",
        range: 0.22,
        data: [["Step 1", 0.66], ["Step 80", 0.66], ["Step 81", 0.88], ["Step 180", 0.88]],
      },
    ],
  },
};
