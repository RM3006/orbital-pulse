from pathlib import Path

import pytest
from launch_atlas.cli import main
from launch_atlas.config import Settings


def test_help_and_doctor_without_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert main(["doctor", "--offline"]) == 0
    assert '"network_used": false' in capsys.readouterr().out


def test_configuration_refuses_dev_source_and_cloud_mode() -> None:
    with pytest.raises(ValueError):
        Settings(base_url="https://lldev.thespacedevs.com/2.3.0/")
    with pytest.raises(ValueError):
        Settings.model_validate({"mode": "cloud"})
    with pytest.raises(ValueError):
        Settings(max_requests=16)


def test_cli_offline_replay(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures/ll2_public_sample.json"
    assert (
        main(
            [
                "import-raw",
                "--input",
                str(fixture),
                "--run-id",
                "cli-offline",
                "--observed-at",
                "2026-11-01T16:52:44Z",
                "--orbital-only",
                "--data-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert main(["replay", "--run-id", "cli-offline", "--data-dir", str(tmp_path)]) == 0
