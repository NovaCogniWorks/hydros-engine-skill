from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_anomalies import detect_negative_flow, group_by_object_metric
from lib.timeseries_loader import add_series_identity_columns, normalize_record


class StationSideSeriesTest(unittest.TestCase):
    def test_gate_station_upstream_and_downstream_are_distinct_series(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "object_name": "深溪沟站(4机+3闸)",
                    "object_type": "GateStation",
                    "metrics_code": "water_level",
                    "position_code": "up_stream",
                    "value": 842.572,
                    "front_water_level": 842.572,
                    "back_water_level": None,
                },
                {
                    "object_name": "深溪沟站(4机+3闸)",
                    "object_type": "GateStation",
                    "metrics_code": "water_level",
                    "position_code": "down_stream",
                    "value": 616.389,
                    "front_water_level": None,
                    "back_water_level": 616.389,
                },
            ]
        )

        result = add_series_identity_columns(frame)

        self.assertEqual(["front", "back"], result["series_side"].tolist())
        self.assertEqual(
            ["深溪沟站(4机+3闸)（前侧）", "深溪沟站(4机+3闸)（后侧）"],
            result["series_name"].tolist(),
        )

    def test_anomaly_groups_do_not_mix_station_sides(self) -> None:
        records = [
            normalize_record(
                {
                    "object_name": "深溪沟站",
                    "object_type": "GateStation",
                    "metrics_code": "water_level",
                    "position_code": position,
                    "data_index": step,
                    "value": value,
                }
            )
            for step, position, value in [
                (0, "up_stream", 842.5),
                (0, "down_stream", 616.4),
                (1, "up_stream", 842.8),
                (1, "down_stream", 616.5),
            ]
        ]

        groups = group_by_object_metric(records)

        self.assertEqual(2, len(groups))
        self.assertEqual([842.5, 842.8], [value for _, value in groups[("深溪沟站（前侧）", "water_level", "GateStation")]])
        self.assertEqual([616.4, 616.5], [value for _, value in groups[("深溪沟站（后侧）", "water_level", "GateStation")]])

    def test_persistent_negative_flow_is_classified(self) -> None:
        groups = {
            ("PS1", "water_flow", "CrossSection"): [
                (0, 2.0),
                (1, -3.0),
                (2, -4.0),
                (3, -2.0),
                (4, 1.0),
            ]
        }

        issue = detect_negative_flow(groups)[0]

        self.assertEqual("持续性倒流", issue["pattern"])
        self.assertEqual(3, issue["longest_negative_run"])
        self.assertEqual(2, issue["sign_changes"])


if __name__ == "__main__":
    unittest.main()
