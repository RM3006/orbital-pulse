"""Explicit, interactive BigQuery-only user ADC setup; imports never authenticate."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

BIGQUERY_SCOPE = "https://www.googleapis.com/auth/bigquery"


def adc_path() -> Path:
    """Use the standard ADC location, outside this project's data/config files."""
    configured = os.environ.get("CLOUDSDK_CONFIG")
    if configured:
        directory = Path(configured)
    elif os.name == "nt":
        directory = Path(os.environ["APPDATA"]) / "gcloud"
    else:
        directory = Path.home() / ".config" / "gcloud"
    return directory / "application_default_credentials.json"


def read_desktop_client(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    client = config.get("installed", {})
    if (
        not client.get("client_id", "").endswith(".apps.googleusercontent.com")
        or not client.get("client_secret")
        or client.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth"
        or client.get("token_uri") != "https://oauth2.googleapis.com/token"
    ):
        raise ValueError("Use an unmodified Google Desktop app OAuth client JSON download.")
    return {"installed": client}


def build_adc_payload(credentials: Credentials, project_id: str) -> dict[str, Any]:
    """Require exactly the approved scope and a refreshable user credential."""
    requested = set(credentials.scopes or ())
    granted = set(credentials.granted_scopes or requested)
    if requested != {BIGQUERY_SCOPE} or granted != {BIGQUERY_SCOPE}:
        raise ValueError("Google did not grant exactly BigQuery-only access; ADC was not saved.")
    if not credentials.refresh_token or not credentials.client_id or not credentials.client_secret:
        raise ValueError(
            "The OAuth response has no refreshable user credential; ADC was not saved."
        )
    payload = json.loads(credentials.to_json(strip=["token", "id_token", "expiry"]))
    payload.update(type="authorized_user", quota_project_id=project_id)
    return payload


def save_adc(payload: dict[str, Any], destination: Path) -> None:
    """Atomically add the credential without replacing an existing user's ADC."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".adc-", dir=destination.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Windows rename and POSIX hard link fail if destination already exists.
        if os.name == "nt":
            os.rename(temporary_path, destination)
        else:
            os.link(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Save BigQuery-only local user ADC.")
    parser.add_argument("--client-secrets", required=True, type=Path)
    parser.add_argument("--project-id", default=os.environ.get("GCP_PROJECT_ID"))
    args = parser.parse_args(argv)
    if not args.project_id:
        parser.error("Pass --project-id or set GCP_PROJECT_ID.")
    destination = adc_path()
    if destination.exists():
        parser.error(
            "ADC already exists. It was preserved; explicitly review it before replacing it."
        )
    try:
        config = read_desktop_client(args.client_secrets)
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_config(
            config, scopes=[BIGQUERY_SCOPE], autogenerate_code_verifier=True
        )
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            timeout_seconds=600,
            authorization_prompt_message="Complete the Google sign-in in your browser.",
            success_message="Google authorization returned. Check the terminal for ADC validation.",
            access_type="offline",
            prompt="consent",
            include_granted_scopes="false",
        )
        if not isinstance(credentials, Credentials):
            raise ValueError("Expected a Google user OAuth credential; ADC was not saved.")
        # Validate both the initial grant and its refresh before writing anything.
        build_adc_payload(credentials, args.project_id)
        credentials.refresh(Request())
        payload = build_adc_payload(credentials, args.project_id)
        save_adc(payload, destination)
    except ImportError:
        parser.exit(1, "Install the locked warehouse dependency group first.\n")
    except Exception as exc:
        # OAuth exceptions can contain authorization codes or tokens. Print only the class.
        parser.exit(1, f"ADC setup failed ({type(exc).__name__}); no existing ADC was replaced.\n")
    print(f"Saved BigQuery-only ADC at {destination}. OAuth refresh succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
