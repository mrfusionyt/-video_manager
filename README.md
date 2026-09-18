# Video Manager

Локальное веб-приложение для управления коллекцией видео: сканирование папок, категоризация, скачивание с внешних источников, поиск дубликатов и статистика.

## Возможности

- 📁 Сканирование локальных папок и построение библиотеки
- 🎬 Скачивание видео с YouTube и Instagram
- 🏷️ Категоризация по артистам и тегам
- 🔍 Поиск дубликатов по превью (перцептивный хеш)
- 📊 Статистика по коллекции
- 🌙 Тёмная тема интерфейса
- 💾 Локальная SQLite-база — никаких внешних серверов

## Скриншоты

<!-- Добавь 2–3 скриншота главной страницы, карточки видео и настроек -->
![Главная](docs/screenshot-main.png)
![Карточка видео](docs/screenshot-video.png)

## Стек

- **Backend**: Python 3.10+, Flask 2.3
- **База данных**: SQLite
- **Скачивание**: yt-dlp, instagrapi, Playwright
- **Обработка медиа**: OpenCV, Pillow, imagehash
- **Frontend**: Jinja2, vanilla JS, CSS

## Требования

- Python 3.10 или выше
- FFmpeg и FFprobe в `PATH` (или в папке `_dop/`)
- Windows / Linux / macOS

## Установка

```bash
# 1. Клонировать репозиторий
git clone https://github.com/mrfusionyt/-video_manager.git
cd -video_manager

# 2. Создать виртуальное окружение
python -m venv venv
source venv/bin/activate      # Linux/macOS
venv\Scripts\activate         # Windows

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Установить браузеры Playwright (одноразово)
playwright install chromium
```

### FFmpeg и FFprobe

Приложи бинарники в папку `_dop/`:

```
_dop/
├── ffmpeg.exe
├── ffprobe.exe
└── deno.exe      # опционально, для yt-dlp
```

Либо установи их глобально и добавь в `PATH`.

> ⚠️ Сами бинарники **не хранятся в репозитории** — они слишком большие для GitHub. Скачай их с [ffmpeg.org](https://ffmpeg.org/download.html) и [deno.land](https://deno.land/).

## Запуск

```bash
python app.py
```

Открой в браузере: http://127.0.0.1:5000

## Конфигурация

Основные настройки — в `config.py`:

| Параметр | Описание | По умолчанию |
|---|---|---|
| `THEME` | Тема интерфейса (`dark` / `light`) | `dark` |
| `HOST` | Адрес прослушивания | `127.0.0.1` |
| `PORT` | Порт | `5000` |
| `DUPLICATE_THRESHOLD` | Порог схожести для поиска дублей | `5` |

## Структура проекта

```
video_manager/
├── app.py              # точка входа Flask
├── config.py           # настройки
├── models.py           # модели и работа с БД
├── scanner.py          # сканирование папок
├── artists.py          # логика артистов
├── addon/              # скачивание (youtube, instagram)
├── views/              # маршруты (blueprints)
├── templates/          # Jinja2-шаблоны
├── static/             # CSS, JS, изображения
├── _dop/               # ffmpeg, ffprobe, deno (не в git)
├── requirements.txt
└── README.md
```

## Roadmap

- [ ] Экспорт метаданных в JSON
- [ ] Пакетная обработка видео (нарезка, конвертация)
- [ ] Авторизация для удалённого доступа
- [ ] Docker-образ

## Лицензия

<!-- Укажи лицензию, например MIT -->
MIT

## Автор

**mrfusionyt** — https://github.com/mrfusionyt
