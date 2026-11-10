import os
from dataclasses import dataclass, field
from pathlib import Path
from tomllib import loads

from adaptix import Retort, loader

from src.adapters.access_token.config import AccessTokenConfiguration
from src.adapters.gitea.config import GiteaConfiguration
from src.application.common.containers import SecretString

__all__ = [
    "AccessTokenConfiguration",
    "Configuration",
    "DatabaseConfiguration",
    "ServerConfiguration",
    "load_configuration",
]


@dataclass(frozen=True, slots=True)
class DatabaseConfiguration:
    url: SecretString


@dataclass(frozen=True, slots=True)
class ServerConfiguration:
    host: str = "127.0.0.1"
    port: int = 8080
    access_log: bool = True


@dataclass(frozen=True, slots=True)
class Configuration:
    database: DatabaseConfiguration
    server: ServerConfiguration
    access_token: AccessTokenConfiguration
    gitea: GiteaConfiguration = field(default_factory=GiteaConfiguration)


retort = Retort(recipe=[loader(SecretString, SecretString)])


def load_configuration(path: Path) -> Configuration:
    data = loads(path.read_text(encoding="utf-8"))
    if database_url := os.getenv("DATABASE_URL"):
        data.setdefault("database", {})["url"] = database_url
    if crypto_key := os.getenv("ACCESS_TOKEN_CRYPTO_KEY"):
        data.setdefault("access_token", {})["crypto_key"] = crypto_key
    for variable, key in (("GITEA_INTERNAL_API_URL", "internal_api_url"), ("GITEA_API_TOKEN", "api_token")):
        if (value := os.getenv(variable)) is not None:
            data.setdefault("gitea", {})[key] = value
    return retort.load(data, Configuration)
