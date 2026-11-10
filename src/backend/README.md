# Backend

Python 3.12+, FastAPI, PostgreSQL 18, SQLAlchemy, Alembic, Dishka.

## Быстрый старт

Нужен Docker с Compose v2. Команда из корня проекта:

```sh
docker compose -f compose.local.yaml up -d --build --wait backend
```

Swagger: http://localhost:18080/docs. Миграции применяются при запуске.

## Проверки

Нужны Python 3.12+, `uv` и `just`. Команды из `src/backend`:

```sh
uv sync --locked
just lint    # Ruff, mypy, codespell, Bandit
just format  # форматирование Ruff
just tests   # pytest
```

Для тестов с БД задайте `TEST_DATABASE_URL` с адресом отдельной тестовой PostgreSQL.

## Конфигурация

Локальный Docker-запуск использует `config.local.toml`.
Для работы с репозиториями задайте `GITEA_INTERNAL_API_URL` и `GITEA_API_TOKEN`
в `artifacts/local-gitea.env` от корня проекта и перезапустите backend командой выше.
