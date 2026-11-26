# VideoManager

Локальное приложение для управления коллекцией видео и фотографий: сканирование папок, каталог в SQLite, категории, артисты, плейлисты, поиск дубликатов, скачивание с YouTube/Instagram и автообновление с GitHub.

Готовая сборка — **Windows x64, onedir**: `VideoManager.exe` и папка `_internal` рядом с ним. Внешний сервер и аккаунт не нужны.

> **Точка входа готового приложения — `launcher.py`.**<br>
> `app.py` — только Flask-приложение без окна, трея и проверки обновлений.

## Возможности

- Окно-лаунчер, иконка в системном трее и веб-интерфейс в браузере; доступ по `127.0.0.1` и по локальной сети.
- Сканирование библиотек, карточки видео и фото, превью, поиск, сортировка и пагинация.
- Категории разделены на `video` и `image`; артисты, плейлисты, рейтинг и теги.
- Скачивание с YouTube и Instagram с прогрессом, историей задач и логами.
- Поиск дубликатов: быстрый по видео и глубокий с анализом аудио (`fpcalc`) + персистентный кэш хешей.
- Страница Tools: диагностика «битых» файлов, восстановление, повторное сканирование.
- Единый стиль интерфейса: шапка, сайдбар, кнопки и категории фото оформлены одинаково.
- **Живой лог в лаунчере**: запуск сервера, адреса, скачивания и обновления видны без консоли.
- **Автообновление** из GitHub Releases: проверка раз в час, новая версия скачивается и устанавливается сама.

## Быстрый старт

### Готовый релиз (рекомендуется)

1. На странице [Releases](https://github.com/mrfusionyt/-video_manager/releases) скачай `VideoManager-vX.Y.Z-win64-onedir.zip`.
2. Распакуй архив в отдельную папку, например `C:\VideoManager`.
3. Запусти `VideoManager.exe`.
4. В окне лаунчера нажми **Open Browser** или открой показанный адрес вида `http://127.0.0.1:5000`.
5. Закрытие окна сворачивает приложение в трей; выход — через иконку в трее.

> Не запускай `.exe` прямо из архива: рядом должны остаться `VideoManager.exe` и `_internal\`, включая `_internal\_dop\`.

### Телефон на Android

APK `VideoManager-Phone-vX.Y.Z.apk` — это WebView-клиент: он открывает веб-интерфейс VideoManager, запущенный на ПК в той же Wi‑Fi-сети. База и видео остаются на компьютере; на телефоне ничего не сканируется и не конвертируется.

1. Запусти VideoManager на ПК.
2. В окне лаунчера или на странице Status скопируй сетевой адрес вида `http://192.168.1.10:5000/`.
3. Установи APK на телефон (sideload, «Установить из неизвестных источников»).
4. При первом запуске введи этот адрес в диалоге **Connect to VideoManager**. Он сохранится; изменить его можно через меню ⋮ → **Server address**.

Сборка APK: GitHub Actions → **Build Android phone client APK** → `Run workflow`. APK и `.sha256` будут в артефактах job’а и, если указать `release_tag`, прикрепятся к соответствующему релизу.

### Запуск из исходников

Проект ориентирован на Windows: в зависимостях есть `pywin32`, а вспомогательные бинарники в `_dop\` — `.exe`.

The easiest way is `run.bat`: it creates `venv\`, installs dependencies, and starts the server at `http://127.0.0.1:5000`.

Если нужен полноценный лаунчер (окно, трей, логи, обновления):

```bat
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
python launcher.py
```

Для работы скачивания и дубликатов положи в `_dop\` бинарники `ffmpeg.exe`, `ffprobe.exe`, `yt-dlp.exe`, `deno.exe` и `fpcalc.exe`. Папка не хранится в git; на чистой машине её можно создать командой:

```bat
python tools\fetch_dop.py
```

Скрипт пропускает файлы, которые уже есть. Первый запуск приложения: добавь папку с видео в **Settings → Folders** и запусти сканирование.

## Где лежат данные

| Что | Где |
|---|---|
| База `videos.db`, `config.json`, `folder_covers.json` | `%APPDATA%\VideoManager\` |
| Кэши сканирования и дубликатов (`scan_cache.json`, `probe_cache.json`, `duplicates_cache.json`, `hash_cache.db`) | рядом с исходниками или рядом с `VideoManager.exe` |
| Загрузки, cookies и профили Instagram | `addon\instagram_downloader\downloads\`, `cookies\`, `sessions\`, `browser_profile\` |
| Загрузки YouTube | `addon\youtube_downloader\downloads\` |
| Логи лаунчера и обновления | окно лаунчера, `%TEMP%\vm_launcher_error.log`, `%TEMP%\vm_update.log` |
| Логи релиза | `cloud_release.log`, `release.log` в корне проекта |

Эти файлы — рабочие данные пользователя. Они не должны попадать в репозиторий или в архив обновления.

## Настройки

Основные пользовательские настройки хранятся в `%APPDATA%\VideoManager\config.json` и редактируются на странице **Settings**:

| Ключ | Назначение | По умолчанию |
|---|---|---|
| `last_folder` | последняя открытая папка | `''` |
| `dark_mode` | тёмная тема | `true` |
| `threshold` | порог фильтрации/совпадения | `0` |
| `static_ip` | фиксированный IP для показа сетевой ссылки | `''` |

Константы в `config.py`: `ITEMS_PER_PAGE = 15`, режимы `MODE_LABELS`, пороги качества `QUALITY_LOW_HEIGHT = 720` и `QUALITY_4K_HEIGHT = 2160`.

Порт выбирается автоматически из диапазона `5000–5100` при запуске через `launcher.py`. При прямом `python app.py` используется `0.0.0.0:5000`.

## Структура проекта

```text
video_manager/
├─ .github/workflows/release.yml   # облачная сборка и публикация релиза
├─ .github/workflows/apk.yml       # сборка подписанного APK для телефона
├─ android/                        # Android WebView-клиент (Gradle, Java)
├─ addon/
│  ├─ instagram_downloader/        # Flask blueprint, логин, cookies, загрузки
│  ├─ photo_classifier/            # страница категорий фото (/pv)
│  └─ youtube_downloader/          # Flask blueprint, загрузки и задачи
├─ db/                             # SQLite: connection, migrations, videos,
│                                  # categories, libraries, playlists, stats, thumbnails
├─ helpers/                        # pagination, profiles, video_filter
├─ image/                          # иконки интерфейса и video.ico
├─ static/css/ , static/js/        # оформление и клиентская логика
├─ templates/                      # Jinja2-шаблоны страниц
├─ tools/
│  ├─ cloud_release.py             # логика release.bat
│  ├─ fetch_dop.py                 # загрузка _dop на чистой машине/CI
│  └─ release_cli.py               # локальный pipeline и --set-version
├─ views/                          # маршруты Flask (main, settings, tools, …)
├─ app.py                          # Flask app factory
├─ launcher.py                     # десктоп-точка входа: окно, трей, логи, обновления
├─ launcher_log.py                 # перехват stdout/stderr/logging в лог-панель
├─ models.py                       # совместимый фасад над пакетом db/
├─ scanner.py                      # сканирование библиотек и кэш
├─ duplicate_finder.py             # поиск дубликатов и кэш хешей
├─ converter.py, artists.py, tools_service.py
├─ config.py                       # конфиг и пороги качества
├─ make_release.py                 # упаковка onedir zip и публикация
├─ VideoManager.spec               # конфиг PyInstaller, entry point — launcher.py
├─ run.bat                         # запуск из исходников
├─ build.bat                       # локальная сборка onedir
├─ release.bat                     # один курс: тег → GitHub Actions
├─ release-local.bat               # запасной локальный путь публикации
├─ requirements.txt
├─ RELEASE.md                      # подробная инструкция по релизу
└─ _dop/                           # бинарники ffmpeg/ffprobe/... (не в git)
```

Создаётся во время работы или сборки и не является исходниками: `venv/`, `__pycache__/`, `build/`, `dist/`, `*.zip`, `*.log`, `.gh_token`, кэши `*.json` / `hash_cache.db`, профили и загрузки внутри `addon/`.

## Сборка и релиз

### Облачно (рекомендуется)

В терминале VS Code из папки проекта:

```bat
.\release.bat
.\release.bat 1.0.2
.\release.bat --watch
.\release.bat --dry-run
.\release.bat 1.0.2 --force-tag
```

Скрипт меняет `CURRENT_VERSION` в `launcher.py`, коммитит изменения, создаёт и пушит тег `v<версия>`. Далее [GitHub Actions](https://github.com/mrfusionyt/-video_manager/actions/workflows/release.yml) на `windows-latest`:

1. ставит Python 3.12 и зависимости;
2. запускает `tools\fetch_dop.py`;
3. собирает onedir из `VideoManager.spec` (точка входа — `launcher.py`, `console=False`, иконка `image\video.ico`);
4. пакует `VideoManager-v<версия>-win64-onedir.zip`;
5. публикует релиз через встроенный `GITHUB_TOKEN`.

Локально не нужны ни PyInstaller, ни `gh`, ни токен — нужен только работающий `git push`. Релиз можно запустить и вручную: **Actions → Build & Release (onedir) → Run workflow** или просто запушить тег `v*`.

### Локально (запасной путь)

```bat
.\build.bat
python make_release.py --no-release
.\release-local.bat
.\release-local.bat --zip-only
```

`pyinstaller` не добавляется в `PATH`, поэтому используй `python -m PyInstaller` или `build.bat`. Для локальной публикации нужен [GitHub CLI](https://cli.github.com/) (`winget install --id GitHub.cli -e`, затем `gh auth login`) либо `GH_TOKEN` / файл `.gh_token`.

Подробности, чек-лист и разбор ошибок — в [RELEASE.md](RELEASE.md).

## Как работает автообновление

1. Лаунчер через ~3 секунды после старта и далее **раз в час** спрашивает `releases/latest` репозитория `mrfusionyt/-video_manager`. Кнопка `Check Updates` запускает проверку немедленно.
2. Версии сравниваются по числам: `1.0.10 > 1.0.9`. При наличии новой версии это сразу видно в лаунчере: статус `Update vX.Y.Z available!` → `Downloading vX.Y.Z: N%` → `Installing vX.Y.Z...`, всё дублируется в живой лог.
3. Нажимать кнопку **не нужно**: zip скачивается автоматически, распаковывается во временную папку, проверяется наличие `VideoManager.exe`. Кнопка остаётся только как `Retry update` на случай ошибки.
4. Отдельный bat-скрипт дожидается выхода лаунчера, копирует файлы через `robocopy` в `C:\!video_manager` и перезапускает `VideoManager.exe`. Так сервер и окно поднимаются уже на новой версии.
5. Папку установки можно переопределить переменной `VIDEOMANAGER_INSTALL_DIR`; если `C:\!video_manager` недоступна для записи, используется папка запущенного `.exe`.
6. База, конфиг и кэши не удаляются — обновляются только файлы из архива.

Чтобы обновление работало, у релиза должен быть тег `v<версия>` больше `CURRENT_VERSION` и zip-ассет, в имени которого есть `onedir`.

## Диагностика

| Симптом | Что сделать |
|---|---|
| Окно не появилось | проверь иконку в трее и `%TEMP%\vm_launcher_error.log` |
| Нет ffmpeg / yt-dlp / fpcalc | запусти `python tools\fetch_dop.py` или положи бинарники в `_dop\` |
| Actions: `Invalid URL ''` в `fetch_dop` | пустая переменная `DOP_*_URL` больше не перебивает адрес по умолчанию |
| `pyinstaller : Имя не распознано` | используй `python -m PyInstaller ...` или `build.bat` |
| `gh : Имя не распознано` | перезапусти терминал; для облачного пути `gh` не нужен вообще |
| Actions падает на `Resolve version` | зависимости теперь ставятся до проверки версии; повторите запуск workflow |
| `remote: invalid credentials` при push | OAuth `gho_*` не годится как bearer; используется Credential Manager, при необходимости `gh auth setup-git` |
| `No package found matching input criteria` | id пакета — `GitHub.cli`, не `GitHub.cli.GitHubCLI` |
| Релиз есть, но обновление не видно | проверь тег, `CURRENT_VERSION` и имя zip-ассета (`onedir`) |
| Обновление не установилось | смотри `%TEMP%\vm_update.log` и `%TEMP%\vm_update.zip` |
| Статус `folder ... is not writable` | проверь права на `C:\!video_manager` или задай `VIDEOMANAGER_INSTALL_DIR` |

## Автор

[mrfusionyt](https://github.com/mrfusionyt)
