import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "skills/hydros-engine-skill-report-daduhe/scripts/upload_report.py"
spec = importlib.util.spec_from_file_location("report_upload", SCRIPT)
upload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upload)


class UploadAuthenticationTest(unittest.TestCase):
    def test_invalid_origin_or_missing_credentials_never_reads_file_or_sends(self):
        with patch.object(upload, "build_multipart_body") as body, patch.object(upload.urllib.request, "build_opener") as opener:
            with patch.dict(os.environ, {"HYDROS_ACCESS_TOKEN": "token"}):
                for origin in ("http://api.test", "https://user:secret@api.test", "https://api.test/other", ""):
                    with self.assertRaises(ValueError):
                        upload.upload_report("TASK_001", Path("unused"), origin, 5)
            with patch.dict(os.environ, {"HYDROS_ACCESS_TOKEN": ""}):
                with self.assertRaises(ValueError):
                    upload.upload_report("TASK_001", Path("unused"), "https://api.test", 5)
            body.assert_not_called(); opener.assert_not_called()

    def test_standard_bearer_configured_origin_and_no_redirect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"; path.write_text("<html>report</html>")
            with patch.dict(os.environ, {"HYDROS_ACCESS_TOKEN": "standard-token"}), patch.object(upload.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({"success": True}).encode()
                self.assertTrue(upload.upload_report("TASK_001", path, "https://192.0.2.1:30483", 5)["success"])
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, "https://192.0.2.1:30483/engine/api/v1/report/uploadReport?biz_scene_instance_id=TASK_001")
                self.assertEqual(request.get_header("Authorization"), "Bearer standard-token")
                self.assertIsNone(opener.call_args.args[0].redirect_request(request, None, 302, "", {}, "https://foreign.test"))


if __name__ == "__main__":
    unittest.main()
