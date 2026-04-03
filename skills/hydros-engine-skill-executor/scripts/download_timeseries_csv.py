#!/usr/bin/env python3
"""
通过 hydros-engine-executor MCP 的 resources/read 方法下载仿真 CSV 并一次性落盘。

用法:
    python3 download_timeseries_csv.py <resource_uri> [output_path]
    python3 download_timeseries_csv.py --file-name SIM_xxx.csv [output_path]

优先级:
1. 显式传入 --token
2. 环境变量 HYDROS_API_TOKEN
3. ~/.codex/config.toml 中 hydros-engine-executor 的 Authorization Bearer token
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit("ERROR: 缺少 requests 依赖，请先安装 requests") from exc


DEFAULT_SERVER_URL = "https://hydroos.cn/mcps/hydros-engine-executor"
DEFAULT_ACCEPT = "application/json,text/event-stream"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 Hydros 仿真时序 CSV 到本地")
    parser.add_argument("resource_uri", nargs="?", help="形如 hydroengine://downloads/SIM_xxx.csv 的资源 URI")
    parser.add_argument("output_path", nargs="?", help="输出 CSV 路径；默认取 resource_uri 中的文件名")
    parser.add_argument("--file-name", help="只给文件名时自动拼成 hydroengine://downloads/<file_name>")
    parser.add_argument("--token", help="Hydros API token；不带 Bearer 前缀")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="MCP 服务 URL")
    parser.add_argument("--timeout", type=int, default=120, help="resources/read 请求超时时间（秒）")
    parser.add_argument("--min-lines", type=int, default=2, help="最小允许行数，低于此值视为坏文件")
    parser.add_argument("--force", action="store_true", help="覆盖已存在文件")
    return parser.parse_args(argv)


def resolve_resource_uri(args: argparse.Namespace) -> str:
    if args.file_name and args.resource_uri and not str(args.resource_uri).startswith("hydroengine://"):
        args.output_path = args.resource_uri
        args.resource_uri = None
    if args.resource_uri:
        return args.resource_uri
    if args.file_name:
        return f"hydroengine://downloads/{args.file_name}"
    raise SystemExit("ERROR: 必须提供 resource_uri 或 --file-name")


def derive_output_path(resource_uri: str, explicit_output_path: str | None) -> Path:
    if explicit_output_path:
        return Path(explicit_output_path).expanduser().resolve()
    file_name = resource_uri.rstrip("/").split("/")[-1]
    if not file_name.endswith(".csv"):
        raise SystemExit(f"ERROR: 无法从 resource_uri 推断 CSV 文件名: {resource_uri}")
    return Path.cwd() / file_name


def load_token_from_codex_config() -> str | None:
    if tomllib is None:
        return None
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return None

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    server = data.get("mcp_servers", {}).get("hydros-engine-executor", {})
    headers = server.get("http_headers", {})
    auth = headers.get("Authorization")
    if not isinstance(auth, str):
        return None
    prefix = "Bearer "
    if auth.startswith(prefix):
        return auth[len(prefix):].strip() or None
    return auth.strip() or None


def resolve_token(explicit_token: str | None) -> str:
    token = explicit_token or os.getenv("HYDROS_API_TOKEN") or load_token_from_codex_config()
    if not token:
        raise SystemExit(
            "ERROR: 未找到 Hydros token。请传 --token，或设置 HYDROS_API_TOKEN，"
            "或确保 ~/.codex/config.toml 已配置 hydros-engine-executor Authorization。"
        )
    return token


def build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Execution-Source": "codex",
        "Production-Code": "copaw",
        "Accept": DEFAULT_ACCEPT,
        "Content-Type": "application/json",
    }


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        response.raise_for_status()
        text = response.text
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise SystemExit(f"ERROR: HTTP 调用失败: {detail}") from exc
    except requests.RequestException as exc:
        raise SystemExit(f"ERROR: 请求失败: {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: 服务端返回非 JSON 内容: {text[:500]}") from exc


def initialize_mcp(server_url: str, headers: dict[str, str], timeout: int) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": DEFAULT_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "hydros-csv-downloader", "version": "1.0"},
        },
    }
    result = post_json(server_url, headers, payload, timeout=30)
    if "error" in result:
        raise SystemExit(f"ERROR: initialize 失败: {json.dumps(result['error'], ensure_ascii=False)}")


def read_resource(server_url: str, headers: dict[str, str], resource_uri: str, timeout: int) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "resources/read",
        "params": {"uri": resource_uri},
    }
    result = post_json(server_url, headers, payload, timeout=timeout)
    if "error" in result:
        raise SystemExit(f"ERROR: resources/read 失败: {json.dumps(result['error'], ensure_ascii=False)}")

    try:
        return result["result"]["contents"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(f"ERROR: resources/read 返回结构异常: {json.dumps(result, ensure_ascii=False)[:1000]}") from exc


def write_csv_once(text: str, output_path: Path, force: bool, min_lines: int) -> tuple[int, int]:
    if output_path.exists() and not force:
        raise SystemExit(f"ERROR: 输出文件已存在，请先删除或加 --force: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")

    size_bytes = output_path.stat().st_size
    with output_path.open("r", encoding="utf-8") as handle:
        line_count = sum(1 for _ in handle)

    if line_count < min_lines:
        raise SystemExit(
            f"ERROR: 落盘后的 CSV 行数异常少，仅 {line_count} 行，疑似坏文件或响应残缺: {output_path}"
        )
    return size_bytes, line_count


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    resource_uri = resolve_resource_uri(args)
    output_path = derive_output_path(resource_uri, args.output_path)
    token = resolve_token(args.token)
    headers = build_headers(token)

    initialize_mcp(args.server_url, headers, timeout=30)
    csv_text = read_resource(args.server_url, headers, resource_uri, timeout=args.timeout)
    size_bytes, line_count = write_csv_once(csv_text, output_path, force=args.force, min_lines=args.min_lines)

    print(f"saved: {output_path}")
    print(f"bytes: {size_bytes}")
    print(f"lines: {line_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
