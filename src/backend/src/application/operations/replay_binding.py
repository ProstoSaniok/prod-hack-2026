"""Explicit HTTP-boundary presentation; the retained outcome is never rewritten.

This is a pure binding contract, not a route or authorization implementation.
Authorize every first response/replay before calling it. Only supply the outcome
returned for this actor/scope by the caller-owned journal/idempotency service.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from src.application.operations.idempotency import (
    CachedResponse,
    RequestScope,
    ResponseCaptureError,
    ResponseCapturePolicy,
)


class ReplayStatusMode(StrEnum):
    ORIGINAL = "original"
    CREATED_AS_OK = "created_as_ok"


@dataclass(frozen=True, kw_only=True, slots=True)
class ReplayPresentation:
    """Transport status plus the exact immutable retained body/semantic headers."""

    status_code: int
    captured: CachedResponse = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or (
            self.status_code != self.captured.status_code
            and (self.captured.status_code, self.status_code) != (201, 200)
        ):
            raise ResponseCaptureError("Unsupported replay presentation status")


@dataclass(frozen=True, kw_only=True, slots=True)
class ReplayBinding:
    """Bind one exact method/path to one approved capture/presentation policy.

    CREATED_AS_OK is an explicit contract proposal for routes documenting first
    201, replay 200. ORIGINAL is the default. Neither mode alters cached status,
    body, Location or queue-state header. An original 200 or business error keeps
    its status; auth/transient/5xx exclusions still come from capture validation.
    """

    capture_policy: ResponseCapturePolicy
    replay_status: ReplayStatusMode = ReplayStatusMode.ORIGINAL

    def __post_init__(self) -> None:
        if type(self.capture_policy) is not ResponseCapturePolicy or type(self.replay_status) is not ReplayStatusMode:
            raise ResponseCaptureError("Invalid immutable replay binding")
        if self.replay_status == ReplayStatusMode.CREATED_AS_OK and 201 not in self.capture_policy.allowed_statuses:
            raise ResponseCaptureError("Created-as-OK requires an explicitly captured 201 outcome")

    def _validate(self, scope: RequestScope, captured: CachedResponse) -> None:
        policy = self.capture_policy
        if (scope.method, scope.path) != (policy.method, policy.path):
            raise ResponseCaptureError("Replay binding does not match the exact request route")
        policy.validate(captured)

    def first(self, scope: RequestScope, captured: CachedResponse) -> ReplayPresentation:
        self._validate(scope, captured)
        return ReplayPresentation(status_code=captured.status_code, captured=captured)

    def replay(self, scope: RequestScope, captured: CachedResponse) -> ReplayPresentation:
        self._validate(scope, captured)
        status = captured.status_code
        if self.replay_status == ReplayStatusMode.CREATED_AS_OK and status == 201:
            status = 200
        return ReplayPresentation(status_code=status, captured=captured)
