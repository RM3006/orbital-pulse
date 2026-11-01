"""One-time Phase 0 probe; at most eight HTTP GETs, no retries or credentials.

Writes unchanged public response bytes and acquisition metadata under ignored data/.
This is an evidence collection script, not the operational ingestion implementation.
"""

import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

ROOT = Path("data/source-spike")
BASE = "https://ll.thespacedevs.com/2.3.0/"
QUERIES = {
    "schema": "json",
    "throttle": "api-throttle/",
    "historical": "launches/previous/?limit=2&mode=normal&ordering=net&include_suborbital=false",
    "recent": "launches/previous/?limit=3&mode=detailed&ordering=-net&include_suborbital=false",
    "upcoming": (
        "launches/upcoming/?limit=10&mode=detailed&ordering=net&include_suborbital=false"
        "&net__gte=2026-11-01T00:00:00Z&net__lte=2027-01-30T00:00:00Z"
    ),
    "statuses": "config/launch_statuses/?limit=100",
    "updated_filter": (
        "launches/?limit=1&mode=normal&include_suborbital=false"
        "&last_updated__gte=2099-01-01T00:00:00Z&ordering=last_updated"
    ),
    "recent_normal": (
        "launches/previous/?limit=1&mode=normal&ordering=-net&include_suborbital=false"
    ),
}


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT / "manifest.json").exists():
        raise SystemExit("Evidence already exists; inspect it instead of repeating requests.")
    manifest = []
    for name, suffix in QUERIES.items():
        url = BASE + suffix
        observed = datetime.now(UTC).isoformat()
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "SpaceLaunchAtlas/0.1 (local research bootstrap; Romain Menant)",
            },
        )
        try:
            response = urlopen(request, timeout=60)
        except HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            retry_after = response.headers.get("Retry-After")
        artifact = ROOT / f"{name}.json.gz"
        artifact.write_bytes(gzip.compress(body, mtime=0))
        entry = {
            "source": "ll2",
            "api_version": "2.3.0",
            "endpoint": urlsplit(url).path,
            "query": parse_qs(urlsplit(url).query),
            "observed_at_utc": observed,
            "http_status": status,
            "content_type": content_type,
            "retry_after": retry_after,
            "sha256": hashlib.sha256(body).hexdigest(),
            "artifact": str(artifact),
        }
        manifest.append(entry)
        (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if status != 200 or "json" not in content_type:
            print(json.dumps(entry))
            break
        payload = json.loads(body)
        if name == "schema":
            paths = payload.get("paths", {})
            for endpoint, spec in paths.items():
                if endpoint.endswith("/launches/"):
                    print("LAUNCH_FILTERS", json.dumps(spec.get("get", {}).get("parameters", [])))
        elif name == "throttle":
            print("THROTTLE", json.dumps(payload))
        else:
            print(name, "count=", payload.get("count"), "next=", payload.get("next"))
            for item in payload.get("results", []):
                if name == "statuses":
                    print(json.dumps(item))
                else:
                    print(
                        json.dumps(
                            {
                                k: item.get(k)
                                for k in (
                                    "id",
                                    "name",
                                    "net",
                                    "last_updated",
                                    "net_precision",
                                    "status",
                                    "orbital_launch_attempt_count",
                                )
                            }
                        )
                    )
            if name == "recent":
                print("RECENT_RECORD", json.dumps(payload["results"][0]))


if __name__ == "__main__":
    main()
