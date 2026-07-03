#!/usr/bin/env python3
"""
基于 objects.yaml 与仿真结果文件生成纵剖面 HTML 页面。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

import pandas as pd
from lib.timeseries_loader import load_timeseries_dataframe
from lib.url_utils import normalize_remote_url

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

if plt is not None:
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti TC", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False


def fetch_objects_yaml(objects_url: str) -> str:
    with urllib.request.urlopen(normalize_remote_url(objects_url), timeout=20) as response:
        return response.read().decode("utf-8")


def load_objects_yaml(
    objects_yaml_path: Path | None = None,
    objects_yaml_url: str | None = None,
) -> str:
    if objects_yaml_path is not None:
        return objects_yaml_path.read_text(encoding="utf-8")
    if objects_yaml_url:
        return fetch_objects_yaml(objects_yaml_url)
    raise ValueError("缺少 objects.yaml 来源，请显式传入 objects_yaml_path 或 objects_yaml_url")


def gate_sort_key(name: str) -> tuple[str, int, str]:
    station = name.split("-", 1)[0]
    match = re.search(r"#(\d+)", name)
    gate_number = int(match.group(1)) if match else 999
    return (station, gate_number, name)


def parse_cross_section_children(block: str) -> list[dict]:
    refs = []
    for child_block in re.split(r"(?m)^\s+-\s*$", block):
        if not child_block.strip():
            continue
        role_match = re.search(r"(?m)^\s+role:\s*(.+?)\s*$", child_block)
        id_match = re.search(r"(?m)^\s+id:\s*(\d+)\s*$", child_block)
        name_match = re.search(r"(?m)^\s+name:\s*(.+?)\s*$", child_block)
        if not id_match and not name_match:
            continue
        refs.append(
            {
                "role": role_match.group(1).strip().upper() if role_match else "",
                "id": int(id_match.group(1)) if id_match else None,
                "name": name_match.group(1).strip() if name_match else "",
            }
        )
    return refs


def pick_role_ref(refs: list[dict], role: str, fallback_index: int) -> dict | None:
    role = role.upper()
    for item in refs:
        if item.get("role") == role:
            return item
    if not refs:
        return None
    try:
        return refs[fallback_index]
    except IndexError:
        return refs[-1]


def parse_object_locations(text: str) -> dict[str, float]:
    locations = {}
    blocks = split_object_blocks(text)
    for block in blocks:
        if not block.strip():
            continue
        name = extract_block_value(block, "name")
        if not name:
            continue
        location_match = re.search(r'\n    location:\s*([-\d.]+)', extract_nested_block(block, "parameters"))
        if location_match:
            locations[name] = float(location_match.group(1))
    return locations


def parse_gate_stations(text: str) -> list[dict]:
    stations = []
    blocks = split_object_blocks(text)
    for block in blocks:
        if not block.strip():
            continue
        if extract_block_value(block, "type") != "GateStation":
            continue

        name = extract_block_value(block, "name")
        if not name:
            continue
        short_name = name.split('-')[0] if '-' in name else name

        child_blocks = extract_nested_block(block, "cross_section_children")
        section_refs = parse_cross_section_children(child_blocks)
        inlet_ref = pick_role_ref(section_refs, "INLET", 0) or {}
        outlet_ref = pick_role_ref(section_refs, "OUTLET", -1) or {}
        section_names = [item["name"] for item in section_refs if item.get("name")]

        gates = []
        device_blocks = extract_nested_block(block, "device_children")
        if device_blocks:
            gates = [gn.strip() for gn in re.findall(r'(?m)^\s+name:\s*(.+?)\s*$', device_blocks)]

        stations.append({
            "name": name,
            "short_name": short_name,
            "inlet_section": inlet_ref.get("name", ""),
            "outlet_section": outlet_ref.get("name", ""),
            "inlet_section_id": inlet_ref.get("id"),
            "outlet_section_id": outlet_ref.get("id"),
            "section_names": section_names,
            "section_refs": section_refs,
            "gates": gates,
            "role": f"{name}控制点"
        })
    return stations


def parse_cross_sections(text: str) -> list[dict]:
    sections = []
    for source_index, block in enumerate(split_cross_section_blocks(text)):
        if extract_block_value(block, "type") != "CrossSection":
            continue
        object_id = extract_block_value(block, "id")
        object_name = extract_block_value(block, "name")
        if not object_id or not object_name:
            continue
        body = extract_nested_block(block, "parameters")
        top_match = re.search(r"\n    (?:t_top_elevation|top_elevation):\s*(?P<value>[-\d.]+)", body)
        bottom_match = re.search(r"\n    bottom_elevation:\s*(?P<value>[-\d.]+)", body)
        location_match = re.search(r"\n    location:\s*(?P<value>[-\d.]+)", body)
        identity_role_match = re.search(r"\n    identity_role:\s*(?P<value>.+?)\s*$", body, re.MULTILINE)
        point_elevations = [
            float(value)
            for value in re.findall(r"\n\s*-\s*\n\s*-\s*[-\d.]+\n\s*-\s*([-\d.]+)", body)
        ]
        if not location_match:
            continue
        if not bottom_match and not point_elevations:
            continue

        bottom_elevation = float(bottom_match.group("value")) if bottom_match else min(point_elevations)
        if top_match:
            top_elevation = float(top_match.group("value"))
        elif point_elevations:
            top_elevation = max(point_elevations)
        else:
            top_elevation = bottom_elevation
        sections.append(
            {
                "id": int(object_id),
                "name": object_name,
                "bottom_elevation": bottom_elevation,
                "top_elevation": top_elevation,
                "location": float(location_match.group("value")),
                "identity_role": identity_role_match.group("value").strip() if identity_role_match else "",
                "source_index": source_index,
            }
        )
    return sections


def normalize_section_locations(sections: list[dict]) -> list[dict]:
    normalized = [dict(item) for item in sections]

    normalized.sort(key=lambda item: (float(item["location"]), int(item.get("source_index", 0))))
    return normalized


def collect_section_errors(sections: list[dict]) -> list[dict]:
    errors = []
    for item in sections:
        top_elevation = float(item["top_elevation"])
        bottom_elevation = float(item["bottom_elevation"])
        if abs(top_elevation - bottom_elevation) >= 1e-6:
            continue
        errors.append(
            {
                "type": "equal_top_bottom_elevation",
                "severity": "error",
                "section_id": item["id"],
                "section_name": item["name"],
                "location": round(float(item["location"]) / 1000, 3),
                "top_elevation": round(top_elevation, 3),
                "bottom_elevation": round(bottom_elevation, 3),
                "message": "断面顶高程与底高程相等，不符合断面数据约束；该断面未参与剖面线绘制",
            }
        )
    return errors


def select_profile_sections_from_objects(
    text: str,
    sections: list[dict],
    invalid_section_ids: set[int] | None = None,
    invalid_section_names: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    invalid_section_ids = invalid_section_ids or set()
    invalid_section_names = invalid_section_names or set()
    sections_by_id = {item["id"]: item for item in sections}
    sections_by_name = {item["name"]: item for item in sections}
    selected_by_id: dict[int, dict] = {}
    errors: list[dict] = []

    for block in split_object_blocks(text):
        object_type = extract_block_value(block, "type")
        object_name = extract_block_value(block, "name")
        if not object_type or not object_name:
            continue

        child_block = extract_nested_block(block, "cross_section_children")
        child_refs = parse_cross_section_children(child_block)
        for ref in child_refs:
            role = ref.get("role", "")
            if role not in {"INLET", "OUTLET"}:
                continue
            section_id = ref.get("id")
            section_name = ref.get("name", "")
            section = sections_by_id.get(section_id) if section_id is not None else None
            matched_by = "id"
            if section is None and section_name:
                section = sections_by_name.get(section_name)
                matched_by = "name"
            if section is None:
                errors.append(
                    {
                        "type": "missing_profile_section_ref",
                        "severity": "warning",
                        "object_name": object_name,
                        "object_type": object_type,
                        "role": role,
                        "section_id": section_id,
                        "section_name": section_name,
                        "message": "对象 INLET/OUTLET 引用的断面未在 cross_sections 中找到，已跳过该断面引用",
                    }
                )
                continue
            if section.get("identity_role") == "source_duplicate":
                errors.append(
                    {
                        "type": "source_duplicate_profile_section_ref",
                        "severity": "warning",
                        "object_name": object_name,
                        "object_type": object_type,
                        "role": role,
                        "section_id": section_id,
                        "section_name": section_name,
                        "message": "对象 INLET/OUTLET 引用的断面标记为 source_duplicate，已跳过该断面引用",
                    }
                )
                continue
            if section["id"] in invalid_section_ids or section["name"] in invalid_section_names:
                errors.append(
                    {
                        "type": "invalid_profile_section_ref",
                        "severity": "warning",
                        "object_name": object_name,
                        "object_type": object_type,
                        "role": role,
                        "section_id": section_id,
                        "section_name": section_name,
                        "message": "对象 INLET/OUTLET 引用的断面存在数据错误，已跳过该断面引用",
                    }
                )
                continue
            if matched_by == "name":
                errors.append(
                    {
                        "type": "profile_section_ref_name_fallback",
                        "severity": "warning",
                        "object_name": object_name,
                        "object_type": object_type,
                        "role": role,
                        "section_id": section_id,
                        "section_name": section_name,
                        "matched_section_id": section["id"],
                        "message": "对象 INLET/OUTLET 引用缺少有效断面 id，已按 name 回退匹配",
                    }
                )
            selected_by_id.setdefault(section["id"], section)

    selected = normalize_section_locations(list(selected_by_id.values()))
    if len(selected) < 3:
        errors.append(
            {
                "type": "insufficient_profile_sections",
                "severity": "error",
                "section_count": len(selected),
                "message": "从 objects 的 INLET/OUTLET 引用中提取的有效断面不足 3 个，无法生成可靠纵剖面",
            }
        )
    return selected, errors


def extract_section(text: str, section_name: str) -> str:
    marker = f"\n{section_name}:\n"
    start = text.find(marker)
    if start == -1:
        marker = f"{section_name}:\n"
        start = text.find(marker)
    if start == -1:
        return ""
    return text[start + len(marker):]


def split_object_blocks(text: str) -> list[str]:
    section = extract_section(text, "objects")
    if not section:
        return []
    return [block for block in re.split(r"(?m)^ -\s*$", section) if block.strip()]


def split_cross_section_blocks(text: str) -> list[str]:
    section = extract_section(text, "cross_sections")
    if not section:
        return []
    return [block for block in re.split(r"(?m)^ -\s*$", section) if block.strip()]


def extract_block_value(block: str, key: str) -> str | None:
    match = re.search(rf"(?m)^  {re.escape(key)}:\s*(.*?)\s*$", block)
    return match.group(1).strip() if match else None


def extract_nested_block(block: str, key: str) -> str:
    lines = block.splitlines()
    nested_lines: list[str] = []
    capture = False
    for line in lines:
        if re.match(rf"^  {re.escape(key)}:\s*$", line):
            capture = True
            continue
        if capture:
            if re.match(r"^  [A-Za-z_][A-Za-z0-9_]*:\s*", line):
                break
            nested_lines.append(line)
    return "\n".join(nested_lines)


def parse_scalar_parameters(block: str) -> list[dict[str, str]]:
    parameters: list[dict[str, str]] = []
    parameters_block = extract_nested_block(block, "parameters")
    for line in parameters_block.splitlines():
        match = re.match(r"^    ([A-Za-z_][A-Za-z0-9_]*):\s*(.+?)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        if key == "location":
            continue
        parameters.append({"key": key, "value": value})
    return parameters


def parse_object_annotations(
    text: str,
    sections: list[dict],
    invalid_section_ids: set[int] | None = None,
    invalid_section_names: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    invalid_section_ids = invalid_section_ids or set()
    invalid_section_names = invalid_section_names or set()
    sections_by_id = {item["id"]: item for item in sections}
    sections_by_name = {item["name"]: item for item in sections}
    annotations: list[dict] = []
    object_errors: list[dict] = []

    for block in split_object_blocks(text):
        object_type = extract_block_value(block, "type")
        object_name = extract_block_value(block, "name")
        if not object_type or not object_name:
            continue

        params = parse_scalar_parameters(block)
        param_summary = " · ".join(f"{item['key']}={item['value']}" for item in params)

        if object_type == "DisturbanceNode":
            location_match = re.search(r"(?m)^    location:\s*([-\d.]+)\s*$", extract_nested_block(block, "parameters"))
            if not location_match:
                continue
            annotations.append(
                {
                    "mode": "point",
                    "name": object_name,
                    "type": object_type,
                    "location": round(float(location_match.group(1)) / 1000, 3),
                    "params": params,
                    "param_summary": param_summary,
                }
            )
            continue

        child_block = extract_nested_block(block, "cross_section_children")
        child_refs = parse_cross_section_children(child_block)
        child_ids = [item["id"] for item in child_refs if item.get("id") is not None]
        child_names = [item["name"] for item in child_refs if item.get("name")]
        if not child_refs or (not child_ids and not child_names):
            if object_type == "Pipe":
                object_errors.append(
                    {
                        "type": "invalid_pipe_range",
                        "severity": "error",
                        "object_name": object_name,
                        "object_type": object_type,
                        "message": "倒虹吸/管道缺少首尾断面，未参与剖面对象绘制",
                    }
                )
            continue

        start_ref = pick_role_ref(child_refs, "INLET", 0) or {}
        end_ref = pick_role_ref(child_refs, "OUTLET", -1) or {}
        start_section_id = start_ref.get("id")
        end_section_id = end_ref.get("id")
        start_section_name = start_ref.get("name", "")
        end_section_name = end_ref.get("name", "")
        start_section = (
            sections_by_id.get(start_section_id)
            if start_section_id is not None
            else None
        ) or sections_by_name.get(start_section_name)
        end_section = (
            sections_by_id.get(end_section_id)
            if end_section_id is not None
            else None
        ) or sections_by_name.get(end_section_name)
        if object_type == "Pipe":
            invalid_reasons = []
            if not start_section:
                reason = "起点断面无效或缺失"
                if start_section_id in invalid_section_ids or start_section_name in invalid_section_names:
                    reason = "起点断面存在数据错误"
                invalid_reasons.append(reason)
            if not end_section:
                reason = "终点断面无效或缺失"
                if end_section_id in invalid_section_ids or end_section_name in invalid_section_names:
                    reason = "终点断面存在数据错误"
                invalid_reasons.append(reason)
            if start_section and end_section:
                start_location = round(start_section["location"] / 1000, 3)
                end_location = round(end_section["location"] / 1000, 3)
                if abs(start_location) < 1e-6:
                    invalid_reasons.append("起点断面里程为0")
                if abs(end_location) < 1e-6:
                    invalid_reasons.append("终点断面里程为0")
                if abs(start_location - end_location) < 1e-6:
                    invalid_reasons.append("首尾断面里程相同")
            if invalid_reasons:
                object_errors.append(
                    {
                        "type": "invalid_pipe_range",
                        "severity": "error",
                        "object_name": object_name,
                        "object_type": object_type,
                        "start_section_id": start_section_id,
                        "end_section_id": end_section_id,
                        "start_section_name": start_section_name,
                        "end_section_name": end_section_name,
                        "start_location": round(start_section["location"] / 1000, 3) if start_section else None,
                        "end_location": round(end_section["location"] / 1000, 3) if end_section else None,
                        "message": f"倒虹吸/管道范围无效（{'、'.join(invalid_reasons)}），未参与剖面对象绘制",
                    }
                )
                continue
        elif not start_section or not end_section:
            continue

        start_location = round(start_section["location"] / 1000, 3)
        end_location = round(end_section["location"] / 1000, 3)

        mode = "range"
        location = None
        if start_section["name"] == end_section["name"]:
            mode = "point"
            location = start_location

        annotations.append(
            {
                "mode": mode,
                "name": object_name,
                "type": object_type,
                "start_section_id": start_section_id,
                "end_section_id": end_section_id,
                "start_section_name": start_section["name"],
                "end_section_name": end_section["name"],
                "start_location": start_location,
                "end_location": end_location,
                "location": location,
                "params": params,
                "param_summary": param_summary,
            }
        )

    annotations.sort(
        key=lambda item: (
            item.get("location")
            if item.get("location") is not None
            else min(item.get("start_location", 0.0), item.get("end_location", 0.0))
        )
    )
    return annotations, object_errors


def build_dataset(
    csv_path: Path,
    objects_yaml_path: Path | None = None,
    objects_yaml_url: str | None = None,
) -> dict:
    yaml_text = load_objects_yaml(objects_yaml_path=objects_yaml_path, objects_yaml_url=objects_yaml_url)
    all_sections = normalize_section_locations(parse_cross_sections(yaml_text))
    all_sections_by_id = {item["id"]: item for item in all_sections}
    all_sections_by_name = {item["name"]: item for item in all_sections}
    section_errors = collect_section_errors(all_sections)
    invalid_section_ids = {item["section_id"] for item in section_errors}
    invalid_section_names = {item["section_name"] for item in section_errors}
    sections, profile_section_errors = select_profile_sections_from_objects(
        yaml_text,
        all_sections,
        invalid_section_ids=invalid_section_ids,
        invalid_section_names=invalid_section_names,
    )
    if len(sections) < 3:
        raise ValueError("从 objects 的 INLET/OUTLET 引用中提取的有效断面不足 3 个，无法生成纵剖面")
    sections_by_id = {item["id"]: item for item in sections}
    sections_by_name = {item["name"]: item for item in sections}
    object_annotations, object_errors = parse_object_annotations(
        yaml_text,
        sections,
        invalid_section_ids=invalid_section_ids,
        invalid_section_names=invalid_section_names,
    )

    df = load_timeseries_dataframe(csv_path)
    df["value"] = pd.to_numeric(df["value"])
    df["data_index"] = pd.to_numeric(df["data_index"])
    last_step = int(df["data_index"].max())
    gate_df = df[(df["object_type"] == "Gate") & (df["metrics_code"] == "gate_opening")].copy()

    last_water = df[(df["metrics_code"] == "water_level") & (df["data_index"] == last_step)][
        ["object_name", "value"]
    ].copy()
    water_map = {row.object_name: round(float(row.value), 3) for row in last_water.itertuples(index=False)}

    profile_points = []
    for item in sections:
        water = water_map.get(item["name"])
        profile_points.append(
            {
                "name": item["name"],
                "location": round(item["location"] / 1000, 3),
                "bottom_elevation": round(item["bottom_elevation"], 3),
                "top_elevation": round(item["top_elevation"], 3),
                "water_level": water,
                "depth": round(water - item["bottom_elevation"], 3) if water is not None else None,
            }
        )

    matched = [item for item in profile_points if item["water_level"] is not None]
    min_bed = min(item["bottom_elevation"] for item in profile_points)
    max_water = max(item["water_level"] for item in matched)
    max_top = max(item["top_elevation"] for item in profile_points)
    start = matched[0]
    end = matched[-1]

    raw_gate_stations = parse_gate_stations(yaml_text)
    csv_gate_names = set(gate_df["object_name"].dropna().unique().tolist())
    
    gate_errors = []
    gate_stations = []
    gate_markers = []
    for gs in raw_gate_stations:
        inlet_section = (
            all_sections_by_id.get(gs.get("inlet_section_id"))
            if gs.get("inlet_section_id") is not None
            else None
        ) or all_sections_by_name.get(gs.get("inlet_section", ""))
        outlet_section = (
            all_sections_by_id.get(gs.get("outlet_section_id"))
            if gs.get("outlet_section_id") is not None
            else None
        ) or all_sections_by_name.get(gs.get("outlet_section", ""))
        inlet_location = round(inlet_section["location"] / 1000, 3) if inlet_section else None
        outlet_location = round(outlet_section["location"] / 1000, 3) if outlet_section else None
        invalid_reasons = []
        if inlet_section is None:
            invalid_reasons.append("INLET断面缺失")
        if outlet_section is None:
            invalid_reasons.append("OUTLET断面缺失")
        if inlet_location is not None and abs(inlet_location) < 1e-6:
            invalid_reasons.append("INLET断面里程为0")
        if outlet_location is not None and abs(outlet_location) < 1e-6:
            invalid_reasons.append("OUTLET断面里程为0")
        if invalid_reasons:
            gate_errors.append(
                {
                    "type": "invalid_gate_station_location",
                    "severity": "error",
                    "object_name": gs["name"],
                    "object_type": "GateStation",
                    "inlet_section_id": gs["inlet_section_id"],
                    "outlet_section_id": gs["outlet_section_id"],
                    "inlet_section_name": gs["inlet_section"],
                    "outlet_section_name": gs["outlet_section"],
                    "inlet_location": inlet_location,
                    "outlet_location": outlet_location,
                    "message": f"闸站位置无效（{'、'.join(invalid_reasons)}），未参与剖面闸站位置绘制",
                }
            )
            continue
        st_location = round((inlet_location + outlet_location) / 2, 3)
        location_source = "inlet_outlet_midpoint"
        
        valid_gates = [g for g in gs["gates"] if g in csv_gate_names]
        if not valid_gates:
            valid_gates = gs["gates"]
            
        gate_stations.append({
            "short_name": gs["short_name"],
            "name": gs["name"],
            "location": st_location,
            "inlet_section": gs["inlet_section"],
            "outlet_section": gs["outlet_section"],
            "inlet_section_id": gs["inlet_section_id"],
            "outlet_section_id": gs["outlet_section_id"],
            "inlet_location": inlet_location,
            "outlet_location": outlet_location,
            "location_source": location_source,
            "gates": valid_gates,
            "role": gs["role"]
        })
        
        gate_markers.append({
            "name": gs["name"],
            "short_name": gs["short_name"],
            "location": st_location,
            "inlet_section": gs["inlet_section"],
            "outlet_section": gs["outlet_section"],
            "inlet_location": inlet_location,
            "outlet_location": outlet_location,
            "location_source": location_source,
        })

    invalid_gate_names = {item["object_name"] for item in gate_errors}
    if invalid_gate_names:
        object_annotations = [
            item
            for item in object_annotations
            if not (item.get("type") == "GateStation" and item.get("name") in invalid_gate_names)
        ]
    profile_errors = [*section_errors, *profile_section_errors, *object_errors, *gate_errors]

    return {
        "meta": {
            "csv_path": str(csv_path),
            "last_step": last_step,
            "section_count": len(profile_points),
            "matched_water_level_sections": len(matched),
            "distance_km": round(profile_points[-1]["location"] - profile_points[0]["location"], 3),
            "water_drop_m": round(start["water_level"] - end["water_level"], 3),
            "min_bed_elevation": round(min_bed, 3),
            "max_water_level": round(max_water, 3),
            "max_top_elevation": round(max_top, 3),
            "flow_direction": f"左侧上游（{start['name']}） → 右侧下游（{end['name']}）",
            "gate_station_count": len(gate_stations),
            "section_error_count": len(section_errors),
            "object_error_count": len(object_errors),
            "gate_error_count": len(gate_errors),
            "profile_section_error_count": len(profile_section_errors),
            "profile_error_count": len(profile_errors),
        },
        "profile_points": profile_points,
        "gate_markers": gate_markers,
        "gate_stations": gate_stations,
        "object_annotations": object_annotations,
        "section_errors": section_errors,
        "object_errors": object_errors,
        "gate_errors": gate_errors,
        "profile_errors": profile_errors,
        "highlights": {
            "start": start,
            "end": end,
            "deepest": max(matched, key=lambda item: item["depth"]),
            "shallowest": min(matched, key=lambda item: item["depth"]),
        },
    }


def interpolate_sequence_value(x_values: list[float], y_values: list[float], x: float) -> float:
    if not x_values:
        return 0.0
    if x <= x_values[0]:
        return y_values[0]
    if x >= x_values[-1]:
        return y_values[-1]
    for index in range(len(x_values) - 1):
        left_x = x_values[index]
        right_x = x_values[index + 1]
        if x < left_x or x > right_x:
            continue
        span = right_x - left_x
        if span == 0:
            return y_values[index]
        ratio = (x - left_x) / span
        return y_values[index] + (y_values[index + 1] - y_values[index]) * ratio
    return y_values[-1]


def save_profile_png(dataset: dict, output_png: Path) -> None:
    if plt is None:
        raise RuntimeError("matplotlib 未安装，无法输出纵剖面 PNG")

    matched = [item for item in dataset["profile_points"] if item["water_level"] is not None]
    x_data = [item["location"] for item in matched]
    bed_data = [item["bottom_elevation"] for item in matched]
    top_data = [item["top_elevation"] for item in matched]
    water_data = [item["water_level"] for item in matched]

    y_top = max(max(water_data), max(top_data)) + 0.38
    y_bottom = min(bed_data) - 0.35
    x_left = min(x_data)
    x_right = max(x_data)
    x_padding = max((x_right - x_left) * 0.02, 0.15)

    fig, ax = plt.subplots(figsize=(14, 6.4))
    ax.fill_between(x_data, y_bottom, bed_data, color="#87603d", alpha=0.16)
    point_style = {
        "marker": "o",
        "markersize": 2,
        "markerfacecolor": "#ffffff",
        "markeredgecolor": "#87603d",
        "markeredgewidth": 1.0,
    }
    ax.plot(
        x_data,
        top_data,
        color="#637487",
        linewidth=2,
        label="断面顶高程",
        **{**point_style, "markeredgecolor": "#637487"},
    )
    ax.plot(x_data, bed_data, color="#87603d", linewidth=2.4, label="断面底高程", **point_style)
    ax.fill_between(x_data, bed_data, water_data, color="#1c7fb5", alpha=0.12)

    for station in dataset["gate_stations"]:
        station_bed = interpolate_sequence_value(x_data, bed_data, station["location"])
        ax.vlines(
            station["location"],
            station_bed,
            y_top - 0.45,
            color="#111827",
            linestyle="--",
            linewidth=1.4,
            alpha=0.82,
        )
        ax.text(
            station["location"],
            y_top - 0.02,
            station["short_name"],
            ha="center",
            va="bottom",
            fontsize=10,
            color="#111827",
            fontweight="bold",
        )

    ax.annotate(
        "",
        xy=(x_data[-1], y_top - 0.18),
        xytext=(x_data[0], y_top - 0.18),
        arrowprops={"arrowstyle": "->", "color": "#1c7fb5", "lw": 2},
    )
    ax.text(x_data[0], y_top - 0.12, "上游", fontsize=10, color="#1c7fb5", ha="left")
    ax.text(x_data[-1], y_top - 0.12, "下游", fontsize=10, color="#1c7fb5", ha="right")

    ax.set_title("渠道纵剖面", fontsize=15, fontweight="bold", pad=10)
    ax.set_xlabel("里程 (km)", fontsize=12, labelpad=8)
    ax.set_ylabel("高程 / 水位 (m)", fontsize=12, labelpad=10)
    ax.xaxis.set_label_coords(0.5, -0.065)
    ax.yaxis.set_label_coords(-0.08, 0.5)
    ax.set_xlim(x_left - x_padding, x_right + x_padding)
    ax.set_ylim(y_bottom, y_top + 0.06)
    ax.grid(True, alpha=0.24)
    ax.legend(loc="upper right")
    fig.subplots_adjust(left=0.1, right=0.985, top=0.9, bottom=0.14)
    fig.savefig(output_png, dpi=180)
    plt.close(fig)
    print(f"纵剖面图: {output_png}")


def build_html(dataset: dict) -> str:
    data_json = json.dumps(dataset, ensure_ascii=False)
    html = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>渠道纵剖面</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
    <style>
      :root {{
        --bg: #eff4f6;
        --ink: #11293b;
        --muted: #5b7385;
        --line: #d5e1e8;
        --panel: rgba(255,255,255,0.84);
        --shadow: 0 24px 48px rgba(17, 41, 59, 0.08);
        --water: #1c7fb5;
        --bed: #87603d;
        --depth: rgba(28,127,181,0.16);
        --gate: #111827;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: Inter, "PingFang SC", Arial, sans-serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(28,127,181,0.12), transparent 30%),
          radial-gradient(circle at top right, rgba(135,96,61,0.08), transparent 26%),
          var(--bg);
      }}
      .shell {{ max-width: 1500px; margin: 0 auto; padding: 24px; }}
      .hero, .panel {{
        background: var(--panel);
        border: 1px solid rgba(255,255,255,0.92);
        border-radius: 30px;
        box-shadow: var(--shadow);
        backdrop-filter: blur(14px);
      }}
      .hero {{ padding: 28px 30px; }}
      .eyebrow {{
        margin: 0;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.28em;
        text-transform: uppercase;
        color: rgba(28,127,181,0.72);
      }}
      h1 {{ margin: 10px 0 12px; font-size: clamp(32px, 4vw, 50px); line-height: 1.04; }}
      .hero p:last-child {{ margin: 0; color: var(--muted); line-height: 1.8; max-width: 940px; }}
      .meta {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 14px;
        margin-top: 22px;
      }}
      .card {{
        padding: 16px 18px;
        border-radius: 22px;
        background: rgba(255,255,255,0.84);
        border: 1px solid var(--line);
      }}
      .card small {{
        display: block;
        font-size: 12px;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.16em;
      }}
      .card strong {{ display: block; margin-top: 8px; font-size: 26px; }}
      .layout {{
        display: grid;
        grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.7fr);
        gap: 18px;
        margin-top: 18px;
      }}
      .panel {{ padding: 22px; }}
      .panel h2 {{ margin: 0 0 8px; font-size: 24px; }}
      .subtle {{ margin: 0; color: var(--muted); line-height: 1.7; }}
      #chart {{ margin-top: 18px; height: 620px; }}
      .chart-wrap {{ position: relative; }}
      .section-error-panel {{
        position: absolute;
        top: 16px;
        right: 16px;
        z-index: 20;
        width: min(360px, calc(100% - 32px));
        border: 1px solid rgba(185, 28, 28, 0.2);
        border-radius: 8px;
        background: rgba(255,255,255,0.96);
        box-shadow: 0 18px 40px rgba(17, 41, 59, 0.14);
        color: #7f1d1d;
      }}
      .section-error-panel[hidden] {{ display: none; }}
      .section-error-panel summary {{
        cursor: pointer;
        padding: 8px 12px;
        font-size: 13px;
        font-weight: 700;
        list-style: none;
      }}
      .section-error-panel summary::-webkit-details-marker {{ display: none; }}
      .section-error-list {{
        max-height: 220px;
        overflow: auto;
        border-top: 1px solid rgba(185, 28, 28, 0.14);
        padding: 8px 12px 12px;
        font-size: 12px;
        line-height: 1.6;
      }}
      .section-error-list div + div {{ margin-top: 8px; }}
      .legend {{
        display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px;
      }}
      .legend span {{
        display: inline-flex; align-items: center; gap: 8px;
        padding: 8px 12px; border-radius: 999px;
        background: rgba(255,255,255,0.78); border: 1px solid var(--line); font-size: 13px;
      }}
      .flow-direction {{
        display: flex;
        align-items: center;
        gap: 12px;
        margin-top: 14px;
        padding: 12px 16px;
        border-radius: 18px;
        background: rgba(28,127,181,0.08);
        border: 1px solid rgba(28,127,181,0.18);
        color: var(--ink);
        font-size: 14px;
        font-weight: 600;
      }}
      .flow-direction .arrow-line {{
        position: relative;
        flex: 1;
        height: 3px;
        border-radius: 999px;
        background: linear-gradient(90deg, rgba(28,127,181,0.35), rgba(28,127,181,0.92));
      }}
      .flow-direction .arrow-line::after {{
        content: "";
        position: absolute;
        right: -1px;
        top: -5px;
        border-top: 6px solid transparent;
        border-bottom: 6px solid transparent;
        border-left: 11px solid rgba(28,127,181,0.92);
      }}
      .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
      .note {{
        margin-top: 16px; padding: 16px 18px; border-radius: 22px;
        background: rgba(255,255,255,0.78); border: 1px solid var(--line); line-height: 1.75; color: var(--muted);
      }}
      .detail {{
        border-radius: 28px;
        background: linear-gradient(180deg, rgba(17,41,59,0.98), rgba(24,48,72,0.94));
        padding: 22px;
        color: white;
      }}
      .detail h3 {{ margin: 8px 0 6px; font-size: 28px; }}
      .detail p {{ margin: 0; line-height: 1.8; color: rgba(255,255,255,0.8); }}
      .detail-grid {{
        display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 18px;
      }}
      .detail-grid div {{
        padding: 14px 16px; border-radius: 20px; background: rgba(255,255,255,0.08);
      }}
      .detail-grid small {{
        display: block; text-transform: uppercase; letter-spacing: 0.14em; color: rgba(255,255,255,0.58); font-size: 11px;
      }}
      .detail-grid strong {{ display: block; margin-top: 6px; font-size: 18px; }}
      .station-list {{
        display: grid;
        gap: 12px;
        margin-top: 18px;
      }}
      .station-card {{
        padding: 16px 18px;
        border-radius: 22px;
        background: rgba(255,255,255,0.82);
        border: 1px solid var(--line);
      }}
      .station-card h4 {{
        margin: 0 0 6px;
        font-size: 18px;
      }}
      .station-card p {{
        margin: 0;
        color: var(--muted);
        line-height: 1.7;
      }}
      .station-card .station-meta {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 10px;
      }}
      .station-card .station-meta span {{
        padding: 6px 10px;
        border-radius: 999px;
        font-size: 12px;
        background: rgba(17,24,39,0.08);
        color: #111827;
      }}
      .table-wrap {{
        margin-top: 18px;
        border-radius: 24px;
        overflow: hidden;
        border: 1px solid var(--line);
        background: rgba(255,255,255,0.82);
      }}
      table {{ width: 100%; border-collapse: collapse; }}
      th, td {{ padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }}
      th {{ font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); background: rgba(239,244,246,0.9); }}
      tr:last-child td {{ border-bottom: 0; }}
      @media (max-width: 1100px) {{ .layout {{ grid-template-columns: 1fr; }} }}
    </style>
  </head>
  <body>
    <div class="shell">
      <section class="hero">
        <p class="eyebrow">Hydros Longitudinal Profile</p>
        <h1>渠道纵剖面图</h1>
        <p>
          纵剖面基于 `objects.yaml` 中的断面里程与底高程构建，并叠加相应结果数据
          在最后时刻（当前展示步 `__LAST_STEP__`）的水位线。这样可以同时观察沿程床面变化和当前工况下的水面线走势。
        </p>
        <div class="meta">
          <div class="card"><small>总里程</small><strong>__DISTANCE_KM__ km</strong></div>
          <div class="card"><small>匹配水位断面</small><strong>__MATCHED_COUNT__</strong></div>
          <div class="card"><small>沿程水头损失</small><strong>__WATER_DROP__ m</strong></div>
          <div class="card"><small>最低底高程</small><strong>__MIN_BED__ m</strong></div>
          <div class="card"><small>闸站数量</small><strong>__GATE_STATION_COUNT__</strong></div>
          <div class="card"><small>水流流向</small><strong style="font-size: 18px;">__FLOW_DIRECTION__</strong></div>
        </div>
      </section>

      <div class="layout">
        <section class="panel">
          <h2>床面线与水面线</h2>
          <p class="subtle">
            灰色实线表示断面顶高程，棕色线表示断面底高程，蓝色阴影表示当前展示水体范围，
            棕色阴影填充到坐标轴底部，用来同时表达过水断面和床面起伏。
            图中额外用竖线标出各闸站入口位置。
          </p>
          <div class="legend">
            <span><i class="dot" style="background: #637487"></i>断面顶高程</span>
            <span><i class="dot" style="background: var(--bed)"></i>断面底高程</span>
            <span><i class="dot" style="background: rgba(28,127,181,0.36)"></i>当前展示水体</span>
            <span><i class="dot" style="background: var(--gate)"></i>闸站位置</span>
          </div>
          <div class="flow-direction">
            <span>上游</span>
            <div class="arrow-line"></div>
            <span>下游</span>
          </div>
          <div class="chart-wrap">
            <details id="sectionErrorPanel" class="section-error-panel" hidden>
              <summary>剖面数据错误 <span id="sectionErrorCount">0</span></summary>
              <div id="sectionErrorList" class="section-error-list"></div>
            </details>
            <div id="chart"></div>
          </div>
          <div class="note">
            这张图主要服务工程理解：如果水面线整体高于床面线且沿程平滑下降，通常说明渠道主流方向正常、工况稳定。
            如果某段水面突然抬升或贴近床面，就值得重点复核该段的边界条件、闸门动作或局部阻力变化。
          </div>
        </section>

        <aside class="panel">
          <div class="detail">
            <p class="eyebrow" style="color: rgba(255,255,255,0.58);">Profile Highlights</p>
            <h3>关键观察</h3>
            <p>
              最后时刻水面线从 <strong>__START_NAME__</strong> 的
              <strong>__START_WATER__ m</strong> 下降到
              <strong>__END_NAME__</strong> 的
              <strong>__END_WATER__ m</strong>，
              说明主干渠整体维持稳定下泄。
            </p>
            <div class="detail-grid">
              <div>
                <small>最大水深</small>
                <strong>__DEEPEST_NAME__</strong>
              </div>
              <div>
                <small>水深</small>
                <strong>__DEEPEST_DEPTH__ m</strong>
              </div>
              <div>
                <small>最小水深</small>
                <strong>__SHALLOWEST_NAME__</strong>
              </div>
              <div>
                <small>水深</small>
                <strong>__SHALLOWEST_DEPTH__ m</strong>
              </div>
            </div>
          </div>

          <div class="station-list" id="stationList"></div>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>断面</th>
                  <th>里程 (km)</th>
                  <th>顶高程 (m)</th>
                  <th>底高程 (m)</th>
                  <th>水位 (m)</th>
                  <th>水深 (m)</th>
                </tr>
              </thead>
              <tbody id="tableBody"></tbody>
            </table>
          </div>
        </aside>
      </div>
    </div>

    <script>
      const dataset = __DATA_JSON__;
      const chart = echarts.init(document.getElementById("chart"));
      const escapeHtml = (value) => String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
      const formatProfileErrorDetail = (item) => {{
        if (item.section_name) {{
          return `里程 ${{Number(item.location).toFixed(3)}} km，顶高程 ${{item.top_elevation}} m，底高程 ${{item.bottom_elevation}} m`;
        }}
        if (item.inlet_section_name || item.outlet_section_name) {{
          const inletLocation = item.inlet_location == null ? '无' : `${{Number(item.inlet_location).toFixed(3)}} km`;
          const outletLocation = item.outlet_location == null ? '无' : `${{Number(item.outlet_location).toFixed(3)}} km`;
          return `闸前 ${{escapeHtml(item.inlet_section_name || '-')}}，里程 ${{inletLocation}}；闸后 ${{escapeHtml(item.outlet_section_name || '-')}}，里程 ${{outletLocation}}`;
        }}
        const startLocation = item.start_location == null ? '无' : `${{Number(item.start_location).toFixed(3)}} km`;
        const endLocation = item.end_location == null ? '无' : `${{Number(item.end_location).toFixed(3)}} km`;
        return `范围 ${{escapeHtml(item.start_section_name || '-')}} → ${{escapeHtml(item.end_section_name || '-')}}，起点里程 ${{startLocation}}，终点里程 ${{endLocation}}`;
      }};
      const renderSectionErrors = () => {{
        const errors = dataset.profile_errors || dataset.section_errors || [];
        const panel = document.getElementById('sectionErrorPanel');
        const count = document.getElementById('sectionErrorCount');
        const list = document.getElementById('sectionErrorList');
        if (!panel || !count || !list) return;
        if (!errors.length) {{
          panel.hidden = true;
          return;
        }}
        panel.hidden = false;
        count.textContent = String(errors.length);
        list.innerHTML = errors.map((item) => `
          <div>
            <strong>${{escapeHtml(item.section_name || item.object_name)}}</strong><br>
            ${{formatProfileErrorDetail(item)}}<br>
            ${{escapeHtml(item.message)}}
          </div>
        `).join('');
      }};
      renderSectionErrors();

      const points = dataset.profile_points;
      const matched = points.filter((item) => item.water_level !== null);
      const xData = matched.map((item) => item.location);
      const bedData = matched.map((item) => item.bottom_elevation);
      const topData = matched.map((item) => item.top_elevation);
      const waterData = matched.map((item) => item.water_level);
      const rawMin = Math.min(...bedData);
      const rawMax = Math.max(...topData, ...waterData);
      const yMin = Number((rawMin - 0.35).toFixed(3));
      const yMax = Number((rawMax + 0.38).toFixed(3));
      const xMin = Math.min(...xData);
      const xMax = Math.max(...xData);
      const bedFillSegments = matched.slice(0, -1).map((item, index) => [
        item.location,
        matched[index + 1].location,
        yMin,
        item.bottom_elevation,
        matched[index + 1].bottom_elevation
      ]);
      const waterFillSegments = matched.slice(0, -1).map((item, index) => [
        item.location,
        matched[index + 1].location,
        item.bottom_elevation,
        matched[index + 1].bottom_elevation,
        item.water_level,
        matched[index + 1].water_level
      ]);
      const annotationTypeColors = {{
        UnifiedCanal: '#176a87',
        Pipe: '#d12f2f',
        GateStation: '#111827',
        DisturbanceNode: '#2b8a57'
      }};
      const getAnnotationColor = (type) => annotationTypeColors[type] || '#4c6476';
      const annotationTypeLabels = {{
        UnifiedCanal: '渠道范围',
        Pipe: '倒虹吸/管道',
        GateStation: '闸站',
        DisturbanceNode: '分水口/退水闸'
      }};
      const annotationParamLabels = {{
        manning_n: '曼宁系数',
        length: '长度',
        loss_coeff: '损失系数',
        orifice_count: '孔口数量',
        single_orifice_height: '单孔高度',
        single_orifice_width: '单孔宽度',
        cross_section_type: '断面形式',
        boundary_type: '边界类型',
        min_sectional_area: '最小过水面积',
        t_bottom_width: '底宽',
        t_side_slope_ratio: '边坡系数'
      }};
      const annotationParamUnits = {{
        length: 'm',
        single_orifice_height: 'm',
        single_orifice_width: 'm'
      }};
      const localizeAnnotationType = (type) => annotationTypeLabels[type] || type;
      const formatAnnotationParamValue = (item) => {{
        const unit = annotationParamUnits[item.key];
        return unit ? `${{item.value}} ${{unit}}` : item.value;
      }};
      const localizeAnnotationParams = (params) =>
        (params || []).map((item) => `${{annotationParamLabels[item.key] || item.key}}=${{formatAnnotationParamValue(item)}}`).join('，');
      const positionFloatingTooltip = (point, params, dom, rect, size) => {{
        const [mouseX, mouseY] = point;
        const viewWidth = size.viewSize[0];
        const viewHeight = size.viewSize[1];
        const boxWidth = size.contentSize[0];
        const boxHeight = size.contentSize[1];
        const offsetX = 18;
        const offsetY = 18;
        let x = mouseX + offsetX;
        let y = mouseY - boxHeight - offsetY;
        if (x + boxWidth > viewWidth - 12) {{
          x = Math.max(12, mouseX - boxWidth - offsetX);
        }}
        if (y < 12) {{
          y = Math.min(viewHeight - boxHeight - 12, mouseY + offsetY);
        }}
        return [x, y];
      }};
      const formatAnnotationTooltip = (item) => [
        `<strong>${{item.name}}</strong>`,
        `类型: ${{localizeAnnotationType(item.type)}}`,
        item.mode === 'range'
          ? `范围: ${{item.start_section_name}} → ${{item.end_section_name}}`
          : `位置: ${{Number(item.location).toFixed(3)}} km`,
        item.params?.length ? `参数: ${{localizeAnnotationParams(item.params)}}` : '参数: 无'
      ].join('<br>');
      const interpolateWaterLevel = (location) => {{
        if (!matched.length) return null;
        if (location <= matched[0].location) return matched[0].water_level;
        if (location >= matched[matched.length - 1].location) return matched[matched.length - 1].water_level;
        for (let index = 0; index < matched.length - 1; index += 1) {{
          const left = matched[index];
          const right = matched[index + 1];
          if (location < left.location || location > right.location) continue;
          const span = right.location - left.location;
          if (!span) return left.water_level;
          const ratio = (location - left.location) / span;
          return left.water_level + (right.water_level - left.water_level) * ratio;
        }}
        return null;
      }};
      const interpolateProfileValue = (location, key) => {{
        if (!matched.length) return null;
        if (location <= matched[0].location) return matched[0][key];
        if (location >= matched[matched.length - 1].location) return matched[matched.length - 1][key];
        for (let index = 0; index < matched.length - 1; index += 1) {{
          const left = matched[index];
          const right = matched[index + 1];
          if (location < left.location || location > right.location) continue;
          const span = right.location - left.location;
          if (!span) return left[key];
          const ratio = (location - left.location) / span;
          return left[key] + (right[key] - left[key]) * ratio;
        }}
        return null;
      }};
      const objectRangeAnnotations = (dataset.object_annotations || [])
        .filter((item) => item.mode === 'range')
        .map((item, index) => {{
          const startPoint = matched.find((point) => point.name === item.start_section_name);
          const endPoint = matched.find((point) => point.name === item.end_section_name);
          if (!startPoint || !endPoint) return null;
          return {{
            ...item,
            index,
            start_water_level: startPoint.water_level,
            end_water_level: endPoint.water_level,
            start_top_elevation: startPoint.top_elevation,
            start_bottom_elevation: startPoint.bottom_elevation,
            end_top_elevation: endPoint.top_elevation,
            end_bottom_elevation: endPoint.bottom_elevation,
          }};
        }})
        .filter(Boolean);
      const objectPointAnnotations = (dataset.object_annotations || [])
        .filter((item) => item.mode === 'point')
        .map((item, index) => {{
          const location = item.location ?? item.start_location;
          const waterLevel = location == null ? null : interpolateWaterLevel(location);
          if (location == null || waterLevel == null) return null;
          return {{
            ...item,
            index,
            location,
            water_level: waterLevel,
          }};
        }})
        .filter(Boolean);
      const clipRect = (params) => ({{
        x: params.coordSys.x,
        y: params.coordSys.y,
        width: params.coordSys.width,
        height: params.coordSys.height,
      }});
      const clipPolygon = (polygon, params) => echarts.graphic.clipPointsByRect(polygon, clipRect(params));
      const clipPolyline = (polyline, params) => echarts.graphic.clipPointsByRect(polyline, clipRect(params));
      const profilePointRadius = 1.5;
      const buildProfilePointStyle = (strokeColor) => ({{ fill: '#ffffff', stroke: strokeColor, lineWidth: 1 }});
      const buildProfileSeriesPointStyle = (strokeColor) => ({{
        color: '#ffffff',
        borderColor: strokeColor,
        borderWidth: 1
      }});
      const annotationLegendPrefix = '相关对象: ';
      const annotationTypeOrder = ['UnifiedCanal', 'Pipe', 'GateStation', 'DisturbanceNode'];
      const hiddenAnnotationTypes = new Set(['UnifiedCanal']);
      const buildAnnotationLegendName = (type) => `${{annotationLegendPrefix}}${{localizeAnnotationType(type)}}`;
      const formatAnnotationLegendName = (name) =>
        name === buildAnnotationLegendName('UnifiedCanal')
          ? name
          : name.startsWith(annotationLegendPrefix)
            ? name.slice(annotationLegendPrefix.length)
            : name;
      const buildObjectAnnotationDataItem = (item) => {{
        const isRange = item.mode === 'range';
        return {{
          value: [
            isRange ? item.start_location : item.location,
            isRange ? item.start_water_level : item.water_level,
            isRange ? item.end_location : item.location,
            isRange ? item.end_water_level : item.water_level,
            item.name,
            item.type,
            item.index,
            item.mode,
            isRange ? item.start_top_elevation : item.water_level,
            isRange ? item.start_bottom_elevation : item.water_level,
            isRange ? item.end_top_elevation : item.water_level,
            isRange ? item.end_bottom_elevation : item.water_level
          ],
          meta: item
        }};
      }};
      const buildObjectAnnotationSeries = (rangeItems, pointItems) => {{
        const grouped = new Map();
        [...rangeItems, ...pointItems].forEach((item) => {{
          if (hiddenAnnotationTypes.has(item.type)) return;
          if (!grouped.has(item.type)) grouped.set(item.type, []);
          grouped.get(item.type).push(item);
        }});
        const sortedTypes = [...grouped.keys()].sort((left, right) => {{
          const leftIndex = annotationTypeOrder.indexOf(left);
          const rightIndex = annotationTypeOrder.indexOf(right);
          return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
        }});
        return sortedTypes.map((annotationType) => ({{
          id: `object-type-${{annotationType}}`,
          name: buildAnnotationLegendName(annotationType),
          type: 'custom',
          silent: false,
          animation: false,
          itemStyle: {{ color: getAnnotationColor(annotationType) }},
          tooltip: {{
            show: true,
            trigger: 'item',
            confine: true,
            enterable: false,
            transitionDuration: 0,
            extraCssText: 'pointer-events:none;',
            position: positionFloatingTooltip,
            formatter: (params) => formatAnnotationTooltip(params.data.meta)
          }},
          z: 6.7,
          renderItem: (params, api) => {{
            const mode = api.value(7);
            const annotationType = api.value(5);
            const color = getAnnotationColor(annotationType);
            if (mode === 'range') {{
              if (annotationType === 'Pipe') {{
                const polygon = [
                  api.coord([api.value(0), api.value(8)]),
                  api.coord([api.value(2), api.value(10)]),
                  api.coord([api.value(2), api.value(11)]),
                  api.coord([api.value(0), api.value(9)])
                ];
                const clippedPolygon = clipPolygon(polygon, params);
                const topLine = clipPolyline([polygon[0], polygon[1]], params);
                const endLine = clipPolyline([polygon[1], polygon[2]], params);
                const bottomLine = clipPolyline([polygon[3], polygon[2]], params);
                const startLine = clipPolyline([polygon[0], polygon[3]], params);
                if (!clippedPolygon?.length) return null;
                const lineStyle = {{ stroke: color, lineWidth: 2.2, opacity: 0.86 }};
                return {{
                  type: 'group',
                  children: [
                    {{
                      type: 'polygon',
                      shape: {{ points: clippedPolygon }},
                      style: {{ fill: color, opacity: 0.12, stroke: 'none' }}
                    }},
                    topLine?.length >= 2 ? {{
                      type: 'line',
                      shape: {{ x1: topLine[0][0], y1: topLine[0][1], x2: topLine[topLine.length - 1][0], y2: topLine[topLine.length - 1][1] }},
                      style: lineStyle
                    }} : null,
                    bottomLine?.length >= 2 ? {{
                      type: 'line',
                      shape: {{ x1: bottomLine[0][0], y1: bottomLine[0][1], x2: bottomLine[bottomLine.length - 1][0], y2: bottomLine[bottomLine.length - 1][1] }},
                      style: lineStyle
                    }} : null,
                    startLine?.length >= 2 ? {{
                      type: 'line',
                      shape: {{ x1: startLine[0][0], y1: startLine[0][1], x2: startLine[startLine.length - 1][0], y2: startLine[startLine.length - 1][1] }},
                      style: {{ ...lineStyle, lineWidth: 1.6 }}
                    }} : null,
                    endLine?.length >= 2 ? {{
                      type: 'line',
                      shape: {{ x1: endLine[0][0], y1: endLine[0][1], x2: endLine[endLine.length - 1][0], y2: endLine[endLine.length - 1][1] }},
                      style: {{ ...lineStyle, lineWidth: 1.6 }}
                    }} : null
                  ].filter(Boolean)
                }};
              }}
              const clippedPolyline = clipPolyline([
                api.coord([api.value(0), api.value(1)]),
                api.coord([api.value(2), api.value(3)])
              ], params);
              if (!clippedPolyline?.length || clippedPolyline.length < 2) return null;
              const startPoint = clippedPolyline[0];
              const endPoint = clippedPolyline[clippedPolyline.length - 1];
              return {{
                type: 'group',
                children: [
                  {{
                    type: 'line',
                    shape: {{ x1: startPoint[0], y1: startPoint[1], x2: endPoint[0], y2: endPoint[1] }},
                    style: {{ stroke: color, lineWidth: 2.4, opacity: 0.78 }}
                  }},
                  {{
                    type: 'circle',
                    shape: {{ cx: startPoint[0], cy: startPoint[1], r: profilePointRadius }},
                    style: buildProfilePointStyle(color)
                  }},
                  {{
                    type: 'circle',
                    shape: {{ cx: endPoint[0], cy: endPoint[1], r: profilePointRadius }},
                    style: buildProfilePointStyle(color)
                  }}
                ]
              }};
            }}
            const bounds = clipRect(params);
            const point = api.coord([api.value(0), api.value(1)]);
            if (
              point[0] < bounds.x ||
              point[0] > bounds.x + bounds.width ||
              point[1] < bounds.y ||
              point[1] > bounds.y + bounds.height
            ) {{
              return null;
            }}
            return {{
              type: 'group',
              children: [
                {{
                  type: 'circle',
                  shape: {{ cx: point[0], cy: point[1], r: profilePointRadius }},
                  style: buildProfilePointStyle(color)
                }}
              ]
            }};
          }},
          data: grouped.get(annotationType).map(buildObjectAnnotationDataItem)
        }}));
      }};
      const objectAnnotationSeries = buildObjectAnnotationSeries(objectRangeAnnotations, objectPointAnnotations);
      const profileLineLegendNames = ['断面顶高程', '断面底高程', '水面线'];
      const canalRangeLegendName = buildAnnotationLegendName('UnifiedCanal');
      const objectLegendNames = [
        canalRangeLegendName,
        ...objectAnnotationSeries
          .map((item) => item.name)
          .filter((name) => !profileLineLegendNames.includes(name) && name !== canalRangeLegendName)
      ];
      const legendNames = [...profileLineLegendNames, ...objectLegendNames];

      const gateLineSegments = dataset.gate_markers.map((item) => [
        item.location,
        interpolateProfileValue(item.location, 'bottom_elevation') ?? yMin,
        yMax,
        item.short_name || item.name
      ]);
      const buildGateStationSeries = () => ({{
        name: '闸站位置',
        type: 'custom',
        silent: true,
        animation: false,
        tooltip: {{ show: false }},
        z: 7,
        renderItem: (params, api) => {{
          const stationKey = api.value(3);
          const x = api.coord([api.value(0), api.value(1)])[0];
          const yBottom = api.coord([api.value(0), api.value(1)])[1];
          const yTop = api.coord([api.value(0), api.value(2)])[1];
          const bounds = clipRect(params);
          if (x < bounds.x || x > bounds.x + bounds.width) return null;
          const clippedBottom = Math.max(bounds.y, Math.min(bounds.y + bounds.height, yBottom));
          const clippedTop = Math.max(bounds.y, Math.min(bounds.y + bounds.height, yTop));
          const textY = Math.max(bounds.y + 12, clippedTop + 14);
          const lineTopY = Math.max(clippedTop, textY + 8);
          const lineShape = lineTopY < clippedBottom
            ? {{ x1: x, y1: clippedBottom, x2: x, y2: lineTopY }}
            : null;
          return {{
            type: 'group',
            children: [
              lineShape ? {{
                type: 'line',
                shape: lineShape,
                style: {{ stroke: '#111827', lineWidth: 1.4, lineDash: [6, 4], opacity: 0.92 }}
              }} : null,
              {{
                type: 'text',
                style: {{
                  x,
                  y: textY,
                  text: stationKey,
                  fill: '#111827',
                  textAlign: 'center',
                  textVerticalAlign: 'bottom',
                  font: '600 12px sans-serif'
                }}
              }}
            ].filter(Boolean)
          }};
        }},
        data: gateLineSegments
      }});

      chart.setOption({
        animationDuration: 500,
        grid: {{ left: 92, right: 44, top: 92, bottom: 82, containLabel: true }},
        tooltip: {{
          trigger: 'axis',
          axisPointer: {{ type: 'cross' }},
          formatter: (params) => {{
            const idx = params[0]?.dataIndex ?? 0;
            const row = matched[idx];
            return [
              `<strong>${{row.name}}</strong>`,
              `里程: ${{row.location}} km`,
              `顶高程: ${{row.top_elevation}} m`,
              `底高程: ${{row.bottom_elevation}} m`,
              `水位: ${{row.water_level}} m`,
              `水深: ${{row.depth}} m`
            ].join('<br>');
          }}
        }},
        legend: {{
          show: legendNames.length > 0,
          type: 'scroll',
          top: 12,
          left: 96,
          right: 40,
          data: legendNames,
          formatter: formatAnnotationLegendName,
          textStyle: {{ color: '#5b7385' }},
          pageTextStyle: {{ color: '#5b7385' }},
          pageIconColor: '#1c7fb5',
          pageIconInactiveColor: 'rgba(91, 115, 133, 0.28)'
        }},
        xAxis: {{
          type: 'value',
          name: '里程 (km)',
          min: Number(xMin.toFixed(3)),
          max: Number(xMax.toFixed(3)),
          nameLocation: 'middle',
          nameGap: 44,
          axisLabel: {{ color: '#5b7385', margin: 14 }},
          splitLine: {{ show: false }}
        }},
        yAxis: {{
          type: 'value',
          name: '高程 / 水位 (m)',
          min: yMin,
          max: yMax,
          nameLocation: 'middle',
          nameRotate: 90,
          nameGap: 72,
          axisLabel: {{ color: '#5b7385', margin: 14 }},
          splitLine: {{ show: false }}
        }},
        series: [
          {{
            type: 'custom',
            silent: true,
            tooltip: {{ show: false }},
            z: 1,
            renderItem: (params, api) => {{
              const polygon = [
                api.coord([api.value(0), api.value(2)]),
                api.coord([api.value(0), api.value(3)]),
                api.coord([api.value(1), api.value(4)]),
                api.coord([api.value(1), api.value(2)])
              ];
              return {{
                type: 'polygon',
                shape: {{ points: polygon }},
                style: {{ fill: 'rgba(135,96,61,0.16)', stroke: 'none' }}
              }};
            }},
            data: bedFillSegments
          }},
          {{
            type: 'custom',
            silent: true,
            tooltip: {{ show: false }},
            z: 2,
            renderItem: (params, api) => {{
              const polygon = [
                api.coord([api.value(0), api.value(2)]),
                api.coord([api.value(0), api.value(4)]),
                api.coord([api.value(1), api.value(5)]),
                api.coord([api.value(1), api.value(3)])
              ];
              return {{
                type: 'polygon',
                shape: {{ points: polygon }},
                style: {{ fill: 'rgba(28,127,181,0.12)', stroke: 'none' }}
              }};
            }},
            data: waterFillSegments
          }},
          {{
            name: '断面顶高程',
            type: 'line',
            smooth: false,
            showSymbol: true,
            symbol: 'circle',
            symbolSize: 3,
            lineStyle: {{ color: '#637487', width: 2 }},
            itemStyle: buildProfileSeriesPointStyle('#637487'),
            emphasis: {{ disabled: true }},
            z: 4,
            data: matched.map((item) => [item.location, item.top_elevation])
          }},
          {{
            name: '水面线',
            type: 'line',
            smooth: false,
            showSymbol: true,
            symbol: 'circle',
            symbolSize: 2.8,
            connectNulls: true,
            lineStyle: {{ color: '#1c7fb5', width: 2.4 }},
            itemStyle: buildProfileSeriesPointStyle('#1c7fb5'),
            emphasis: {{ disabled: true }},
            z: 5.6,
            data: matched.map((item) => [item.location, item.water_level])
          }},
          {{
            name: canalRangeLegendName,
            type: 'line',
            silent: true,
            animation: false,
            showSymbol: false,
            tooltip: {{ show: false }},
            lineStyle: {{ color: getAnnotationColor('UnifiedCanal'), width: 2.4, opacity: 0.78 }},
            itemStyle: {{ color: getAnnotationColor('UnifiedCanal') }},
            emphasis: {{ disabled: true }},
            data: []
          }},
          {{
            name: '断面底高程',
            type: 'line',
            smooth: false,
            showSymbol: true,
            symbol: 'circle',
            symbolSize: 3,
            lineStyle: {{ color: '#87603d', width: 2.2 }},
            itemStyle: buildProfileSeriesPointStyle('#87603d'),
            emphasis: {{ disabled: true }},
            z: 5,
            data: matched.map((item) => [item.location, item.bottom_elevation])
          }},
          ...objectAnnotationSeries,
          buildGateStationSeries()
        ]
      });

      const stationList = document.getElementById('stationList');
      stationList.innerHTML = dataset.gate_stations.map((item) => `
        <div class="station-card">
          <h4>${item.short_name} · ${item.name}</h4>
          <p>${item.role}</p>
          <div class="station-meta">
            <span>里程 ${item.location} km</span>
            <span>闸前 ${{item.inlet_section || '无'}}</span>
            <span>闸后 ${{item.outlet_section || '无'}}</span>
            ${item.gates.map((gate) => `<span>${gate}</span>`).join('')}
          </div>
        </div>
      `).join('');

      const tableBody = document.getElementById('tableBody');
      tableBody.innerHTML = matched.map((item) => `
        <tr>
          <td>${item.name}</td>
          <td>${item.location}</td>
          <td>${item.top_elevation}</td>
          <td>${item.bottom_elevation}</td>
          <td>${item.water_level}</td>
          <td>${item.depth}</td>
        </tr>
      `).join('');

      window.addEventListener('resize', () => chart.resize());
    </script>
  </body>
</html>
"""
    html = html.replace("{{", "{").replace("}}", "}")
    return (
        html.replace("__DATA_JSON__", data_json)
        .replace("__LAST_STEP__", str(dataset["meta"]["last_step"]))
        .replace("__DISTANCE_KM__", str(dataset["meta"]["distance_km"]))
        .replace("__MATCHED_COUNT__", str(dataset["meta"]["matched_water_level_sections"]))
        .replace("__WATER_DROP__", str(dataset["meta"]["water_drop_m"]))
        .replace("__MIN_BED__", str(dataset["meta"]["min_bed_elevation"]))
        .replace("__GATE_STATION_COUNT__", str(dataset["meta"]["gate_station_count"]))
        .replace("__FLOW_DIRECTION__", str(dataset["meta"]["flow_direction"]))
        .replace("__START_NAME__", str(dataset["highlights"]["start"]["name"]))
        .replace("__START_WATER__", str(dataset["highlights"]["start"]["water_level"]))
        .replace("__END_NAME__", str(dataset["highlights"]["end"]["name"]))
        .replace("__END_WATER__", str(dataset["highlights"]["end"]["water_level"]))
        .replace("__DEEPEST_NAME__", str(dataset["highlights"]["deepest"]["name"]))
        .replace("__DEEPEST_DEPTH__", str(dataset["highlights"]["deepest"]["depth"]))
        .replace("__SHALLOWEST_NAME__", str(dataset["highlights"]["shallowest"]["name"]))
        .replace("__SHALLOWEST_DEPTH__", str(dataset["highlights"]["shallowest"]["depth"]))
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据 objects.yaml 和时序结果生成纵剖面 HTML 页面")
    parser.add_argument("timeseries_file")
    parser.add_argument("output_html", nargs="?")
    parser.add_argument("objects_yaml", nargs="?")
    parser.add_argument("--objects-yaml-url", default=None, help="显式传入远程 objects.yaml 地址")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    csv_path = Path(args.timeseries_file).resolve()
    output_html = (
        Path(args.output_html).resolve()
        if args.output_html
        else csv_path.parent / "longitudinal_profile.html"
    )
    objects_yaml_path = Path(args.objects_yaml).resolve() if args.objects_yaml else None

    try:
        dataset = build_dataset(
            csv_path,
            objects_yaml_path=objects_yaml_path,
            objects_yaml_url=args.objects_yaml_url,
        )
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    output_html.write_text(build_html(dataset), encoding="utf-8")
    print(f"纵剖面页面: {output_html}")


if __name__ == "__main__":
    main()
