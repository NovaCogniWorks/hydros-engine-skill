#!/usr/bin/env python3
"""
从 MDM 水网模型、仿真结果和历史 Excel 构建 GateStation 对比映射。

该脚本不调用 MCP。调用方应先通过 hydros-engine-mdm 获取 waterway 模型，
保存为 JSON 或 objects.yaml，再把文件传给本脚本。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd


OBS_COLUMNS = {"闸前水位", "闸后水位", "流量"}
STRUCTURE_WORDS = ["倒虹吸", "渡槽", "暗渠", "涵洞式渡槽", "涵洞", "隧洞", "退水闸", "分水口"]
SHEET_SUFFIXES = [
    "倒虹吸出口节制闸",
    "倒虹吸进口节制闸",
    "渡槽进口节制闸",
    "涵洞式渡槽进口节制闸",
    "涵洞进口节制闸",
    "暗渠进口节制闸",
    "隧洞出口节制闸",
    "节制闸",
]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 MDM GateStation 到历史 Excel sheet 的映射 JSON")
    parser.add_argument("--simulation-file", required=True, help="仿真结果 xlsx/csv")
    parser.add_argument("--history-excel", required=True, help="历史实测 Excel")
    parser.add_argument("--mdm-model", required=True, help="MDM waterway JSON 或 objects.yaml")
    parser.add_argument("--output", required=True, help="输出 mdm_gate_map.json")
    parser.add_argument("--diagnostics", help="输出匹配诊断文件，支持 .xlsx 或 .csv")
    parser.add_argument("--waterway-id", default="")
    parser.add_argument("--biz-scenario-id", default="")
    parser.add_argument("--min-score", type=int, default=70)
    parser.add_argument("--ambiguity-margin", type=int, default=10)
    return parser.parse_args(argv)


def normalize_text(text: Any) -> str:
    value = str(text or "")
    value = value.replace("（", "(").replace("）", ")")
    value = re.sub(r"\s+", "", value)
    return value


def clean_name(text: Any) -> str:
    value = normalize_text(text)
    value = re.sub(r"^[A-Za-z]+\d+-", "", value)
    value = re.sub(r"^\d+-", "", value)
    value = re.sub(r"站\(\d+闸\)$", "", value)
    value = re.sub(r"闸站$", "", value)
    value = re.sub(r"\d+#$", "", value)
    return value.strip()


def detect_structure(text: str) -> str:
    for word in STRUCTURE_WORDS:
        if word in text:
            return word
    return ""


def sheet_core_name(sheet_name: str) -> str:
    core = clean_name(sheet_name)
    for suffix in SHEET_SUFFIXES:
        core = core.replace(suffix, "")
    return core


def sheet_features(sheet_name: str) -> dict[str, str]:
    full = clean_name(sheet_name)
    return {
        "sheet": sheet_name,
        "full": full,
        "core": sheet_core_name(sheet_name),
        "structure": detect_structure(full),
    }


def candidate_name_variants(name: str) -> set[str]:
    cleaned = clean_name(name)
    variants = {cleaned}
    variants.add(cleaned.replace("渠道", ""))
    variants.add(cleaned.replace("(尾)", ""))
    variants.add(cleaned.replace("尾", "出口"))
    variants.add(cleaned.replace("渠道", "").replace("(尾)", ""))
    variants.add(cleaned.replace("渠道", "").replace("尾", "出口"))
    return {item for item in variants if item}


def score_sheet_match(mdm_names: list[str], sheet: dict[str, str]) -> tuple[int, list[str]]:
    best = 0
    reasons: list[str] = []
    full = sheet["full"]
    core = sheet["core"]
    structure = sheet["structure"]

    for raw_name in mdm_names:
        for name in candidate_name_variants(raw_name):
            if full == name:
                best = max(best, 100)
                reasons.append("FULL_EXACT")
            if full and (full in name or name in full):
                best = max(best, 90)
                reasons.append("FULL_CONTAINS")
            if core and core in name:
                if structure and structure in name:
                    best = max(best, 80)
                    reasons.append("CORE_AND_STRUCTURE")
                else:
                    best = max(best, 50)
                    reasons.append("CORE_ONLY")

    return best, sorted(set(reasons))


def load_simulation_ids(path: Path) -> tuple[set[int], dict[int, dict[str, Any]], dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
    required = {"object_id", "object_name", "metrics_code"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"仿真结果缺少字段: {', '.join(missing)}")
    df["object_id"] = pd.to_numeric(df["object_id"], errors="coerce")
    df = df.dropna(subset=["object_id"])
    df["object_id"] = df["object_id"].astype(int)
    ids = set(df["object_id"].tolist())
    object_map = (
        df[["object_id", "object_name", "object_type"]]
        .drop_duplicates("object_id")
        .set_index("object_id")
        .to_dict(orient="index")
    )
    meta = {}
    for col in ("biz_scenario_id", "waterway_id", "biz_scenario_instance_id"):
        if col in df.columns and not df[col].dropna().empty:
            meta[col] = str(df[col].dropna().iloc[0])
    return ids, object_map, meta


def load_history_sheets(path: Path) -> list[dict[str, Any]]:
    excel = pd.ExcelFile(path, engine="openpyxl")
    sheets = []
    for sheet_name in excel.sheet_names:
        columns = [str(col) for col in pd.read_excel(excel, sheet_name=sheet_name, nrows=0, engine="openpyxl").columns]
        if "日期" not in columns or not (OBS_COLUMNS & set(columns)):
            continue
        features = sheet_features(sheet_name)
        features["columns"] = columns
        sheets.append(features)
    return sheets


def load_mdm_model(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        raw = json.loads(text)
        if isinstance(raw, dict):
            objects = raw.get("objects") or raw.get("result", {}).get("objects") or raw.get("data", {}).get("objects")
            if objects is None and "gates" in raw:
                return normalize_existing_gate_map(raw["gates"])
            if objects is None:
                raise ValueError("MDM JSON 中未找到 objects")
            return [normalize_json_object(item) for item in objects if item.get("type") == "GateStation"]
        raise ValueError("MDM JSON 顶层必须是 object")
    return parse_gate_stations_from_objects_yaml(text)


def normalize_existing_gate_map(gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for gate in gates:
        if gate.get("mdm_status") != "OK":
            continue
        station = gate.get("station") or {}
        sections = gate.get("sections") or {}
        normalized.append(
            {
                "id": station.get("object_id"),
                "name": station.get("object_name", ""),
                "alias_name": station.get("object_alias_name", ""),
                "device_children": [],
                "cross_section_children": [
                    make_section_child("INLET", sections.get("front")),
                    make_section_child("OUTLET", sections.get("back")),
                ],
            }
        )
    return normalized


def normalize_json_object(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id") or item.get("object_id"),
        "name": item.get("name") or item.get("object_name", ""),
        "alias_name": item.get("alias_name") or item.get("object_alias_name", ""),
        "device_children": item.get("device_children") or [],
        "cross_section_children": item.get("cross_section_children") or [],
    }


def make_section_child(role: str, section: dict[str, Any] | None) -> dict[str, Any]:
    section = section or {}
    return {
        "role": role,
        "section_ref": {"id": section.get("object_id"), "name": section.get("object_name", "")},
        "alias_name": section.get("object_alias_name", ""),
    }


def parse_gate_stations_from_objects_yaml(text: str) -> list[dict[str, Any]]:
    section = extract_top_level_section(text, "objects")
    stations = []
    for block in split_yaml_list_blocks(section):
        if extract_yaml_value(block, "type") != "GateStation":
            continue
        station = {
            "id": int(extract_yaml_value(block, "id") or 0),
            "name": extract_yaml_value(block, "name") or "",
            "alias_name": extract_yaml_value(block, "alias_name") or "",
            "device_children": parse_simple_children(extract_nested_block(block, "device_children")),
            "cross_section_children": parse_cross_section_children(extract_nested_block(block, "cross_section_children")),
        }
        stations.append(station)
    return stations


def extract_top_level_section(text: str, section_name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(section_name)}:\s*$", text)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_]*:\s*$", text[start:])
    end = start + next_match.start() if next_match else len(text)
    return text[start:end]


def split_yaml_list_blocks(section: str) -> list[str]:
    return [block for block in re.split(r"(?m)^ -\s*$", section) if block.strip()]


def extract_yaml_value(block: str, key: str) -> str | None:
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
        if capture and re.match(r"^  [A-Za-z_][A-Za-z0-9_]*:\s*", line):
            break
        if capture:
            nested_lines.append(line)
    return "\n".join(nested_lines)


def parse_simple_children(block: str) -> list[dict[str, Any]]:
    children = []
    for child in re.split(r"(?m)^\s+-\s*$", block):
        if not child.strip():
            continue
        item_id = find_indented_value(child, "id")
        name = find_indented_value(child, "name")
        child_type = find_indented_value(child, "type")
        if item_id or name:
            children.append({"id": int(item_id) if item_id else None, "type": child_type or "", "name": name or ""})
    return children


def parse_cross_section_children(block: str) -> list[dict[str, Any]]:
    children = []
    for child in re.split(r"(?m)^\s+-\s*$", block):
        if not child.strip():
            continue
        role = find_indented_value(child, "role") or ""
        alias_name = find_indented_value(child, "alias_name") or ""
        section_id = find_indented_value(child, "id")
        section_name = find_indented_value(child, "name") or ""
        children.append(
            {
                "role": role,
                "section_ref": {"id": int(section_id) if section_id else None, "name": section_name},
                "alias_name": alias_name,
            }
        )
    return children


def find_indented_value(block: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s+{re.escape(key)}:\s*(.*?)\s*$", block)
    return match.group(1).strip() if match else None


def section_by_role(station: dict[str, Any], role: str) -> dict[str, Any] | None:
    for child in station.get("cross_section_children", []):
        if str(child.get("role", "")).upper() == role:
            ref = child.get("section_ref") or {}
            return {
                "object_id": ref.get("id"),
                "object_name": ref.get("name", ""),
                "object_alias_name": child.get("alias_name", ""),
            }
    return None


def station_candidate_names(station: dict[str, Any]) -> list[str]:
    names = [station.get("name", ""), station.get("alias_name", "")]
    names.extend(child.get("name", "") for child in station.get("device_children", []))
    for role in ("INLET", "OUTLET"):
        section = section_by_role(station, role)
        if section:
            names.extend([section.get("object_name", ""), section.get("object_alias_name", "")])
    return [name for name in names if name]


def match_history_sheet(station: dict[str, Any], history_sheets: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    names = station_candidate_names(station)
    candidates = []
    for sheet in history_sheets:
        score, reasons = score_sheet_match(names, sheet)
        if score > 0:
            candidates.append({"sheet": sheet["sheet"], "core": sheet["core"], "score": score, "reasons": reasons})
    candidates.sort(key=lambda item: (-item["score"], item["sheet"]))
    if not candidates:
        return None, [], "OBS_SHEET_NOT_FOUND"
    top = candidates[0]
    if top["score"] < args_min_score:
        return None, candidates[:5], "OBS_SHEET_LOW_CONFIDENCE"
    if len(candidates) > 1 and top["score"] - candidates[1]["score"] < args_ambiguity_margin:
        return None, candidates[:5], "OBS_SHEET_AMBIGUOUS"
    return top, candidates[:5], "OK"


args_min_score = 70
args_ambiguity_margin = 10


def build_map(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    global args_min_score, args_ambiguity_margin
    args_min_score = args.min_score
    args_ambiguity_margin = args.ambiguity_margin

    sim_ids, object_map, sim_meta = load_simulation_ids(Path(args.simulation_file))
    history_sheets = load_history_sheets(Path(args.history_excel))
    stations = load_mdm_model(Path(args.mdm_model))

    gates = []
    diagnostics = []
    matched_sheets: set[str] = set()
    for station in stations:
        front = section_by_role(station, "INLET")
        back = section_by_role(station, "OUTLET")
        section_ids = {section.get("object_id") for section in (front, back) if section and section.get("object_id") is not None}
        present_section_ids = sorted(int(section_id) for section_id in section_ids if int(section_id) in sim_ids)
        if not present_section_ids:
            diagnostics.append(make_diagnostic(station, "", 0, [], "EXCLUDED_NOT_IN_SIMULATION"))
            continue

        matched, candidates, status = match_history_sheet(station, history_sheets)
        for candidate in candidates:
            diagnostics.append(make_diagnostic(station, candidate["sheet"], candidate["score"], candidate["reasons"], status))

        if status != "OK" or matched is None:
            gates.append(
                {
                    "history_sheet": "",
                    "query_keyword": "",
                    "mdm_status": status,
                    "mdm_error": f"历史 Excel sheet 匹配失败；候选={candidates}",
                    "station": station_payload(station),
                    "sections": section_payload(front, back),
                    "sheet_candidates": candidates,
                }
            )
            continue

        matched_sheets.add(matched["sheet"])
        gates.append(
            {
                "history_sheet": matched["sheet"],
                "query_keyword": sheet_core_name(matched["sheet"]),
                "mdm_status": "OK",
                "mdm_error": "",
                "station": station_payload(station),
                "sections": section_payload(front, back),
                "sheet_match_score": matched["score"],
                "sheet_match_reason": matched["reasons"],
                "sheet_candidates": candidates,
                "present_section_ids": present_section_ids,
            }
        )

    excluded_history_sheets = [
        {"sheet": sheet["sheet"], "reason": "OUT_OF_CURRENT_SIMULATION_OR_NO_GATESTATION_MATCH"}
        for sheet in history_sheets
        if sheet["sheet"] not in matched_sheets
    ]
    payload = {
        "source": "build_mdm_gate_map.py",
        "mdm_model": str(Path(args.mdm_model).resolve()),
        "scope": {
            "source": "simulation_result_driven",
            "waterway_id": args.waterway_id or sim_meta.get("waterway_id", ""),
            "biz_scenario_id": args.biz_scenario_id or sim_meta.get("biz_scenario_id", ""),
            "biz_scenario_instance_id": sim_meta.get("biz_scenario_instance_id", ""),
            "history_excel_total_candidate_sheets": len(history_sheets),
            "mdm_gate_station_count": len(stations),
            "mapped_gate_count": sum(1 for gate in gates if gate["mdm_status"] == "OK"),
            "is_full_history_excel": False,
        },
        "gates": gates,
        "excluded_history_sheets": excluded_history_sheets,
    }
    return payload, diagnostics


def station_payload(station: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_id": station.get("id"),
        "object_name": station.get("name", ""),
        "object_alias_name": station.get("alias_name", ""),
    }


def section_payload(front: dict[str, Any] | None, back: dict[str, Any] | None) -> dict[str, Any]:
    payload = {}
    if front:
        payload["front"] = front
    if back:
        payload["back"] = back
    return payload


def make_diagnostic(station: dict[str, Any], sheet: str, score: int, reasons: list[str], status: str) -> dict[str, Any]:
    return {
        "station_id": station.get("id"),
        "station_name": station.get("name", ""),
        "candidate_sheet": sheet,
        "score": score,
        "reasons": ",".join(reasons),
        "status": status,
    }


def main() -> None:
    args = parse_args(sys.argv[1:])
    payload, diagnostics = build_map(args)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.diagnostics:
        write_diagnostics(pd.DataFrame(diagnostics), Path(args.diagnostics))
    print(f"映射 JSON: {output}")
    if args.diagnostics:
        print(f"诊断文件: {Path(args.diagnostics).resolve()}")
    print(f"MDM GateStation: {payload['scope']['mdm_gate_station_count']}")
    print(f"匹配成功闸站: {payload['scope']['mapped_gate_count']}")
    print(f"历史候选 Sheet: {payload['scope']['history_excel_total_candidate_sheets']}")


def write_diagnostics(df: pd.DataFrame, path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="mapping_diagnostics")
        return
    df.to_csv(path, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
