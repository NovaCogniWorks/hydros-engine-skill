#!/usr/bin/env python3
"""
上传 Hydros HTML 报告并输出 S3 可访问链接。

文件通过 api.hydroos.pub 的 OpenAPI 入口上传，成功后由服务端返回
https://s3.hydroos.pub/report/... 形式的最终报告地址。

用法:
    python3 scripts/upload_report.py <biz_scene_instance_id> <html_report_path>
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import uuid
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_UPLOAD_URL_TEMPLATE = (
    "https://api.hydroos.pub/openapi/engine/api/v1/file/anonymous/upload/{biz_scene_instance_id}"
)
DEFAULT_REPORT_URL_PREFIX = "https://s3.hydroos.pub/report/"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="上传 Hydros HTML 报告并返回访问链接")
    parser.add_argument("biz_scene_instance_id", help="仿真任务实例 ID")
    parser.add_argument("html_report_path", help="simulation_report.html 本地路径")
    parser.add_argument(
        "--upload-url-template",
        default=DEFAULT_UPLOAD_URL_TEMPLATE,
        help="上传接口模板，需包含 {biz_scene_instance_id}",
    )
    parser.add_argument("--timeout", type=int, default=120, help="HTTP 超时时间，单位秒")
    return parser.parse_args(argv)


def build_multipart_body(field_name: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----HydrosReportBoundary{uuid.uuid4().hex}"
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    filename = file_path.name
    file_bytes = file_path.read_bytes()

    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(file_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), boundary


def upload_report(
    biz_scene_instance_id: str,
    html_report_path: Path,
    upload_url_template: str,
    timeout: int,
) -> dict:
    upload_url = upload_url_template.format(biz_scene_instance_id=biz_scene_instance_id)
    body, boundary = build_multipart_body("file", html_report_path)
    request = urllib.request.Request(
        upload_url,
        data=body,
        method="POST",
        headers={
            "Accept": "*/*",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw_text = response.read().decode("utf-8")
    return json.loads(raw_text)


def main() -> None:
    args = parse_args(sys.argv[1:])
    html_report_path = Path(args.html_report_path).resolve()
    if not html_report_path.exists():
        raise FileNotFoundError(f"报告文件不存在: {html_report_path}")
    if not html_report_path.is_file():
        raise ValueError(f"报告路径不是文件: {html_report_path}")

    try:
        result = upload_report(
            args.biz_scene_instance_id,
            html_report_path,
            args.upload_url_template,
            args.timeout,
        )
    except urllib.error.HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"上传失败，HTTP {exc.code}: {response_text}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"上传失败，网络错误: {exc}") from exc

    if not result.get("success"):
        raise RuntimeError(f"上传接口返回失败: {json.dumps(result, ensure_ascii=False)}")

    report_url = result.get("data")
    if not isinstance(report_url, str) or not report_url.strip():
        raise RuntimeError(f"上传成功但未返回有效链接: {json.dumps(result, ensure_ascii=False)}")
    if (
        args.upload_url_template == DEFAULT_UPLOAD_URL_TEMPLATE
        and not report_url.startswith(DEFAULT_REPORT_URL_PREFIX)
    ):
        raise RuntimeError(
            "上传成功但返回地址不属于预期的 S3 报告目录: "
            f"{json.dumps(result, ensure_ascii=False)}"
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(report_url)


if __name__ == "__main__":
    main()
