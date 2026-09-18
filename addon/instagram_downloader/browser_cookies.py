import os
import sys
import time
import shutil
import sqlite3
import tempfile
import subprocess


# ===================================================================
#                     ПОИСК И ЧТЕНИЕ COOKIES
# ===================================================================
def get_instagram_cookies_dict():
    """Возвращает dict {cookie_name: value} для instagram.com из Firefox."""
    profile_path = _find_firefox_profile_with_instagram()
    if not profile_path:
        raise RuntimeError(
            'Не найден профиль Firefox с cookie instagram.com. '
            'Убедитесь, что Firefox установлен и вы залогинены в Instagram в нём.'
        )
    return _read_cookies(profile_path)


def get_instagram_sessionid_from_chrome_safe(start_url=None):
    """Обёртка для совместимости."""
    try:
        cookies = get_instagram_cookies_dict()
        if 'sessionid' not in cookies:
            return None, 'sessionid не найден в Firefox'
        import json
        return json.dumps(cookies), None
    except Exception as e:
        return None, str(e)


# ===================================================================
#                     ЗАКРЫТИЕ FIREFOX
# ===================================================================
def close_firefox():
    """
    Закрывает Firefox.
    Сначала мягко (Firefox сохранит сессию), потом жёстко если не получилось.
    Возвращает True, если Firefox закрыт.
    """
    if sys.platform == 'win32':
        try:
            subprocess.run(
                ['taskkill', '/IM', 'firefox.exe'],
                capture_output=True, text=True, timeout=10
            )
        except Exception as e:
            print(f"[browser_cookies] soft close error: {e}")

        for _ in range(16):
            time.sleep(0.5)
            if not _is_firefox_running():
                print("[browser_cookies] Firefox closed (session saved)")
                return True

        print("[browser_cookies] Firefox didn't close softly, force close")
        try:
            subprocess.run(
                ['taskkill', '/F', '/IM', 'firefox.exe'],
                capture_output=True, text=True, timeout=10
            )
            time.sleep(1)
            return not _is_firefox_running()
        except Exception as e:
            print(f"[browser_cookies] force close error: {e}")
            return False

    try:
        subprocess.run(['pkill', '-TERM', '-f', 'firefox'], timeout=10)
        for _ in range(16):
            time.sleep(0.5)
            if not _is_firefox_running():
                print("[browser_cookies] Firefox closed (session saved)")
                return True
        subprocess.run(['pkill', '-KILL', '-f', 'firefox'], timeout=10)
        return True
    except Exception as e:
        print(f"[browser_cookies] close_firefox error: {e}")
        return False


def _is_firefox_running():
    """Проверяет, запущен ли процесс Firefox."""
    if sys.platform == 'win32':
        try:
            r = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq firefox.exe'],
                capture_output=True, text=True, timeout=5
            )
            return 'firefox.exe' in (r.stdout or '').lower()
        except Exception:
            return False
    else:
        try:
            r = subprocess.run(
                ['pgrep', '-f', 'firefox'],
                capture_output=True, timeout=5
            )
            return r.returncode == 0
        except Exception:
            return False


# ===================================================================
#                     ПЕРЕЗАПУСК FIREFOX (СВЁРНУТЫМ)
# ===================================================================
def reopen_firefox(minimized=True):
    """
    Запускает Firefox свёрнутым, без перехвата фокуса.

    minimized=True (по умолчанию) — окно появится в панели задач свёрнутым,
    но не будет активироваться. Firefox сам восстановит последнюю сессию
    (если в настройках «Открывать последние вкладки и окна»).
    """
    path = _find_firefox_exe()
    if not path:
        print("[browser_cookies] firefox.exe not found — cannot restart")
        return False

    try:
        if sys.platform == 'win32':
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200

            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            if minimized:
                # SW_SHOWMINNOACTIVE = 7 — свёрнуто, без активации (не перехватывает фокус)
                startupinfo.wShowWindow = 7
            else:
                startupinfo.wShowWindow = 1  # SW_SHOWNORMAL

            subprocess.Popen(
                [path],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                startupinfo=startupinfo,
            )
        else:
            subprocess.Popen(
                [path],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        state = 'minimized' if minimized else 'normal'
        print(f"[browser_cookies] Firefox started ({state}): {path}")
        return True
    except Exception as e:
        print(f"[browser_cookies] reopen_firefox error: {e}")
        return False


def _find_firefox_exe():
    """Ищет firefox.exe в стандартных местах и в реестре Windows."""
    if sys.platform != 'win32':
        for candidate in [
            '/usr/bin/firefox',
            '/usr/local/bin/firefox',
            '/Applications/Firefox.app/Contents/MacOS/firefox',
        ]:
            if os.path.isfile(candidate):
                return candidate
        return shutil.which('firefox')

    candidates = [
        os.path.expandvars(r'%ProgramFiles%\Mozilla Firefox\firefox.exe'),
        os.path.expandvars(r'%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\Mozilla Firefox\firefox.exe'),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p

    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (
                r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe',
            ):
                try:
                    key = winreg.OpenKey(hive, sub)
                    path, _ = winreg.QueryValueEx(key, '')
                    winreg.CloseKey(key)
                    if path and os.path.isfile(path):
                        return path
                except Exception:
                    continue
    except Exception:
        pass

    return None


# ===================================================================
#                     ВНУТРЕННИЕ ФУНКЦИИ (sqlite)
# ===================================================================
def _read_cookies(cookies_path):
    tmp = None
    try:
        try:
            return _extract_from_sqlite(cookies_path)
        except sqlite3.OperationalError as e:
            err = str(e).lower()
            if 'locked' in err or 'unable to open' in err:
                tmp = _copy_to_temp(cookies_path)
                return _extract_from_sqlite(tmp)
            raise
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass


def _extract_from_sqlite(cookies_path):
    conn = sqlite3.connect(cookies_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name, value FROM moz_cookies WHERE host LIKE '%instagram.com'"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        raise RuntimeError(
            'В профиле Firefox нет cookies instagram.com. '
            'Залогиньтесь в Instagram в Firefox и попробуйте снова.'
        )

    cookies = {name: value for name, value in rows if name and value}
    if 'sessionid' not in cookies:
        raise RuntimeError(
            'Cookie "sessionid" не найдена в Firefox. '
            'Убедитесь, что вы залогинены в Instagram именно в Firefox.'
        )
    return cookies


def _find_firefox_profile_with_instagram():
    profiles_dir = _get_firefox_profiles_dir()
    if not profiles_dir:
        return None

    try:
        entries = os.listdir(profiles_dir)
    except Exception:
        return None

    for entry in entries:
        profile_path = os.path.join(profiles_dir, entry)
        if not os.path.isdir(profile_path):
            continue
        cookies = os.path.join(profile_path, 'cookies.sqlite')
        if not os.path.isfile(cookies):
            continue
        try:
            if _has_instagram_cookie(cookies):
                return cookies
        except Exception:
            continue
    return None


def _get_firefox_profiles_dir():
    candidates = [
        os.path.expandvars(r'%APPDATA%\Mozilla\Firefox\Profiles'),
        os.path.expanduser('~/.mozilla/firefox'),
        os.path.expanduser('~/Library/Application Support/Firefox/Profiles'),
    ]
    for path in candidates:
        if path and os.path.isdir(path):
            return path
    return None


def _has_instagram_cookie(cookies_path):
    tmp = None
    try:
        tmp = _copy_to_temp(cookies_path)
        conn = sqlite3.connect(tmp)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM moz_cookies WHERE host LIKE '%instagram.com'"
            )
            return cur.fetchone()[0] > 0
        finally:
            conn.close()
    except Exception:
        return False
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass


def _copy_to_temp(src):
    fd, tmp = tempfile.mkstemp(prefix='ff_cookies_', suffix='.sqlite')
    os.close(fd)

    try:
        shutil.copy2(src, tmp)
        return tmp
    except PermissionError:
        pass

    try:
        import win32file  # type: ignore
        handle = win32file.CreateFile(
            src, win32file.GENERIC_READ,
            win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE | win32file.FILE_SHARE_DELETE,
            None, win32file.OPEN_EXISTING, 0, None,
        )
        try:
            with open(tmp, 'wb') as out:
                while True:
                    _, data = win32file.ReadFile(handle, 65536)
                    if not data:
                        break
                    out.write(data)
        finally:
            win32file.CloseHandle(handle)
        return tmp
    except ImportError:
        pass

    try:
        os.remove(tmp)
    except Exception:
        pass
    raise RuntimeError(
        'Не удалось прочитать cookies.sqlite Firefox. Закройте Firefox и попробуйте снова.'
    )