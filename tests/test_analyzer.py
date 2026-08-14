from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import analyzer
from analyzer import AnalysisConfig


class NormalizeAnalysisOutputTests(unittest.TestCase):
    def test_sorts_merges_and_renders_changes_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            existing = project / "src" / "b.py"
            existing.parent.mkdir(parents=True)
            existing.write_text("print('ok')\n", encoding="utf-8")
            output = {
                "changes": [
                    {
                        "path": "src/b.py",
                        "summary": "修改 B|逻辑",
                        "min_lines": 20,
                        "max_lines": 30,
                        "is_new": False,
                    },
                    {
                        "path": ".\\src\\a.py",
                        "summary": "增加 A 逻辑",
                        "min_lines": 10,
                        "max_lines": 15,
                        "is_new": True,
                    },
                    {
                        "path": "src/a.py",
                        "summary": "补充 A 测试",
                        "min_lines": 8,
                        "max_lines": 18,
                        "is_new": True,
                    },
                ]
            }

            report = analyzer._normalize_analysis_output(json.dumps(output, ensure_ascii=False), project)

            self.assertLess(report.index("`src/a.py`"), report.index("`src/b.py`"))
            self.assertIn("新文件；增加 A 逻辑；补充 A 测试。", report)
            self.assertIn("修改 B\\|逻辑。", report)
            self.assertIn("**合计：约 28–48 行**", report)

    def test_rejects_path_outside_repository(self) -> None:
        output = {
            "changes": [
                {
                    "path": "../secret.txt",
                    "summary": "无效路径",
                    "min_lines": 1,
                    "max_lines": 2,
                    "is_new": False,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "无效的项目相对路径"):
                analyzer._normalize_analysis_output(
                    json.dumps(output, ensure_ascii=False),
                    Path(directory),
                )


class AnalysisCacheTests(unittest.TestCase):
    def test_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache_directory = Path(directory)
            with patch.object(analyzer, "CACHE_DIRECTORY", cache_directory):
                analyzer._write_cached_report("abc", "report")
                self.assertEqual(analyzer._read_cached_report("abc"), "report")
                self.assertIsNone(analyzer._read_cached_report("missing"))

    def test_cache_key_uses_prototype_content_not_temporary_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-b", "dev", str(project)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(project), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(project), "config", "user.name", "Test"],
                check=True,
            )
            (project / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-m", "initial"], check=True, capture_output=True)

            requirement = root / "requirement.html"
            requirement.write_text("<p>requirement</p>", encoding="utf-8")
            prototype_a = root / "prototype-a.png"
            prototype_b = root / "prototype-b.png"
            prototype_a.write_bytes(b"same image")
            prototype_b.write_bytes(b"same image")
            skill_directory = root / "skill"
            skill_directory.mkdir()
            skill_file = skill_directory / "SKILL.md"
            skill_file.write_text("skill", encoding="utf-8")

            common = {
                "requirement_html_path": str(requirement),
                "project_path": str(project),
                "skill_path": str(skill_file),
                "model": "fixed-model",
                "reasoning_effort": "high",
            }
            key_a, head_a = analyzer._build_cache_key(
                AnalysisConfig(prototype_image=str(prototype_a), **common),
                skill_file,
            )
            key_b, head_b = analyzer._build_cache_key(
                AnalysisConfig(prototype_image=str(prototype_b), **common),
                skill_file,
            )

            self.assertEqual(head_a, head_b)
            self.assertEqual(key_a, key_b)


if __name__ == "__main__":
    unittest.main()
