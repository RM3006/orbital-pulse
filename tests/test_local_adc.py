import json
import os
from pathlib import Path

import pytest
from launch_atlas.warehouse.local_adc import (
    BIGQUERY_SCOPE,
    build_adc_payload,
    read_desktop_client,
    save_adc,
)

oauth = pytest.importorskip("google.oauth2.credentials")
auth = pytest.importorskip("google.auth")


def credentials(scopes: list[str] | None = None, refresh_token: str | None = "fake-refresh"):
    return oauth.Credentials(
        token="fake-access-token",
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test.apps.googleusercontent.com",
        client_secret="fake-client-secret",
        scopes=[BIGQUERY_SCOPE],
        granted_scopes=scopes or [BIGQUERY_SCOPE],
    )


def test_saved_adc_is_readable_by_google_auth_without_network(tmp_path: Path) -> None:
    destination = tmp_path / "application_default_credentials.json"
    save_adc(build_adc_payload(credentials(), "example-project"), destination)
    payload = json.loads(destination.read_text())
    assert "token" not in payload
    assert "fake-access-token" not in destination.read_text()
    loaded, _ = auth.load_credentials_from_file(str(destination), scopes=[BIGQUERY_SCOPE])
    assert loaded.refresh_token == "fake-refresh"
    assert loaded.scopes == [BIGQUERY_SCOPE]
    assert loaded.quota_project_id == "example-project"
    assert sorted(path.name for path in tmp_path.iterdir()) == [destination.name]


@pytest.mark.parametrize("extra", ["cloud-platform", "drive", "sqlservice.login"])
def test_unapproved_scope_is_rejected_before_saving(extra: str) -> None:
    with pytest.raises(ValueError, match="exactly BigQuery-only"):
        build_adc_payload(
            credentials([BIGQUERY_SCOPE, f"https://www.googleapis.com/auth/{extra}"]),
            "example-project",
        )


def test_missing_refresh_token_cannot_produce_adc() -> None:
    with pytest.raises(ValueError, match="refreshable"):
        build_adc_payload(credentials(refresh_token=None), "example-project")


def test_existing_adc_survives_setup_attempt_unchanged(tmp_path: Path) -> None:
    destination = tmp_path / "application_default_credentials.json"
    original = b'{"existing":"preserve exactly"}\n'
    destination.write_bytes(original)
    with pytest.raises(FileExistsError):
        save_adc(build_adc_payload(credentials(), "example-project"), destination)
    assert destination.read_bytes() == original
    assert len(list(tmp_path.iterdir())) == 1


def test_client_json_must_be_desktop_app_using_google_endpoints(tmp_path: Path) -> None:
    source = tmp_path / "client.json"
    source.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test.apps.googleusercontent.com",
                    "client_secret": "fake-client-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://untrusted.example/token",
                }
            }
        )
    )
    with pytest.raises(ValueError, match="unmodified Google Desktop"):
        read_desktop_client(source)


def test_dbt_adapter_recognizes_the_profile_scope_restriction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yaml = pytest.importorskip("yaml")
    adapter = pytest.importorskip("dbt.adapters.bigquery.credentials")
    from dbt.config.renderer import ProfileRenderer
    from dbt_common.context import set_invocation_context

    monkeypatch.setenv("DBT_MAXIMUM_BYTES_BILLED", "100000000")
    set_invocation_context(os.environ)
    profile = Path(__file__).parents[1] / "models" / "profiles.yml.example"
    output = yaml.safe_load(profile.read_text())["space_launch_atlas"]["outputs"]["local"]
    output = ProfileRenderer().render_data(output)
    parsed = adapter.BigQueryCredentials.from_dict(
        adapter.BigQueryCredentials.translate_aliases(output)
    )
    assert parsed.maximum_bytes_billed == 100_000_000
    assert tuple(parsed.scopes) == (BIGQUERY_SCOPE,)
