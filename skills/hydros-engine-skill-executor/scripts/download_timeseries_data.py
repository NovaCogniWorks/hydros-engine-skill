#!/usr/bin/env python3
"""
下载 hydros 仿真结果文件并一次性落盘。

支持:
  - CSV 结果文件
  - XLSX 结果文件

用法:
    python3 download_timeseries_data.py <download_url> [output_path]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit("ERROR: 缺少 requests 依赖，请先安装 requests") from exc


SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".xls", ".xlsm"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 Hydros 仿真结果文件到本地")
    parser.add_argument("download_url", help="get_timeseries_data 返回的 https 下载链接")
    parser.add_argument("output_path", nargs="?", help="输出文件路径；默认取下载链接中的文件名")
    parser.add_argument("--timeout", type=int, default=120, help="下载超时时间（秒）")
    parser.add_argument("--min-rows", type=int, default=2, help="最小允许数据行数，低于此值视为坏文件")
    parser.add_argument("--min-lines", type=int, default=None, help="兼容旧参数；等价于 --min-rows")
    parser.add_argument("--force", action="store_true", help="覆盖已存在文件")
    return parser.parse_args(argv)


def validate_download_url(download_url: str) -> str:
    if not download_url.startswith(("http://", "https://")):
        raise SystemExit("ERROR: 现在只支持 get_timeseries_data 返回的 https 下载链接")
    return download_url


def derive_output_path(download_url: str, explicit_output_path: str | None) -> Path:
    if explicit_output_path:
        return Path(explicit_output_path).expanduser().resolve()
    file_name = Path(urlparse(download_url).path).name
    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise SystemExit(f"ERROR: 无法从下载链接推断可支持的结果文件名: {download_url}")
    return Path.cwd() / file_name


def validate_output(output_path: Path, min_rows: int) -> tuple[int, int]:
    size_bytes = output_path.stat().st_size
    suffix = output_path.suffix.lower()

    if suffix == ".csv":
        with output_path.open("r", encoding="utf-8") as handle:
            row_count = max(sum(1 for _ in handle) - 1, 0)
    elif suffix in {".xlsx", ".xls", ".xlsm"}:
        try:
            import pandas as pd
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise SystemExit("ERROR: 缺少 pandas 依赖，无法校验 Excel 结果文件") from exc
        df = pd.read_excel(output_path, sheet_name=0, engine="openpyxl")
        row_count = len(df)
    else:
        row_count = 0

    if row_count < min_rows:
        raise SystemExit(
            f"ERROR: 落盘后的结果文件数据行异常少，仅 {row_count} 行，疑似坏文件或响应残缺: {output_path}"
        )
    return size_bytes, row_count


def download_file(download_url: str, output_path: Path, timeout: int) -> None:
    try:
        with requests.get(download_url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise SystemExit(f"ERROR: 结果文件下载失败: {detail}") from exc
    except requests.RequestException as exc:
        raise SystemExit(f"ERROR: 结果文件下载失败: {exc}") from exc


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    download_url = validate_download_url(args.download_url)
    output_path = derive_output_path(download_url, args.output_path)
    if output_path.exists() and not args.force:
        raise SystemExit(f"ERROR: 输出文件已存在，请先删除或加 --force: {output_path}")

    min_rows = args.min_lines if args.min_lines is not None else args.min_rows
    download_file(download_url, output_path, timeout=args.timeout)
    size_bytes, row_count = validate_output(output_path, min_rows=min_rows)

    print(f"saved: {output_path}")
    print(f"bytes: {size_bytes}")
    print(f"rows: {row_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
