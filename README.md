# PROD.Guild

PROD.Guild — платформа проектного обучения для школьников и студентов. Участники решают кейсы, получают отзывы по конкретным строкам решения и зарабатывают право проверять работы по мере роста компетенций.

[Открыть стенд](https://hack-lead-03-ru-3-vm1-c0e704.vmaas-spirit.t-education.com/) · [Swagger API стенда](https://hack-lead-03-ru-3-vm1-c0e704.vmaas-spirit.t-education.com/docs) · [Требования и архитектура](RFC.md)

## Как проходит кейс

1. **Организатор** готовит материалы, указывает требуемые навыки и публикует кейс в каталоге.
2. **Участник** открывает проект, редактирует файлы и отправляет зафиксированную версию решения на проверку.
3. **Ревьюер** получает задание по своим компетенциям, изучает файлы и изменения, оставляет построчные комментарии и публикует вердикт.
4. **Эксперт** оценивает качество ревью и готовность проверяющего к следующему уровню. В профиле участника видны отзывы, опыт по навыкам, баланс PRODитов и история операций. PRODиты можно потратить на приоритет в очереди.

## Локальный запуск

Понадобятся Docker с Compose v2.24 или новее и Node.js 22.12 или новее из ветки 22.x с npm. Выполните команды из корня репозитория.

```sh
docker compose -f compose.local.yaml up -d --build --wait backend
npm --prefix src/frontend ci
npm --prefix src/frontend start
```

Откройте [приложение](http://localhost:4200), [Swagger UI](http://localhost:18080/docs) или [проверку готовности API](http://localhost:18080/healthcheck). Angular проксирует запросы к локальному API. Для другого бэкенда задайте `PRODGUILD_BACKEND_URL` перед запуском фронтенда.

Compose поднимает PostgreSQL, применяет миграции Alembic и запускает API с демо-аккаунтами. Для входа организатором используйте `organizer.local@example.com`. Пароль берётся из `DEMO_ORGANIZER_PASSWORD` при первом создании аккаунта. Значение по умолчанию указано в [локальной конфигурации Compose](compose.local.yaml).

Для сценариев с Git-репозиториями дополнительно запустите Gitea, создайте токен для операций API и укажите `GITEA_INTERNAL_API_URL=http://gitea:3000` и `GITEA_API_TOKEN` в локальном файле `artifacts/local-gitea.env`. После сохранения файла пересоздайте контейнер API.

```sh
docker compose -f compose.local.yaml up -d --wait gitea
# После настройки токена в artifacts/local-gitea.env
docker compose -f compose.local.yaml up -d --force-recreate --wait backend
```

Интерфейс локальной Gitea доступен на [порту 13000](http://localhost:13000). Для остановки завершите Angular сочетанием Ctrl+C и выполните `docker compose -f compose.local.yaml down`. Именованные тома сохраняют данные между запусками.

## Компоненты и проверка изменений

- [Бэкенд](src/backend/README.md) — FastAPI на Python, PostgreSQL 18, SQLAlchemy, Alembic и Dishka. Из `src/backend` выполните `uv sync --locked`, затем `just lint` и `just tests`. Для тестов с БД нужен `TEST_DATABASE_URL` отдельной тестовой PostgreSQL.
- [Фронтенд](src/frontend/README.md) — Angular 20, TypeScript, RxJS, Taiga UI и CodeMirror 6. После установки зависимостей выполните `npm --prefix src/frontend run lint` и `npm --prefix src/frontend run build`. Для [браузерных тестов Playwright](src/frontend/tests/README.md) из `src/frontend` выполните `npx playwright install chromium`, затем `npm test`.
- [Развёртывание](RFC.md#деплой) — конфигурации Compose и Coolify находятся в [deploy](deploy), подготовка серверов описана в [руководстве Ansible](infrastructure/iac/ansible/README.md).
- [Наблюдаемость](infrastructure/observability/README.md) — конфигурация метрик и логов. [Дашборды Sage](infrastructure/iac/sage/README.md) управляются из репозитория.
