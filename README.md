# Разбор фотографий

Локальное веб-приложение для проверки фотоархива на домашнем сервере. Оно
находит подозрительные фотографии, но ничего не перемещает без вашего решения.

## Что уже умеет приложение

- индексировать весь архив или выбранную подпапку;
- не анализировать повторно неизменившиеся файлы;
- работать с JPEG, PNG, WebP, HEIC и HEIF;
- пропускать RAW и видео и показывать их количество;
- находить точные копии по содержимому и визуально похожие серии;
- искать размытые, тёмные, однотонные кадры, скриншоты и изображения с текстом;
- уточнять расширенные категории встроенной локальной ONNX-моделью;
- показывать отдельные очереди проверки;
- оставлять, откладывать или массово отправлять фотографии в карантин;
- восстанавливать файлы с защитой от перезаписи;
- окончательно удалять их только после ввода слова `УДАЛИТЬ`;
- сохранять историю действий.

Всё распознавание выполняется внутри контейнера. Интернет для работы не нужен.

## Установка в CasaOS

Перед установкой откройте [docker-compose.yml](docker-compose.yml) и измените:

1. `/srv/storage/02_IMG/Photos` — путь к вашему фотоархиву.
2. `/srv/storage/02_IMG/Photo_Quarantine` — отдельная папка карантина.
3. `PHOTO_REVIEW_PASSWORD` — пароль для входа.
4. `PHOTO_REVIEW_SESSION_SECRET` — длинная случайная строка.

Затем импортируйте Compose-файл в CasaOS. После запуска приложение будет
доступно на порту `8088`.

Папки архива и карантина должны находиться на одном диске, если это возможно:
так перемещение будет быстрым и не потребует временной второй копии файла.

## Важное ограничение

Автоматическая оценка никогда не бывает безошибочной. Сначала запустите анализ
одного небольшого года, проверьте результаты и только после этого переходите к
остальному архиву. Карантин не является резервной копией.

## Локальный запуск для разработки

Требуется Python 3.11 и системный пакет Tesseract с русским и английским языками.

```text
python -m venv .venv
.venv/bin/pip install -e ".[test]"
PHOTO_REVIEW_PHOTOS=/путь/к/фото \
PHOTO_REVIEW_QUARANTINE=/путь/к/карантину \
PHOTO_REVIEW_DATA=/путь/к/данным \
PHOTO_REVIEW_PASSWORD=пароль \
.venv/bin/uvicorn app.main:app --reload
```

## Huey worker

The web process does not start the Huey consumer. Run it separately after the
application environment is configured:

```text
huey_consumer app.infrastructure.background.tasks.huey -k thread -w 1
```

Huey stores technical tasks in `PHOTO_REVIEW_HUEY_DB`, which defaults to
`$PHOTO_REVIEW_DATA/queue/huey.sqlite3`; it remains separate from the main
PhotoHome database. Docker Compose starts the same worker as
`photo-review-worker`.

## Core runtime configuration

Copy `.env.example` to `.env` and replace all host paths and secrets. The
Compose services run separately: `docker compose up photo-review` starts web,
while `docker compose up photo-review-worker` starts only the Huey consumer.
The main state directory, Huey queue directory, and staging directory are
separate mounts. No service removes or migrates library files during startup.

For local development without a `.env`, the application uses the safe
`./.photo-review/` directory rather than a user library. Run web with
`python -m uvicorn app.main:app --reload` and worker with
`python -m app.workers.huey_consumer`. Run tests with
`.venv\\Scripts\\python.exe -m pytest -q` on Windows or `python -m pytest -q`
on Linux/macOS. Test mode (`PHOTO_REVIEW_TEST_MODE=true`) rejects media and
staging roots outside `PHOTO_REVIEW_DATA`.
