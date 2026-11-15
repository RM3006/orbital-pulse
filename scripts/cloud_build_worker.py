"""Build the worker image in Cloud Build using a temporary private source bucket."""

from __future__ import annotations

import argparse
import io
import json
import os
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import google.auth
from google.auth.transport.requests import AuthorizedSession, Request

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"}


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def api(session: AuthorizedSession, method: str, url: str, **kwargs: object) -> dict[str, object]:
    response = session.request(method, url, timeout=60, **kwargs)
    if not response.ok:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            detail = response.text[:300]
        raise RuntimeError(
            f"Google API {method} {url.split('?')[0]} returned HTTP "
            f"{response.status_code}: {detail}"
        )
    return response.json() if response.content else {}


def source_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in [ROOT / "Dockerfile.worker", ROOT / "pyproject.toml", ROOT / "uv.lock"]:
            archive.add(path, arcname=path.relative_to(ROOT).as_posix())
        for path in sorted((ROOT / "pipelines" / "launch_atlas").rglob("*.py")):
            if "__pycache__" not in path.parts:
                archive.add(path, arcname=path.relative_to(ROOT).as_posix())
    return buffer.getvalue()


def set_bucket_reader(session: AuthorizedSession, bucket: str, service_account: str) -> None:
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/iam"
    policy = api(session, "GET", url, params={"optionsRequestedPolicyVersion": 3})
    member = f"serviceAccount:{service_account}"
    raw_bindings = policy.get("bindings")
    if not isinstance(raw_bindings, list):
        raw_bindings = []
        policy["bindings"] = raw_bindings
    bindings = raw_bindings
    binding = next(
        (item for item in bindings if item.get("role") == "roles/storage.objectViewer"), None
    )
    if binding is None:
        bindings.append({"role": "roles/storage.objectViewer", "members": [member]})
    else:
        raw_members = binding.get("members")
        if not isinstance(raw_members, list):
            raw_members = []
            binding["members"] = raw_members
        if member not in raw_members:
            raw_members.append(member)
    api(
        session,
        "PUT",
        url,
        params={"optionsRequestedPolicyVersion": 3},
        json=policy,
    )


def remove_source_bucket(session: AuthorizedSession, bucket: str) -> None:
    object_url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/worker-source.tar.gz"
    response = session.delete(object_url, timeout=30)
    if response.status_code not in (200, 204, 404):
        raise RuntimeError(
            f"Could not remove temporary source object (HTTP {response.status_code})"
        )
    response = session.delete(f"https://storage.googleapis.com/storage/v1/b/{bucket}", timeout=30)
    if response.status_code not in (200, 204, 404):
        raise RuntimeError(
            f"Could not remove temporary source bucket (HTTP {response.status_code})"
        )


def build_id_from_operation(operation: dict[str, object]) -> str | None:
    """Return the build ID published in Cloud Build's long-running operation metadata."""
    metadata = operation.get("metadata")
    if isinstance(metadata, dict):
        build = metadata.get("build")
        if isinstance(build, dict) and build.get("id"):
            return str(build["id"])
    response = operation.get("response")
    if isinstance(response, dict):
        if response.get("id"):
            return str(response["id"])
        name = response.get("name")
        if isinstance(name, str) and "/builds/" in name:
            return name.rsplit("/builds/", 1)[1]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local")
    parser.add_argument("--tag", default=None)
    parser.add_argument("--max-wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    load_env_file(args.env_file)
    project = os.environ["GCP_PROJECT_ID"]
    location = os.environ["GCP_LOCATION"]
    tag = args.tag or datetime.now(UTC).strftime("test-%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    image = f"{location}-docker.pkg.dev/{project}/space-launch-atlas/ll2-worker:{tag}"
    bucket = f"sla-build-src-{uuid4().hex[:12]}"
    build_sa = f"sla-worker-build@{project}.iam.gserviceaccount.com"
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    session = AuthorizedSession(credentials)
    bucket_created = False
    build_submission_uncertain = False
    build_accepted = False
    build_terminal = False

    try:
        api(
            session,
            "POST",
            "https://storage.googleapis.com/storage/v1/b",
            params={"project": project},
            json={
                "name": bucket,
                "location": location,
                "uniformBucketLevelAccess": {"enabled": True},
                "publicAccessPrevention": "enforced",
                "lifecycle": {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 1}}]},
            },
        )
        bucket_created = True
        set_bucket_reader(session, bucket, build_sa)
        upload = session.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
            params={"uploadType": "media", "name": "worker-source.tar.gz"},
            data=source_archive(),
            headers={"Content-Type": "application/gzip"},
            timeout=120,
        )
        if not upload.ok:
            raise RuntimeError(f"Temporary source upload failed (HTTP {upload.status_code})")
        source_generation = upload.json()["generation"]

        build_submission_uncertain = True
        try:
            operation = api(
                session,
                "POST",
                f"https://cloudbuild.googleapis.com/v1/projects/{project}/locations/{location}/builds",
                json={
                    "source": {
                        "storageSource": {
                            "bucket": bucket,
                            "object": "worker-source.tar.gz",
                            "generation": source_generation,
                        }
                    },
                    "steps": [
                        {
                            "name": "gcr.io/cloud-builders/docker",
                            "args": ["build", "-f", "Dockerfile.worker", "-t", image, "."],
                        }
                    ],
                    "images": [image],
                    "serviceAccount": f"projects/{project}/serviceAccounts/{build_sa}",
                    "options": {"logging": "CLOUD_LOGGING_ONLY"},
                    "timeout": "1500s",
                    "tags": ["orbital-pulse-worker-once"],
                },
            )
        except RuntimeError:
            # An explicit HTTP rejection means no build was accepted.
            build_submission_uncertain = False
            raise
        build_accepted = True
        build_id = build_id_from_operation(operation)
        if not build_id:
            raise RuntimeError(
                "Cloud Build accepted the request but returned no build ID; source bucket retained"
            )
        build_submission_uncertain = False
        build_name = f"projects/{project}/locations/{location}/builds/{build_id}"
        deadline = time.monotonic() + args.max_wait_seconds
        while time.monotonic() < deadline:
            status = str(
                api(session, "GET", f"https://cloudbuild.googleapis.com/v1/{build_name}")["status"]
            )
            print(json.dumps({"build_status": status}), flush=True)
            if status in TERMINAL:
                build_terminal = True
                if status != "SUCCESS":
                    raise RuntimeError(f"Cloud Build ended with status {status}")
                print(json.dumps({"status": "image_built", "image_uri": image}), flush=True)
                return 0
            time.sleep(5)
        raise TimeoutError(
            "Cloud Build is still running; its temporary source bucket was preserved"
        )
    finally:
        if (
            bucket_created
            and not build_submission_uncertain
            and (not build_accepted or build_terminal)
        ):
            remove_source_bucket(session, bucket)
        elif bucket_created:
            print(json.dumps({"temporary_source_bucket_preserved": bucket}), flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__, "detail": str(exc)[:400]}
            ),
            file=__import__("sys").stderr,
        )
        raise SystemExit(1) from exc
