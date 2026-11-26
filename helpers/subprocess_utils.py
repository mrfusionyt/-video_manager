"""Общие параметры запуска внешних процессов без мигающего терминала.

Приложение собирается с ``console=False``. Если дочерний процесс
(ffmpeg, ffprobe, yt-dlp, tasklist и т.д.) запустить без специальных флагов,
Windows создаёт для него новое консольное окно — оно мелькает при сканировании
библиотеки и генерации превью. Этот помощник добавляет скрытие консоли и
корректную UTF-8 кодировку вывода.
"""
import os
import subprocess


def hidden_process_kwargs(**overrides):
    """Вернуть kwargs для subprocess.run/Popen с невидимой консолью.

    Переданные параметры имеют приоритет, поэтому вызывающий код может
    добавить timeout, cwd, env, stdin/stdout и т.п. При необходимости можно
    передать собственный ``startupinfo`` — например, чтобы свернуть Firefox.
    """
    kwargs = dict(overrides)

    # По умолчанию читаем вывод как UTF-8: на Windows иначе ломаются кириллические пути.
    # text=False означает бинарный вывод — в этом случае кодировку не добавляем.
    if 'encoding' not in kwargs and kwargs.get('text') is not False:
        kwargs['encoding'] = 'utf-8'
        kwargs['errors'] = 'replace'

    if os.name == 'nt':
        flags = int(kwargs.get('creationflags', 0))
        flags |= int(getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
        kwargs['creationflags'] = flags

        if 'startupinfo' not in kwargs:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs['startupinfo'] = startupinfo

    return kwargs
