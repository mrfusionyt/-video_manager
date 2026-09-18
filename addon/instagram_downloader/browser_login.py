import os
import time

# Директория для сохранения профиля браузера (чтобы логин сохранялся между запусками)
PROFILE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'browser_profile'
)
os.makedirs(PROFILE_DIR, exist_ok=True)

INSTAGRAM_URL = 'https://www.instagram.com/'
MAX_WAIT_SECONDS = 300  # Максимальное время ожидания входа (5 минут)


def open_browser_and_wait_for_login():
    """
    Открывает браузер (Chromium через Playwright), переходит на Instagram,
    ждет, пока пользователь войдет вручную, и возвращает cookies.

    Возвращает:
        dict: Словарь с cookies для instagram.com (sessionid, csrftoken, ...)
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            'Playwright не установлен. Выполните:\n'
            '  pip install playwright\n'
            '  playwright install chromium'
        )

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=False,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-first-run',
                    '--no-default-browser-check',
                ],
                viewport={'width': 1280, 'height': 800},
            )
        except Exception as e:
            raise RuntimeError(f'Не удалось запустить браузер: {e}')

        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(INSTAGRAM_URL, wait_until='domcontentloaded', timeout=30000)
            except Exception:
                pass

            print("[browser_login] Окно браузера открыто. Войдите в Instagram вручную.")
            print("[browser_login] Ожидание входа...")

            session_cookies = _wait_for_login(context, MAX_WAIT_SECONDS)

            if not session_cookies:
                raise RuntimeError(
                    f'Не удалось получить cookies за {MAX_WAIT_SECONDS} секунд. '
                    'Убедитесь, что вы вошли в Instagram в открывшемся окне.'
                )

            print("[browser_login] Вход выполнен! Cookies получены.")
            return session_cookies

        finally:
            try:
                context.close()
            except Exception:
                pass


def _wait_for_login(context, timeout_seconds):
    """Ждет появления cookie sessionid, возвращает нужные cookies для instagram.com."""
    deadline = time.time() + timeout_seconds
    last_log = 0.0

    while time.time() < deadline:
        try:
            cookies = context.cookies(['https://www.instagram.com/'])
            has_sessionid = any(c.get('name') == 'sessionid' for c in cookies)

            if has_sessionid:
                result = {}
                for c in cookies:
                    name = c.get('name')
                    if name in ('sessionid', 'csrftoken', 'ds_user_id',
                                'mid', 'ig_did', 'rur', 'ig_nrcb'):
                        result[name] = c.get('value')
                return result

            now = time.time()
            if now - last_log > 15:
                remaining = int(deadline - now)
                print(f"[browser_login] Ожидание входа... осталось ~{remaining}s")
                last_log = now

            time.sleep(1)

        except Exception as e:
            print(f"[browser_login] Ошибка при чтении cookies: {e}")
            time.sleep(1)

    return None