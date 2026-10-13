import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
UPLOAD_PATH = "/engine/api/v1/report/uploadReport"


def upload_examples():
    for path in sorted(SKILLS.rglob("*.md")):
        for block in re.findall(r"^[ \t]*```bash\n(.*?)^[ \t]*```", path.read_text(), re.M | re.S):
            if UPLOAD_PATH in block:
                yield path, textwrap.dedent(block)


class ReportUploadExamplesTest(unittest.TestCase):
    def run_example(self, block, overrides=None):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "curl-call.json"
            curl = Path(directory) / "curl"
            curl.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "Path(os.environ['CURL_CAPTURE']).write_text(json.dumps("
                "{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}))\n"
                "print('{\"success\":true,\"data\":\"/protected/report.html\"}')\n"
            )
            curl.chmod(0o700)
            environment = {
                **os.environ,
                "PATH": directory,
                "CURL_CAPTURE": str(capture),
                "HYDROS_ENGINE_BASE_URL": "https://api.example.test/",
                "HYDROS_ACCESS_TOKEN": "test-token-never-valid",
                "BIZ_SCENE_INSTANCE_ID": "TASK_test-001",
            }
            for name, value in (overrides or {}).items():
                if value is None:
                    environment.pop(name, None)
                else:
                    environment[name] = value
            result = subprocess.run(
                [shutil.which("bash"), "--noprofile", "--norc", "-c", block],
                env=environment, capture_output=True, text=True, timeout=5,
            )
            call = json.loads(capture.read_text()) if capture.exists() else None
            return result, call

    def test_no_retired_anonymous_upload_in_skill_documents(self):
        for path in SKILLS.rglob("*.md"):
            with self.subTest(document=str(path.relative_to(ROOT))):
                self.assertNotIn("/file/anonymous/upload", path.read_text())

    def test_examples_use_authenticated_https_multipart_without_redirects(self):
        examples = list(upload_examples())
        self.assertEqual({path.parent.name for path, _ in examples}, {
            "hydros-engine-skill-executor", "hydros-engine-skill-report-xuhonghe",
        })
        for path, block in examples:
            with self.subTest(document=str(path.relative_to(ROOT))):
                result, call = self.run_example(block)
                self.assertEqual(result.returncode, 0)
                self.assertIsNotNone(call)
                args = call["argv"]
                self.assertIn("https://api.example.test" + UPLOAD_PATH + "?biz_scene_instance_id=TASK_test-001", args)
                self.assertEqual(args[args.index("--proto") + 1], "=https")
                self.assertIn("--fail-with-body", args)
                self.assertEqual(args[args.index("--request") + 1], "POST")
                self.assertEqual(args[args.index("--header") + 1], "@-")
                self.assertEqual(args[args.index("--form") + 1], 'file=@"output/TASK_test-001/report/simulation_report.html"')
                self.assertEqual(call["stdin"], "Authorization: Bearer test-token-never-valid\n")
                self.assertFalse(any(arg in {"-L", "--location", "--location-trusted", "-k", "--insecure"} for arg in args))
                self.assertFalse(any("test-token-never-valid" in arg for arg in args))
                self.assertNotIn("test-token-never-valid", result.stdout + result.stderr)

    def test_missing_configuration_and_invalid_task_never_call_curl(self):
        for path, block in upload_examples():
            for name in ("HYDROS_ENGINE_BASE_URL", "HYDROS_ACCESS_TOKEN", "BIZ_SCENE_INSTANCE_ID"):
                for value in (None, ""):
                    with self.subTest(document=str(path.relative_to(ROOT)), missing=name, empty=value == ""):
                        result, call = self.run_example(block, {name: value})
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIsNone(call)
            for invalid in (
                {"BIZ_SCENE_INSTANCE_ID": "TASK&other=bad"},
                {"HYDROS_ENGINE_BASE_URL": "http://api.example.test"},
                {"HYDROS_ENGINE_BASE_URL": "https://"},
            ):
                with self.subTest(document=str(path.relative_to(ROOT)), invalid=invalid):
                    result, call = self.run_example(block, invalid)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIsNone(call)


if __name__ == "__main__":
    unittest.main()
