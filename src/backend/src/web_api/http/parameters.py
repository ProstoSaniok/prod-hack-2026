from typing import Annotated

from fastapi import Header, Query

from src.application.repository_storage.git import MAX_PATH_BYTES

Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")]


PathQuery = Annotated[str, Query(min_length=1, max_length=MAX_PATH_BYTES)]


VersionQuery = Annotated[int, Query(ge=1)]


LimitQuery = Annotated[int, Query(ge=1, le=100)]


OffsetQuery = Annotated[int, Query(ge=0)]
