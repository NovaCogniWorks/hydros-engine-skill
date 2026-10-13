#!/usr/bin/env python3
"""
上传 Hydros HTML 报告并输出 S3 可访问链接。

通过当前环境的统一业务接口上传。HYDROS_ENGINE_BASE_URL 指定 HTTPS 入口，
HYDROS_ACCESS_TOKEN 提供标准访问令牌；需要 report:write 和该任务的资源权限。

用法:
    python3 scripts/upload_report.py <biz_scene_instance_id> <html_report_path>
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import urllib.parse
import sys
import uuid
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="上传 Hydros HTML 报告并返回访问链接")
    parser.add_argument("biz_scene_instance_id", help="仿真任务实例 ID")
    parser.add_argument("html_report_path", help="simulation_report.html 本地路径")
    parser.add_argument("--api-base-url", default=os.getenv("HYDROS_ENGINE_BASE_URL", ""), help="当前环境 HTTPS API 入口")
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
    api_base_url: str,
    timeout: int,
) -> dict:
    origin = urllib.parse.urlsplit(api_base_url)
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.path not in ("", "/") or origin.query or origin.fragment:
        raise ValueError("必须配置当前环境的 HTTPS API 入口")
    token = os.getenv("HYDROS_ACCESS_TOKEN", "")
    if not token or any(char.isspace() for char in token) or "," in token:
        raise ValueError("需要 HYDROS_ACCESS_TOKEN 标准访问令牌")
    upload_url = api_base_url.rstrip("/") + "/engine/api/v1/report/uploadReport?" + urllib.parse.urlencode({"biz_scene_instance_id": biz_scene_instance_id})
    body, boundary = build_multipart_body("file", html_report_path)
    request = urllib.request.Request(
        upload_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
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
            args.api_base_url,
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

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(report_url)


if __name__ == "__main__":
    main()
