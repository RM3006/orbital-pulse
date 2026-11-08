import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_dbt_project_parses_offline_with_placeholder_profile(tmp_path: Path) -> None:
    dbt = shutil.which("dbt")
    if dbt is None:
        pytest.skip("Optional warehouse dependency group is not installed")

    project_dir = Path(__file__).parents[1] / "models"
    profiles_dir = tmp_path / "profiles"
    target_dir = tmp_path / "target"
    log_dir = tmp_path / "logs"
    profiles_dir.mkdir()
    shutil.copyfile(
        project_dir / "profiles.yml.example",
        profiles_dir / "profiles.yml",
    )
    environment = os.environ.copy()
    environment["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "false"
    environment.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

    result = subprocess.run(
        [
            dbt,
            "parse",
            "--project-dir",
            str(project_dir),
            "--profiles-dir",
            str(profiles_dir),
            "--target-path",
            str(target_dir),
            "--log-path",
            str(log_dir),
            "--no-partial-parse",
            "--no-version-check",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
    models = [node for node in manifest["nodes"].values() if node["resource_type"] == "model"]
    tests = [node for node in manifest["nodes"].values() if node["resource_type"] == "test"]
    assert len(models) == 26
    assert len(tests) == 80
    assert len(manifest["sources"]) == 11
