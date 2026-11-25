# VideoManager — сборка и публикация релиза

Самый простой путь: один `release.bat` в терминале VS Code. Он пушит тег, а
**GitHub Actions собирает exe, пакует zip и публикует релиз** — локально не
нужны ни PyInstaller, ни `gh`, ни токен.

---

## 0. Что где лежит

| Файл | Назначение |
|------|-----------|
| `launcher.py` | окно лаунчера, трей, лог-панель, проверка и установка обновлений |
| `app.py` | Flask-приложение (запускается внутри лаунчера) |
| `VideoManager.spec` | конфиг PyInstaller, точка входа — `launcher.py` |
| `.github/workflows/release.yml` | облачная сборка и публикация релиза |
| `release.bat` | **одна команда**: версия → тег → GitHub Actions |
| `tools/cloud_release.py` | логика `release.bat` (коммит, тег, push, ссылки) |
| `tools/fetch_dop.py` | скачивает ffmpeg/ffprobe/deno/yt-dlp/fpcalc на runner |
| `release-local.bat` | запасной вариант: сборка на этом ПК |
| `build.bat` | только локальная сборка onedir |
| `make_release.py` | упаковка zip + публикация (локальный путь) |

Версия хранится в одном месте — `launcher.py`:

```python
CURRENT_VERSION = '1.0.0'
GITHUB_USER = 'mrfusionyt'
GITHUB_REPO = '-video_manager'
```

Перед новым релизом увеличьте `CURRENT_VERSION` (например `1.0.1`), иначе лаунчер
у пользователей не увидит обновление. `release.bat 1.0.1` сделает это сам.

---

## 1. Релиз в одну команду (рекомендуется)

В терминале VS Code (`C:\!video_manager`):

```bat
.\release.bat
```

или сразу с новой версией:

```bat
.\release.bat 1.0.1
```

Что происходит:

1. `CURRENT_VERSION` в `launcher.py` меняется на `1.0.1`;
2. все локальные изменения коммитятся (`release v1.0.1`) — включая workflow;
3. создаётся тег `v1.0.1` и делается `git push` ветки и тега;
4. **GitHub Actions** на `windows-latest`:
   - ставит Python 3.12 и зависимости из `requirements.txt`;
   - скачивает бинарники `_dop` (см. ниже);
   - собирает `onedir` из `VideoManager.spec` (точка входа — `launcher.py`);
   - пакует `VideoManager-v1.0.1-win64-onedir.zip`;
   - создаёт релиз и прикрепляет архив через встроенный `GITHUB_TOKEN`;
5. в конце выводятся ссылки на Actions и на релиз; лог — `cloud_release.log`.

Ничего не собирается на вашем ПК: достаточно Python и работающего `git push`.

Варианты:

```bat
release.bat                   :: текущая версия → облачный релиз
release.bat 1.0.1             :: поднять версию → облачный релиз
release.bat --watch           :: дождаться окончания сборки в Actions
release.bat --dry-run         :: только показать план, ничего не менять
release.bat 1.0.1 --force-tag :: заменить тег v1.0.1, если он уже есть
```

Первый раз проверьте, что git может пушить: `git push -u origin HEAD`.
Если `gh auth login` уже сделан, батник использует его токен автоматически;
иначе сработает Credential Manager Windows.

### Важно про `_dop`

Папка `_dop` (~470 МБ) в git не лежит. На runner её создаёт `tools/fetch_dop.py`:

| Файл | Источник |
|------|----------|
| `ffmpeg.exe`, `ffprobe.exe` | https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip |
| `yt-dlp.exe` | latest release yt-dlp |
| `deno.exe` | latest release denoland/deno (windows x64) |
| `fpcalc.exe` | acoustid/chromaprint v1.6.2 (windows x64) |

Свои URL можно задать переменными `DOP_FFMPEG_URL`, `DOP_FFPROBE_URL`,
`DOP_YTDLP_URL`, `DOP_DENO_URL`, `DOP_FPCALC_URL` (в Settings → Secrets and
variables → Variables репозитория). Существующие файлы не перезаписываются.

### Запуск без батника

Вкладка **Actions** → `Build & Release (onedir)` → **Run workflow** → при желании
укажите версию. Либо просто сделайте `git push origin v1.0.1` — тег сам запустит
сборку.

---

## 2. Локальная сборка (запасной путь)

```bat
.\release-local.bat
.\release-local.bat 1.0.1
.\release-local.bat --zip-only
.\release-local.bat --skip-build
```

Скрипт сам находит `gh.exe` (даже если PATH в терминале устарел), при
необходимости предлагает `gh auth login`, собирает exe, пакует zip, публикует
релиз и пишет лог в `release.log`.

Только сборка без публикации:

```bat
build.bat
python -m PyInstaller --noconfirm --clean VideoManager.spec
python make_release.py --no-release
```

> `pyinstaller` без `python -m` не находится, потому что `Scripts\` не добавлен
> в `PATH`. Всегда используйте `python -m PyInstaller`.

Результат локальной сборки:

```
dist\VideoManager\VideoManager.exe     <- лаунчер
dist\VideoManager\_internal\...        <- библиотеки, шаблоны, _dop (ffmpeg и т.д.)
```

Публикация без батника (если есть `gh` или токен):

```bat
winget install --id GitHub.cli -e
gh auth login
python make_release.py
```

Важно: id пакета — **`GitHub.cli`** (не `GitHub.cli.GitHubCLI`). Если `gh`
установился, но PowerShell его не видит — перезапустите терминал.

Токен вместо CLI: https://github.com/settings/tokens/new (scope `repo`), затем
`set GH_TOKEN=ghp_...` и `python make_release.py`. Либо файл `.gh_token` в корне
проекта (уже в `.gitignore`).

Ручная загрузка: https://github.com/mrfusionyt/-video_manager/releases/new?tag=v1.0.0
→ прикрепить `VideoManager-v1.0.0-win64-onedir.zip` → Publish release. Имя ассета
должно содержать `onedir` и заканчиваться на `.zip` — лаунчер ищет именно такой
файл.

---

## 3. Как работает авто-обновление у пользователя

1. Лаунчер при старте (через ~3 секунды) и далее раз в час запрашивает
   `https://api.github.com/repos/mrfusionyt/-video_manager/releases/latest`.
2. Если `tag_name` новее `CURRENT_VERSION` (сравнение по числам, а не по строке),
   появляется кнопка **Update to vX.Y.Z** и в лог пишется версия.
3. По нажатию:
   - скачивается zip (с прогрессом в лог-панели);
   - распаковывается во временную папку, проверяется наличие `VideoManager.exe`;
   - создаётся `%TEMP%\vm_update.bat`, который: ждёт 2 с → `taskkill` лаунчера →
     `robocopy /E /IS /IT` поверх папки exe → запускает `VideoManager.exe` заново.
4. Логи установки: лог-панель лаунчера + `%TEMP%\vm_update.log`.

Данные (`.db`, кэши, настройки) лежат рядом с exe и **не удаляются** — robocopy
копирует только файлы из архива.

---

## 4. Чек-лист нового релиза

- [ ] `release.bat 1.0.1` — версия поднялась, тег запушен
- [ ] в выводе есть ссылка `Release : https://github.com/.../releases/tag/v1.0.1`
- [ ] во вкладке Actions задача `Build & Release (onedir)` зелёная
- [ ] `cloud_release.log` без ошибок
- [ ] на «чистой» машине скачал zip из релиза, распаковал, запустил `.exe`
- [ ] лаунчер на старой версии увидел `Update to v1.0.1`

---

## 5. Частые ошибки

| Ошибка | Причина / решение |
|--------|-------------------|
| `git push failed` | авторизуйтесь: `gh auth login` или Credential Manager; проверьте `git push -u origin HEAD` |
| `tag v1.0.1 already exists` | `release.bat 1.0.1 --force-tag` или новая версия |
| `Tag version ... does not match CURRENT_VERSION` | батник должен менять версию; при ручном теге обновите `launcher.py` |
| Actions: `ffmpeg.exe not found` | упал `tools/fetch_dop.py`; проверьте лог шага и доступ к URL (или задайте `DOP_*_URL`) |
| `pyinstaller : Имя не распознано` | используйте `python -m PyInstaller ...`, `build.bat` или облачную сборку |
| `No package found matching input criteria` | неверный id: нужно `winget install --id GitHub.cli -e` (только локальный путь) |
| `gh : Имя не распознано` | PATH обновится в новом окне; `release-local.bat` сам находит `C:\Program Files\GitHub CLI\gh.exe` |
| Релиз есть, но лаунчер не видит обновление | тег должен быть `v<версия>` больше `CURRENT_VERSION`, а в релизе — zip-ассет |
| Обновление скачалось, но не установилось | смотрите `%TEMP%\vm_update.log` |
