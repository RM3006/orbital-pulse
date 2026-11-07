import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from pydantic import BaseModel, Field

from launch_atlas.config import Settings
from launch_atlas.storage.local import LocalStore, atomic_json, read_json


class SourceError(ValueError):
    pass


class PauseRun(SourceError):
    def __init__(self, reason: str, retry_at: float | None = None):
        super().__init__(reason)
        self.retry_at = retry_at


class Page(BaseModel):
    count: int = Field(ge=0)
    next: str | None
    results: list[dict[str, Any]]


class RequestBudget:
    """One cap shared by all clients and source components in an invocation."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    def reserve(self) -> None:
        if self.used >= self.limit:
            raise PauseRun("budget_stopped")
        self.used += 1


def safe_url(url: str, base_url: str) -> str:
    parsed = urlsplit(url)
    base = urlsplit(base_url)
    if (
        parsed.scheme != base.scheme
        or parsed.netloc != base.netloc
        or parsed.fragment
        or parsed.path
        not in (
            base.path + "launches/",
            base.path + "launches/previous/",
            base.path + "launches/upcoming/",
        )
        or unquote(parsed.path) != parsed.path
    ):
        raise SourceError("Unsafe source or pagination URL")
    allowed = {
        "limit",
        "offset",
        "mode",
        "ordering",
        "include_suborbital",
        "net__gte",
        "net__lt",
        "net__lte",
        "last_updated__gte",
        "last_updated__lte",
    }
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) - allowed or any(len(values) != 1 for values in query.values()):
        raise SourceError("Unrecognized query parameter; credentials must never appear in URLs")
    scope = query.get("include_suborbital")
    if scope not in (["false"], ["true"]) or query.get("mode") != ["detailed"]:
        raise SourceError("Only explicitly orbital, detailed queries can feed this parser")
    if scope == ["true"]:
        if (
            parsed.path != base.path + "launches/previous/"
            or "last_updated__gte" not in query
            or "last_updated__lte" not in query
        ):
            raise SourceError(
                "Broadened scope is restricted to bounded previous-launch corrections"
            )
        try:
            lower = datetime.fromisoformat(query["last_updated__gte"][0].replace("Z", "+00:00"))
            upper = datetime.fromisoformat(query["last_updated__lte"][0].replace("Z", "+00:00"))
            if (
                lower.tzinfo is None
                or upper.tzinfo is None
                or upper <= lower
                or (upper - lower).total_seconds() > 31 * 86400
            ):
                raise ValueError
        except ValueError:
            raise SourceError(
                "Broadened-scope correction query must span at most 31 days"
            ) from None
    try:
        if not 1 <= int(query.get("limit", ["0"])[0]) <= 100:
            raise ValueError
        if int(query.get("offset", ["0"])[0]) < 0:
            raise ValueError
    except ValueError:
        raise SourceError("Invalid pagination limit or offset") from None
    return url


def validate_next(next_url: str | None, initial: str, base_url: str) -> None:
    if next_url is None:
        return
    safe_url(next_url, base_url)
    first, following = urlsplit(initial), urlsplit(next_url)
    original_query, next_query = parse_qs(first.query), parse_qs(following.query)
    original_query.pop("offset", None)
    next_query.pop("offset", None)
    if first.path != following.path or original_query != next_query:
        raise SourceError("Pagination changed the source window or endpoint")
    if int(parse_qs(following.query).get("offset", ["0"])[0]) <= int(
        parse_qs(first.query).get("offset", ["0"])[0]
    ):
        raise SourceError("Pagination offset did not advance")


def retry_seconds(value: str | None, now: float) -> float:
    try:
        return max(0, float(value or "60"))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value or "").timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 60


class LL2Client:
    def __init__(
        self,
        settings: Settings,
        store: LocalStore,
        *,
        transport: httpx.BaseTransport | None = None,
        budget: RequestBudget | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings, self.store = settings, store
        self.clock, self.sleep = clock, sleep
        self.budget = budget or RequestBudget(settings.max_requests)
        self.http = httpx.Client(
            headers={"Accept": "application/json", "User-Agent": settings.user_agent},
            timeout=45,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self.http.close()

    @property
    def used(self) -> int:
        return self.budget.used

    def reserve(self) -> None:
        if self.budget.used >= self.budget.limit:
            raise PauseRun("budget_stopped")
        path = self.store.root / "quota.json"
        ledger = read_json(path) if path.exists() else {"requests": [], "retry_at": 0}
        now = self.clock()
        requests = [stamp for stamp in ledger["requests"] if stamp > now - 3600]
        if ledger["retry_at"] > now:
            raise PauseRun("throttled", ledger["retry_at"])
        if len(requests) >= 15:
            raise PauseRun("quota_stopped", min(requests) + 3600)
        requests.append(now)
        atomic_json(path, {"requests": requests, "retry_at": ledger["retry_at"]})
        self.budget.reserve()

    def fetch(
        self,
        url: str,
        record: Callable[[httpx.Response | None, str, str, str], None],
    ) -> Page:
        safe_url(url, self.settings.base_url)
        for attempt in range(3):
            self.reserve()
            requested_at = datetime.fromtimestamp(self.clock(), UTC).isoformat()
            try:
                response = self.http.get(url)
            except httpx.TransportError:
                record(
                    None,
                    url,
                    datetime.fromtimestamp(self.clock(), UTC).isoformat(),
                    requested_at,
                )
                if attempt == 2:
                    raise SourceError("Transport failure after bounded retries") from None
                self.sleep(2**attempt)
                continue
            # Observation is receipt time; save raw evidence before parsing.
            record(
                response,
                url,
                datetime.fromtimestamp(self.clock(), UTC).isoformat(),
                requested_at,
            )
            if response.status_code == 429:
                retry_at = self.clock() + retry_seconds(
                    response.headers.get("Retry-After"), self.clock()
                )
                path = self.store.root / "quota.json"
                ledger = read_json(path)
                ledger["retry_at"] = retry_at
                atomic_json(path, ledger)
                raise PauseRun("throttled", retry_at)
            if response.status_code in (500, 502, 503, 504):
                if attempt == 2:
                    raise SourceError(f"HTTP {response.status_code} after bounded retries")
                delay = retry_seconds(
                    response.headers.get("Retry-After", str(2**attempt)), self.clock()
                )
                if delay > 5:
                    raise PauseRun("retry_wait", self.clock() + delay)
                self.sleep(delay)
                continue
            if response.status_code != 200:
                raise SourceError(f"Unexpected source HTTP status {response.status_code}")
            if response.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise SourceError("Expected application/json; response retained for inspection")
            try:
                page = Page.model_validate(json.loads(response.content))
            except ValueError:
                raise SourceError(
                    "Invalid JSON page envelope; response retained for inspection"
                ) from None
            if not page.results and (page.count > 0 or page.next):
                raise SourceError("Empty page contradicts pagination; refusing silent data loss")
            return page
        raise AssertionError("unreachable")
