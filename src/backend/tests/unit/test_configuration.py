from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from src.web_api.configuration import load_configuration


@pytest.fixture(autouse=True)
def crypto_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCESS_TOKEN_CRYPTO_KEY", Fernet.generate_key().decode())


def test_database_url_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[server]\n[database]\nurl = "from-file"\n', encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "from-environment")

    configuration = load_configuration(path)

    assert configuration.database.url.get_value() == "from-environment"
    assert "from-environment" not in repr(configuration)
    assert configuration.server.port == 8080


def test_database_url_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[server]\n[database]\nurl = "from-file"\n', encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert load_configuration(path).database.url.get_value() == "from-file"


def test_gitea_secret_overrides_and_empty_disablement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[server]\n[database]\nurl = "unused"\n', encoding="utf-8")
    monkeypatch.setenv("GITEA_INTERNAL_API_URL", "https://git.example.invalid")
    monkeypatch.setenv("GITEA_API_TOKEN", "private-test-token")
    configuration = load_configuration(path)
    assert configuration.gitea.internal_api_url.get_value() == "https://git.example.invalid"
    assert configuration.gitea.api_token.get_value() == "private-test-token"
    assert "git.example.invalid" not in repr(configuration)
    assert "private-test-token" not in repr(configuration)
    monkeypatch.setenv("GITEA_API_TOKEN", "")
    assert not load_configuration(path).gitea.api_token.get_value()
