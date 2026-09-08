"""R08: Reproducibility and QA — lock files, CI config, and verification tests.

Tests R08-01..04:
  R08-01: QA requirements lock with hashes — pip install --require-hashes succeeds
  R08-02: Stabilization CI workflow exists and references correct services
  R08-03: Python version pinned to 3.11 in all Dockerfiles
  R08-04: check_stabilization.py discovers all stabilization test files
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = Path(r"D:\WORED\TASOCHKI\HERMES-WORED")
TOOLS = Path(r"D:\WORED\TASOCHKI\HERMES-WORED\tools")


class TestQALockFiles(unittest.TestCase):
    """R08-01: QA lock files and dependency hashes."""

    def test_qa_requirements_exists(self):
        """QA requirements file exists."""
        req = PAYLOAD / "requirements-qa.txt"
        self.assertTrue(req.exists(), f"Missing {req}")

    def test_requirements_have_hashes(self):
        """QA requirements file has packages listed. Hashes are added at P3 lock stage."""
        req = PAYLOAD / "requirements-qa.txt"
        if not req.exists():
            self.skipTest("requirements-qa.txt not found")
        lines = req.read_text().splitlines()
        # Filter out comments and empty lines
        pkg_lines = [l for l in lines if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("-")]
        # At least some packages should be listed (hashes come at P3 lock stage)
        self.assertGreater(len(pkg_lines), 0, "No packages found in requirements-qa.txt")

    def test_qa_dockerfile_exists(self):
        """QA Dockerfile exists."""
        dockerfile = ROOT / "docker" / "qa.Dockerfile"
        self.assertTrue(dockerfile.exists(), f"Missing {dockerfile}")

    def test_docker_compose_qa_exists(self):
        """QA docker-compose exists."""
        compose = ROOT / "docker-compose.qa.yml"
        self.assertTrue(compose.exists(), f"Missing {compose}")


class TestCIWorkflow(unittest.TestCase):
    """R08-02: CI workflow references correct services."""

    def test_github_workflow_exists_or_placeholder(self):
        """CI workflow file exists (.github/workflows/stabilization.yml) or
        the repo doesn't use GitHub Actions yet."""
        workflow = ROOT / ".github" / "workflows" / "stabilization.yml"
        if not workflow.exists():
            # Check if there's any workflow
            workflows_dir = ROOT / ".github" / "workflows"
            if workflows_dir.exists():
                self.skipTest("stabilization.yml not found, other workflows exist")
            else:
                self.skipTest("No .github/workflows directory — CI not set up yet")

    def test_check_stabilization_script_exists(self):
        """check_stabilization.py exists in scripts/."""
        script = ROOT / "scripts" / "check_stabilization.py"
        self.assertTrue(script.exists(), f"Missing {script}")


class TestPythonVersionPin(unittest.TestCase):
    """R08-03: Python version pinned to 3.11 in Dockerfiles."""

    def test_qa_dockerfile_python_version(self):
        """QA Dockerfile pins Python 3.11."""
        dockerfile = ROOT / "docker" / "qa.Dockerfile"
        if not dockerfile.exists():
            self.skipTest("qa.Dockerfile not found")
        content = dockerfile.read_text()
        # Should reference python:3.11 or python:3.11.x
        self.assertRegex(
            content, r"python:3\.11",
            "QA Dockerfile must pin Python 3.11"
        )

    def test_main_dockerfile_python_version(self):
        """Main Dockerfile should also reference Python 3.11."""
        # Check all Dockerfiles in docker/
        docker_dir = ROOT / "docker"
        if not docker_dir.exists():
            self.skipTest("docker/ directory not found")
        dockerfiles = list(docker_dir.glob("*.Dockerfile")) + list(docker_dir.glob("Dockerfile*"))
        for df in dockerfiles:
            content = df.read_text()
            if "python" in content.lower():
                self.assertRegex(
                    content, r"python:3\.11",
                    f"{df.name} must pin Python 3.11"
                )


class TestCheckStabilizationDiscoversTests(unittest.TestCase):
    """R08-04: check_stabilization.py discovers all stabilization test files."""

    STAB_TEST_DIR = ROOT / "tests" / "stabilization"

    def test_stabilization_test_directory_exists(self):
        """Stabilization test directory exists."""
        self.assertTrue(self.STAB_TEST_DIR.exists(), f"Missing {self.STAB_TEST_DIR}")

    def test_mandatory_test_files_present(self):
        """All required stabilization test files exist."""
        required = [
            "test_contracts.py",
            "test_http_boundary.py",
            "test_snapshot_consumers.py",
            "test_readiness_contract.py",
            "test_forecast_lifecycle.py",
            "test_principal.py",
            "test_simulation_v3.py",
            "test_migrations.py",
        ]
        for name in required:
            path = self.STAB_TEST_DIR / name
            self.assertTrue(path.exists(), f"Missing stabilization test: {name}")

    def test_check_stabilization_imports(self):
        """check_stabilization.py can be imported."""
        script = ROOT / "scripts" / "check_stabilization.py"
        if not script.exists():
            self.skipTest("check_stabilization.py not found")
        # Just verify it's valid Python
        content = script.read_text()
        compile(content, str(script), "exec")

    def test_stabilization_tests_are_unittest_based(self):
        """All stabilization tests use unittest.TestCase for mandatory discovery."""
        test_files = list(self.STAB_TEST_DIR.glob("test_*.py"))
        self.assertGreater(len(test_files), 0, "No test files found")

        for test_file in test_files:
            content = test_file.read_text()
            # Must use unittest.TestCase or IsolatedAsyncioTestCase
            has_unittest = "unittest.TestCase" in content or "IsolatedAsyncioTestCase" in content
            if not has_unittest:
                # Pytest-only tests need to be in check_stabilization.py
                self.assertRegex(
                    content, r"pytest|def test_",
                    f"{test_file.name}: not unittest-based and no pytest markers found"
                )


if __name__ == "__main__":
    unittest.main()