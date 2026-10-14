# PROD хакатон 2026

[![CI](https://github.com/<owner>/<repository>/actions/workflows/ci.yml/badge.svg)](https://github.com/<owner>/<repository>/actions/workflows/ci.yml)
[![Angular](https://img.shields.io/badge/Angular-20-DD0031?logo=angular&logoColor=white)](https://angular.dev/)
[![Taiga UI](https://img.shields.io/badge/Taiga%20UI-4-7C3AED)](https://taiga-ui.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)

Лендинг и стартовая full-stack инфраструктура для **PROD хакатона 2026**.

## Стек

- **Frontend:** Angular 20, standalone components, Taiga UI.
- **Backend:** FastAPI, Uvicorn, Pydantic.
- **Инфраструктура:** Docker, Docker Compose, Nginx.
- **CI:** GitHub Actions.

## Структура репозитория

```text
.
├── backend/
│   ├── app/
│   │   └── main.py
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── public/
│   │   ├── fonts/
│   │   └── imgs/
│   ├── src/
│   ├── Dockerfile
│   ├── angular.json
│   └── package.json
├── .github/workflows/ci.yml
├── .dockerignore
├── .gitignore
├── docker-compose.yml
└── README.md
```

Сгенерированные зависимости, результаты сборки, кэши, логи и локальные
переменные окружения исключены из Git через [`.gitignore`](./.gitignore).

## Запуск через Docker

Требования: Docker Desktop с поддержкой Compose.

```bash
docker compose up --build
```

После запуска:

- Frontend: http://localhost:4200
- FastAPI Swagger: http://localhost:8000/docs
- Health-check: http://localhost:8000/api/health

Остановка:

```bash
docker compose down
```

## Разработка с hot reload

Для разработки используйте отдельный Compose-файл. Angular и FastAPI будут
запущены в режиме наблюдения за файлами, поэтому изменения в `frontend/src`,
`frontend/public` и `backend/app` применяются без пересоздания контейнеров:

```bash
docker compose -f docker-compose.dev.yml up --build
```

На Windows polling уже включён в Angular dev server, поэтому Docker Desktop
видит изменения в примонтированной папке `frontend` даже если файловые события
хоста не передаются напрямую.

После первого запуска:

- изменяйте Angular-шаблоны и стили — браузер обновится автоматически;
- изменяйте FastAPI-код — Uvicorn перезапустит backend автоматически;
- перезапуск Docker нужен только при изменении зависимостей или Docker-конфигурации.

Остановка dev-окружения:

```bash
docker compose -f docker-compose.dev.yml down
```

## Локальная разработка

### Frontend

```bash
cd frontend
npm ci
npm start
```

### Backend

Windows:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Linux/macOS:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Проверка

```bash
cd frontend
npm run build
```

```bash
cd backend
python -m compileall app
```

GitHub Actions запускает эти проверки автоматически для push в `main`/`master`
и для pull request.

## Публикация на GitHub

```bash
git init
git add .
git commit -m "Initial PROD hackathon 2026 setup"
git branch -M main
git remote add origin https://github.com/<owner>/<repository>.git
git push -u origin main
```

Перед push замените `<owner>/<repository>` в README и в URL CI-бейджа на
адрес созданного GitHub-репозитория.
