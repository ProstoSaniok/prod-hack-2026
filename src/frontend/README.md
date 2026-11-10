# Frontend

Angular 20, TypeScript, Taiga UI, RxJS, CodeMirror 6.

## Быстрый старт

Нужен Node.js 22 с npm. Команды из `src/frontend`:

```sh
npm ci
npm start
```

Открыть http://localhost:4200.

API проксируется на `http://127.0.0.1:18080` — [запуск бэкенда](../backend/README.md#быстрый-старт).
Другой адрес задаётся через `PRODGUILD_BACKEND_URL` перед `npm start`.

## Сборка и проверки

```sh
npm run build     # сборка
npm run lint      # ESLint, Stylelint, Prettier
npm run lint:fix  # автоисправление и форматирование
```

## E2E-тесты

```sh
npx playwright install chromium
npm test
```

Playwright запускает dev-сервер автоматически. Адрес готового стенда задаётся через `BASE_URL`.
