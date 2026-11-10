from dataclasses import dataclass, field

from src.application.common.containers import SecretString


@dataclass(frozen=True, slots=True, kw_only=True)
class GiteaConfiguration:
    """An operator-controlled internal origin, not a public or caller-supplied URL.

    Empty defaults allow liveness without Gitea. Operations validate settings and
    fail closed. HTTP is supported for explicitly configured private networks;
    network policy and TLS verification belong to the deployment/client owner.
    """

    internal_api_url: SecretString = field(default_factory=lambda: SecretString(""))
    api_token: SecretString = field(default_factory=lambda: SecretString(""))
    request_timeout_seconds: float = 3.0
    max_response_bytes: int = 65_536
    max_concurrency: int = 4
