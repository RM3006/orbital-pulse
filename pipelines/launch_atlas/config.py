import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    mode: Literal["local"] = "local"
    base_url: str = "https://ll.thespacedevs.com/2.3.0/"
    data_dir: Path = Path("data")
    user_agent: str = "SpaceLaunchAtlas/0.1 (local research; Romain Menant)"
    max_requests: int = Field(default=2, ge=1, le=8)
    page_size: int = Field(default=5, ge=1, le=100)

    @field_validator("base_url")
    @classmethod
    def production_source(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.netloc != "ll.thespacedevs.com"
            or url.path.rstrip("/") != "/2.3.0"
            or url.query
            or url.fragment
        ):
            raise ValueError("Only the verified HTTPS production LL2 2.3.0 source is enabled")
        return value.rstrip("/") + "/"

    @field_validator("user_agent")
    @classmethod
    def descriptive_agent(cls, value: str) -> str:
        if len(value.strip()) < 12 or "\n" in value or "\r" in value:
            raise ValueError("Use a descriptive, single-line User-Agent")
        return value

    @classmethod
    def from_env(cls, **overrides: object) -> "Settings":
        values: dict[str, object] = {
            "base_url": os.getenv("LL2_BASE_URL", "https://ll.thespacedevs.com/2.3.0/"),
            "data_dir": os.getenv("LOCAL_DATA_DIR", "data"),
            "user_agent": os.getenv(
                "LL2_USER_AGENT", "SpaceLaunchAtlas/0.1 (local research; Romain Menant)"
            ),
            "mode": os.getenv("LAUNCH_ATLAS_MODE", "local"),
        }
        values.update(overrides)
        return cls.model_validate(values)
