# PROD.Guild

RFC: [RFC.md](RFC.md)

## Стенд

### Фронт

https://hack-lead-03-ru-3-vm1-c0e704.vmaas-spirit.t-education.com/

### Бек

https://hack-lead-03-ru-3-vm1-c0e704.vmaas-spirit.t-education.com/docs

### Coolify (https://paas.hackaton.itqdev.xyz/)


## Кейс - PROD.Guild

## Для продактов

### Проблема

Проектное обучение требует обратной связи, но эксперты не успевают проверять все работы. Участники могли бы помогать друг другу, но в сообществе нет системы подтверждения навыков и доверия к проверяющим

### Целевая аудитория

Школьники и студенты, которые хотят прокачивать свои компетенции и получать обратную связь по своим работам

## Сервисы

Документация по разработке по сервисам:

- [Backend](src/backend/README.md)
- [Frontend](src/frontend/README.md)

## Быстрый старт

```sh
docker compose -f compose.local.yaml up -d --build --wait backend
npm --prefix src/frontend ci && npm --prefix src/frontend start
```

Фронтенд: http://localhost:4200. Swagger API: http://localhost:18080/docs.

## Стек

Фронтенд:

- TypeScript, Angular 20
- RxJS - реактивная работа с данными
- Taiga UI - UI-компоненты
- CodeMirror 6 - редактор кода
- Playwright - E2E-тесты
- ESLint, Stylelint, Prettier - линтеры

Бэкенд:

- Python 3.12+, FastAPI, Uvicorn
- Pydantic 2
- SQLAlchemy 2, Alembic - бд и миграции
- PostgreSQL 18
- Dishka - DI, IoC
- pytest - тесты
- Ruff, mypy, Bandit, codespell - SAST и линтеры
