# Локальная интеграция с backend

Из корня репозитория: `docker compose -f compose.local.yaml up -d --build backend`.
Из `src/frontend`: `npm ci`, затем `npm start -- --port 4317 --host 127.0.0.1`.
Frontend: http://localhost:4317. Backend: http://localhost:18080/docs.
Angular proxy сохраняет исходные пути; CORS не требуется. Backend target можно
переопределить переменной `PRODGUILD_BACKEND_URL` перед запуском frontend.
Конфигурация `compose.local.yaml` имеет отдельные имена образа, сети и тома БД.

| Экран / действие               | HTTP backend                       | Данные / ошибки                                                    |
| ------------------------------ | ---------------------------------- | ------------------------------------------------------------------ |
| /register                      | POST /auth/sign_up/email           | email, password → opaque bearer; 409 дубликат, 422 валидация       |
| /login                         | POST /auth/sign_in/email           | email, password → opaque bearer; 401 неверные данные               |
| Восстановление сессии, профиль | GET /auth/me                       | id, role; 401 удаляет локальную сессию                             |
| Выход                          | POST /auth/sign_out                | отзыв токена, 204; при сетевой ошибке показывается повтор          |
| Шапка, /profile                | GET /api/v1/me/wallet              | settled, reserved, available, unit; без фиктивного нуля при ошибке |
| /profile                       | GET /api/v1/me/wallet/transactions | limit=20, offset; записи баланса и резерва                         |
| /profile                       | GET /api/v1/competencies           | limit=20, offset; общий каталог, не навыки пользователя            |
| Состояние сервера              | GET /healthcheck                   | liveness                                                           |
| Состояние сервера              | GET /ready                         | DB/Gitea; 503 отображается как состояние зависимости               |
| Ссылка на метрики              | GET /metrics                       | текст Prometheus, локальный диагностический доступ                 |

Сессия хранится в sessionStorage вкладки, не в прежнем localStorage mock-аккаунтов.
Пароль не сохраняется. Авторизованные операции передают Authorization: Bearer.
Имя/email для отображения — метаданные вкладки: текущий `/auth/me` возвращает
только id и role. Профиль и баланс загружаются с сервера.

Кейсы, файлы решений и admin пока демонстрационные: в исходном master для них
не зарегистрированы HTTP-маршруты. Footer и формы явно обозначают это ограничение.
Кнопка демо открывает только локальную демо-сессию; она не выдаёт bearer token.
Gitea в локальном конфиге не настроена, поэтому `/ready` возвращает 503,
при этом авторизация и кошелёк работают. На публичном Caddy диагностические
health/readiness/metrics намеренно закрыты существующей политикой.

Проверка живой связки: `BASE_URL=http://127.0.0.1:4317 BROWSER_CHANNEL=chrome npm test -- api-integration --workers=1`.
Сценарий создаёт отдельный тестовый аккаунт, проверяет реальный бонус +10,
единственную транзакцию после повторного входа, отзыв сессии и сохранность
локального прогресса пользователя. Старые тесты mock-регистрации по логину
описывают прежний контракт и требуют миграции на email/backend fixtures.
