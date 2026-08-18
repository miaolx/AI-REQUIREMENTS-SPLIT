from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import app


class LocalDevRepositoryTests(unittest.TestCase):
    def test_accepts_local_dev_and_returns_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            repository.mkdir()
            subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repository, check=True, capture_output=True)
            nested = repository / "src" / "feature"
            nested.mkdir(parents=True)

            resolved = app._resolve_local_dev_repository(nested)

            self.assertEqual(resolved, repository.resolve())

    def test_rejects_non_dev_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            repository.mkdir()
            subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "feature"], cwd=repository, check=True, capture_output=True)

            with self.assertRaises(HTTPException) as raised:
                app._resolve_local_dev_repository(repository)

            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("实际分支: feature", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
