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

Откройте [приложение](http://localhost:4200) или [Swagger UI](http://localhost:18080/docs). Angular проксирует запросы к локальному API. Для другого бэкенда задайте `PRODGUILD_BACKEND_URL` перед запуском фронтенда.

Compose поднимает PostgreSQL, применяет миграции Alembic и запускает API с демо-аккаунтами. Для входа организатором используйте `organizer.local@example.com`. Пароль берётся из `DEMO_ORGANIZER_PASSWORD` при первом создании аккаунта. Значение по умолчанию указано в [локальной конфигурации Compose](compose.local.yaml).

### Подключить Gitea для работы с файлами

Материалы кейса, эталон и решения участников хранятся в приватных репозиториях. На новой локальной установке запустите Gitea и один раз создайте служебного пользователя через CLI. Регистрация через браузер в локальном Compose выключена. Команды выполняются из корня проекта во втором терминале.

```sh
docker compose -f compose.local.yaml up -d --wait gitea
docker compose -f compose.local.yaml exec --user git gitea gitea --config /data/gitea/conf/app.ini admin user create --username prodguild-storage --email storage@example.test --random-password --must-change-password=false
docker compose -f compose.local.yaml exec --user git gitea gitea --config /data/gitea/conf/app.ini admin user generate-access-token --username prodguild-storage --token-name prodguild-local --scopes read:user,write:repository,write:issue
```

Последняя команда выдаёт токен. Бэкенд использует его для проверки пользователя, записи файлов и публикации комментариев к ревью. Создайте каталог `artifacts`, если его ещё нет, и сохраните две строки в `artifacts/local-gitea.env`, подставив выданный токен. Этот файл исключён из Git. [Справочник Gitea CLI](https://docs.gitea.com/administration/command-line/)

```dotenv
GITEA_INTERNAL_API_URL=http://gitea:3000
GITEA_API_TOKEN=<токен из предыдущей команды>
```

Примените настройки командой `docker compose -f compose.local.yaml up -d --no-deps --force-recreate --wait backend`. Адрес `gitea:3000` доступен внутри Compose, а веб-интерфейс Gitea открывается в браузере на [localhost:13000](http://localhost:13000).

### Проверить запуск

- [GET /healthcheck](http://localhost:18080/healthcheck) возвращает `{"ok":true}`, когда процесс API отвечает.
- [GET /ready](http://localhost:18080/ready) проверяет подключения к PostgreSQL и хранилищу репозиториев. При готовности обоих возвращается HTTP 200 и `{"ok":true,"checks":{"database":true,"repository_storage":true}}`. При недоступной зависимости возвращается HTTP 503. Если `repository_storage` равен `false`, проверьте состояние Gitea, адрес и токен в env-файле, затем пересоздайте контейнер бэкенда.

Для остановки завершите Angular сочетанием Ctrl+C и выполните `docker compose -f compose.local.yaml down`. Именованные тома сохраняют данные между запусками.

## Первый кейс

На чистой базе локальный запуск создаёт демо-аккаунты и компетенции. Первый кейс публикует организатор. Перед началом убедитесь, что `/ready` возвращает HTTP 200. Для перечисленных аккаунтов действует пароль, заданный при первом запуске через `DEMO_ORGANIZER_PASSWORD`.

1. Войдите как `organizer.local@example.com` и откройте [раздел организатора](http://localhost:4200/admin). Создайте кейс, например «Первый SQL-запрос». Заполните название, описание и ожидаемый результат. Добавьте компетенцию SQL с требуемым уровнем 20 и весом 100 %.
2. Добавьте текстовый файл с заданием в материалы и пример решения в приватный эталон. Сохраните файлы и подтвердите готовность эталона. Нажмите «Опубликовать кейс», затем «Подтвердить публикацию».
3. Войдите как `performer.local@example.com`. Откройте кейс в [каталоге](http://localhost:4200/catalog), начните решение, добавьте свой ответ в файл проекта и отправьте решение на проверку.
4. Войдите как `review-plus.local@example.com`, откройте [раздел ревью](http://localhost:4200/reviews) и получите задание из очереди. Проверьте отправленную версию и опубликуйте отзыв. Вернитесь под аккаунтом участника, чтобы увидеть результат. Для сценария экспертной проверки предусмотрен `mentor.local@example.com`.

## Компоненты и проверка изменений

- [Бэкенд](src/backend/README.md) — FastAPI на Python, PostgreSQL 18, SQLAlchemy, Alembic и Dishka. Из `src/backend` выполните `uv sync --locked`, затем `just lint` и `just tests`. Для тестов с БД нужен `TEST_DATABASE_URL` отдельной тестовой PostgreSQL.
- [Фронтенд](src/frontend/README.md) — Angular 20, TypeScript, RxJS, Taiga UI и CodeMirror 6. После установки зависимостей выполните `npm --prefix src/frontend run lint` и `npm --prefix src/frontend run build`.
- [Развёртывание](RFC.md#деплой) — конфигурации Compose и Coolify находятся в [deploy](deploy), подготовка серверов описана в [руководстве Ansible](infrastructure/iac/ansible/README.md).
- [Наблюдаемость](infrastructure/observability/README.md) — конфигурация метрик и логов. [Дашборды Sage](infrastructure/iac/sage/README.md) управляются из репозитория.

### Браузерные тесты Playwright

Выполняйте команды из `src/frontend`. В новом терминале установите Chromium командой `npx playwright install chromium`.

- **Интерфейс на тестовых данных.** По умолчанию `npm test` запускает набор `fixtures`. Playwright сам поднимает Angular на порту 4200. Сначала остановите запущенный вручную `npm start`, чтобы освободить порт.
- **Интеграция с API.** Набор `live` работает с уже запущенными фронтендом и бэкендом и меняет данные. Выделите отдельную тестовую базу и хранилище. В `BASE_URL` укажите адрес фронтенда этого стенда. Некоторые сценарии требуют дополнительных переменных для входа под нужной ролью.

Пример для PowerShell в отдельном терминале, если изолированный стенд доступен на порту 4200:

```powershell
$env:BASE_URL = 'http://127.0.0.1:4200'
$env:PRODGUILD_E2E_SUITE = 'live'
$env:PRODGUILD_E2E_ISOLATED_BACKEND = '1'
npm test
```

Состав наборов и условия запуска перечислены в [конфигурации Playwright](src/frontend/playwright.config.ts). Сценарии и размеры экранов описаны в [руководстве по тестам](src/frontend/tests/README.md).
