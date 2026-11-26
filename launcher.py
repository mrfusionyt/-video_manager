"""
Десктоп-лаунчер VideoManager.
Запускает Flask в фоновом потоке, открывает окно Tkinter, добавляет
иконку в системный трей и умеет автообновляться с GitHub.
"""
import os
import sys
import socket
import shutil
import threading
import webbrowser
import subprocess
import time
import zipfile
import queue
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox

from launcher_log import LOG_QUEUE, append_log, install_log_redirect

# Install before app / yt-dlp imports so their startup output reaches the log panel.
install_log_redirect()

import requests
from PIL import Image, ImageDraw
import pystray

from app import app as flask_app
from addon.photo_classifier import state as pv_state


# =====================================================================
# UPDATE SETTINGS
# =====================================================================
CURRENT_VERSION = '1.0.3'
GITHUB_USER = 'mrfusionyt'
GITHUB_REPO = '-video_manager'
# =====================================================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ERROR_LOG = os.path.join(tempfile.gettempdir(), 'vm_launcher_error.log')


def _log_exception(exc_type, exc, tb):
    import traceback
    text = ''.join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(ERROR_LOG, 'a', encoding='utf-8') as f:
            f.write(text + '\n')
    except Exception:
        pass
    try:
        append_log('[fatal] ' + text.strip().splitlines()[-1])
    except Exception:
        pass


sys.excepthook = _log_exception
try:
    threading.excepthook = lambda args: _log_exception(
        args.exc_type, args.exc_value, args.exc_traceback)
except Exception:
    pass


tk_root = None
status_label = None
update_btn = None
pending_update_url = None
ignored_version = None
tray_icon = None
local_url_global = ''
quit_requested = threading.Event()

log_text = None


def _version_key(v):
    parts = []
    for chunk in str(v or '').replace('-', '.').split('.'):
        parts.append(int(chunk) if chunk.isdigit() else 0)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts) or (0,)



def resource_path(rel: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(BASE_DIR, rel)


def get_local_ips():
    ips = []
    try:
        _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        for ip in addrs:
            if ip not in ips and not ip.startswith('127.') and ip != '0.0.0.0':
                ips.append(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips and not ip.startswith('127.') and ip != '0.0.0.0':
            ips.append(ip)
    except Exception:
        pass
    return ips


def find_free_port(start=5000, end=5100):
    for port in range(start, end):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('0.0.0.0', port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError('No free port')


def load_app_icon_pil():
    for p in (resource_path('image/video.png'),
              resource_path('image/settings.png'),
              resource_path('image/icon1.png')):
        if os.path.exists(p):
            try:
                return Image.open(p)
            except Exception:
                pass
    img = Image.new('RGB', (64, 64), '#1a1a1a')
    d = ImageDraw.Draw(img)
    d.ellipse([8, 8, 56, 56], fill='#dc3545')
    d.polygon([(24, 20), (24, 44), (46, 32)], fill='white')
    return img


def show_window(icon=None, item=None):
    if tk_root:
        tk_root.after(0, lambda: (tk_root.deiconify(), tk_root.lift(), tk_root.focus_force()))


def hide_window(icon=None, item=None):
    if tk_root:
        tk_root.withdraw()


def tray_quit(icon, item):
    if icon:
        icon.stop()
    quit_requested.set()
    if tk_root:
        tk_root.after(0, tk_root.destroy)


def open_photo_classifier():
    webbrowser.open(local_url_global + '/pv')


def create_tray():
    global tray_icon
    try:
        image = load_app_icon_pil()
        menu = pystray.Menu(
            pystray.MenuItem('Show App', show_window, default=True),
            pystray.MenuItem('Open in Browser', lambda: webbrowser.open(local_url_global)),
            pystray.MenuItem('Restart Server', restart_from_tray),
            pystray.MenuItem('Exit', tray_quit),
        )
        tray_icon = pystray.Icon('VideoManager', image, 'VideoManager', menu)
        threading.Thread(target=tray_icon.run, daemon=True).start()
    except Exception as e:
        print('[tray] failed:', e)


def check_for_updates(manual=False):
    global ignored_version
    if GITHUB_USER == 'YOUR_NICKNAME' or GITHUB_REPO == 'YOUR_REPO':
        return False
    try:
        url = f'https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/releases/latest'
        r = requests.get(url, timeout=(5, 15),
                         headers={'Accept': 'application/vnd.github+json'})
        if r.status_code == 404:
            append_log('[update] no releases found yet')
            return False
        r.raise_for_status()
        data = r.json()
        latest_version = str(data.get('tag_name', '')).lstrip('v')

        if not latest_version:
            return False

        is_newer = _version_key(latest_version) > _version_key(CURRENT_VERSION)
        append_log(f'[update] latest v{latest_version} / current v{CURRENT_VERSION}')

        if is_newer and (manual or latest_version != ignored_version):
            download_url = None
            # Prefer the onedir archive asset (contains the .exe + libs) for auto-update.
            for asset in data.get('assets', []):
                name = (asset.get('name') or '').lower()
                if name.endswith('.zip') and 'onedir' in name:
                    download_url = asset['browser_download_url']
                    break
            if not download_url:
                for asset in data.get('assets', []):
                    if (asset.get('name') or '').lower().endswith('.zip'):
                        download_url = asset['browser_download_url']
                        break
            if not download_url:
                append_log('[update] release has no .zip asset')
                return False
            if tk_root:
                tk_root.after(0, lambda: show_update_indicator(latest_version, download_url))
            return True
        return False
    except Exception as e:
        append_log(f'[update] check error: {e}')
        return False


def force_check_update():
    def do_check():
        global ignored_version, pending_update_url
        ignored_version = None
        pending_update_url = None

        append_log('[update] manual check started')
        if status_label:
            tk_root.after(0, lambda: status_label.config(text='Checking...', fg='#ffffff'))

        found = check_for_updates(manual=True)

        if not found and tk_root:
            def show_latest_msg():
                append_log('[update] you are on the latest version')
                if status_label:
                    status_label.config(text='Latest version installed.', fg='#22c55e')
                def revert_msg():
                    if status_label and status_label.cget('text') == 'Latest version installed.':
                        status_label.config(text='Running.', fg='#e5e7eb')
                tk_root.after(20000, revert_msg)
            tk_root.after(0, show_latest_msg)
        elif not found and status_label:
            tk_root.after(0, lambda: status_label.config(text='Running.', fg='#e5e7eb'))
    threading.Thread(target=do_check, daemon=True).start()


def update_loop():
    # Give the window time to appear before the first network request.
    for _ in range(30):
        if quit_requested.is_set():
            return
        time.sleep(0.1)
    while not quit_requested.is_set():
        check_for_updates(manual=False)
        for _ in range(3600):
            if quit_requested.is_set():
                break
            time.sleep(1)


def show_update_indicator(latest_version, download_url):
    global pending_update_url
    pending_update_url = download_url
    append_log(f'[update] new version v{latest_version} available')
    if update_btn and not update_btn.winfo_ismapped():
        update_btn.config(text=f'Update to v{latest_version}', state=tk.NORMAL)
        update_btn.pack(fill=tk.X, padx=0, pady=(0, 6))
        if status_label:
            status_label.config(text='Update available!', fg='#ff596b')


def trigger_update():
    global pending_update_url
    if not pending_update_url:
        return
    if update_btn:
        update_btn.config(state=tk.DISABLED, text='Downloading update...')
    if status_label:
        status_label.config(text='Downloading update...', fg='#ff596b')
    append_log('[update] download started')
    threading.Thread(target=download_and_update, args=(pending_update_url,), daemon=True).start()


def _find_payload_root(folder):
    """Zip may contain either files at root or a single top-level directory."""
    exe_name = 'VideoManager.exe'
    if os.path.isfile(os.path.join(folder, exe_name)):
        return folder
    subdirs = [d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))]
    for d in subdirs:
        if os.path.isfile(os.path.join(folder, d, exe_name)):
            return os.path.join(folder, d)
    return folder


def download_and_update(download_url):
    if not getattr(sys, 'frozen', False):
        tk_root.after(0, lambda: messagebox.showinfo('Info', 'Auto-update works only in the compiled .exe.', parent=tk_root))
        return
    try:
        exe_path = sys.executable
        exe_dir = os.path.dirname(exe_path)
        exe_name = os.path.basename(exe_path)
        tmp = tempfile.gettempdir()
        zip_path = os.path.join(tmp, 'vm_update.zip')
        update_dir = os.path.join(tmp, 'vm_update_temp')

        r = requests.get(download_url, stream=True, timeout=(10, 120))
        r.raise_for_status()
        total = int(r.headers.get('content-length') or 0)
        done = 0
        next_report = 10
        with open(zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total and (done * 100 // total) >= next_report:
                    append_log(f'[update] downloaded {done * 100 // total}%')
                    next_report += 10
        append_log(f'[update] archive saved ({done // 1048576} MB)')

        if os.path.exists(update_dir):
            shutil.rmtree(update_dir, ignore_errors=True)
        os.makedirs(update_dir)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(update_dir)

        src_root = _find_payload_root(update_dir)
        if not os.path.isfile(os.path.join(src_root, exe_name)):
            raise RuntimeError(f'{exe_name} not found inside the update archive')

        bat_path = os.path.join(tmp, 'vm_update.bat')
        log_path = os.path.join(tmp, 'vm_update.log')
        with open(bat_path, 'w', encoding='utf-8') as f:
            f.write(f'''@echo off
chcp 65001 >nul
set "LOG={log_path}"
echo [%DATE% %TIME%] update started >> "%LOG%"
timeout /t 2 /nobreak >nul
taskkill /IM "{exe_name}" /F >> "%LOG%" 2>&1
timeout /t 1 /nobreak >nul
robocopy "{src_root}" "{exe_dir}" /E /IS /IT /R:3 /W:2 /NFL /NDL /NJH /NJS /NC /NS /NP >> "%LOG%" 2>&1
rmdir /s /q "{update_dir}" >nul 2>&1
del "{zip_path}" >nul 2>&1
start "" "{exe_path}"
del "%~f0"
''')
        append_log('[update] installing, the app will restart')
        subprocess.Popen(['cmd', '/c', bat_path], shell=False,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        quit_requested.set()
        if tray_icon:
            tray_icon.stop()
        if tk_root:
            tk_root.after(400, tk_root.destroy)
    except Exception as e:
        append_log(f'[update] failed: {e}')
        if tk_root:
            tk_root.after(0, lambda: messagebox.showerror('Update Error', f'Failed to update: {e}', parent=tk_root))


def drain_log():
    """Move queued log lines into the Text widget (called from the Tk loop)."""
    global log_text
    if log_text is None or quit_requested.is_set():
        return
    lines = []
    try:
        while len(lines) < 250:
            lines.append(LOG_QUEUE.get_nowait())
    except queue.Empty:
        pass
    if lines:
        at_bottom = log_text.yview()[1] >= 0.995
        log_text.configure(state='normal')
        log_text.insert('end', '\n'.join(lines) + '\n')
        try:
            total = int(log_text.index('end-1c').split('.')[0])
            if total > 700:
                log_text.delete('1.0', f'{total - 700}.0')
        except Exception:
            pass
        log_text.configure(state='disabled')
        if at_bottom:
            log_text.see('end')
    tk_root.after(150, drain_log)


def tk_pump():
    if quit_requested.is_set():
        try:
            tk_root.destroy()
        except Exception:
            pass
        return
    try:
        title = pv_state.dialog_req.get_nowait()
    except Exception:
        tk_root.after(100, tk_pump)
        return
    try:
        path = filedialog.askdirectory(title=title, parent=tk_root) or ''
    except Exception:
        path = ''
    pv_state.dialog_res.put(path)
    tk_root.after(100, tk_pump)


def copy_to_clipboard(text: str, feedback_label: tk.Label):
    try:
        tk_root.clipboard_clear()
        tk_root.clipboard_append(text)
        tk_root.update()
        if feedback_label is not None:
            old = feedback_label.cget('text')
            feedback_label.config(text='✓', fg='#22c55e')
            tk_root.after(1200, lambda: feedback_label.config(text=old, fg='#6b7280'))
    except Exception as e:
        print('[copy] failed:', e)


def make_url_row(parent, label_text: str, url: str):
    row = tk.Frame(parent, bg='#141417')
    row.pack(fill=tk.X, pady=1)

    if label_text:
        lbl = tk.Label(row, text=label_text, bg='#141417', fg='#9ca3af',
                       font=('Segoe UI', 8), width=12, anchor='w')
        lbl.pack(side=tk.LEFT)

    entry = tk.Entry(row, font=('Consolas', 9), bg='#1f1f24',
                     fg='#fca5a5', readonlybackground='#1f1f24',
                     relief='flat', bd=0, highlightthickness=1,
                     highlightbackground='#333', highlightcolor='#dc3545',
                     insertbackground='#fff')
    entry.insert(0, url)
    entry.config(state='readonly')
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4), ipady=2)

    fb = tk.Label(row, text='⎘', bg='#141417', fg='#6b7280',
                  font=('Segoe UI', 12), cursor='hand2', width=3)
    fb.pack(side=tk.LEFT)

    def on_copy(_):
        copy_to_clipboard(url, fb)

    fb.bind('<Button-1>', on_copy)
    entry.bind('<Control-c>', on_copy)


class RoundedButton(tk.Canvas):
    """Canvas button styled like the unified app buttons (tint + red border, stronger red on hover)."""
    def __init__(self,parent,text,command,primary=False,width=130,height=34):
        super().__init__(parent,width=width,height=height,bg=parent.cget('bg'),highlightthickness=0,bd=0,cursor='hand2')
        self.text=text; self.command=command; self.primary=primary; self.w=width; self.h=height
        self.normal='#2e1e22'; self.hover='#4b3248'
        self.fg='#e6e7ea'
        self.bind('<Configure>',lambda e:self.draw(self.normal)); self.bind('<Enter>',lambda e:self.draw(self.hover)); self.bind('<Leave>',lambda e:self.draw(self.normal)); self.bind('<Button-1>',lambda e:self.command())
        self.draw(self.normal)
    def draw(self,color):
        self.delete('all'); r=7; w=self.w; h=self.h
        self.create_rectangle(r,0,w-r,h,fill=color,outline=color); self.create_rectangle(0,r,w,h-r,fill=color,outline=color)
        for x,y in [(r,r),(w-r,r),(r,h-r),(w-r,h-r)]: self.create_oval(x-r,y-r,x+r,y+r,fill=color,outline=color)
        self.create_text(w/2,h/2,text=self.text,fill=self.fg,font=('Segoe UI',9,'bold' if self.primary else 'normal'))

def show_restart_dialog():
    dialog=tk.Toplevel(tk_root); dialog.title('Restart VideoManager'); dialog.configure(bg='#191a1f'); dialog.resizable(False,False); dialog.transient(tk_root); dialog.grab_set()
    dialog.geometry('360x178'); dialog.update_idletasks()
    x=tk_root.winfo_rootx()+(tk_root.winfo_width()-360)//2; y=tk_root.winfo_rooty()+(tk_root.winfo_height()-178)//2; dialog.geometry(f'+{x}+{y}')
    tk.Label(dialog,text='Restart VideoManager?',bg='#191a1f',fg='#f4f4f5',font=('Segoe UI',13,'bold')).pack(anchor='w',padx=20,pady=(18,6))
    tk.Label(dialog,text='The server will briefly go offline and then start again.',bg='#191a1f',fg='#aeb0b8',font=('Segoe UI',9),wraplength=315,justify='left').pack(anchor='w',padx=20)
    bar=tk.Frame(dialog,bg='#191a1f'); bar.pack(fill='x',side='bottom',padx=18,pady=16)
    RoundedButton(bar,'Cancel',dialog.destroy,False,92,34).pack(side='right',padx=(8,0))
    def confirm():
        dialog.destroy()
        if status_label: status_label.config(text='Restarting...',fg='#ff596b')
        def request_restart():
            try: requests.post(local_url_global+'/restart_server',timeout=3)
            except Exception as exc: print('[restart] request failed:',exc)
        threading.Thread(target=request_restart,daemon=True).start()
    RoundedButton(bar,'Restart Server',confirm,True,126,34).pack(side='right')
    dialog.bind('<Escape>',lambda e:dialog.destroy()); dialog.bind('<Return>',lambda e:confirm())

def restart_from_tray(icon=None,item=None):
    if tk_root: tk_root.after(0,show_restart_dialog)


def main():
    global tk_root, status_label, update_btn, local_url_global, tray_icon, log_text

    install_log_redirect()

    port = find_free_port(5000, 5100)
    local_url = f'http://127.0.0.1:{port}'
    local_url_global = local_url
    lan_ips = get_local_ips()
    shown_ips = lan_ips[:5]

    # Window height follows the number of network rows so nothing gets clipped.
    window_height = 336 + 26 * len(shown_ips)
    tk_root = tk.Tk()
    tk_root.title('VideoManager')
    tk_root.geometry(f'470x{window_height}')
    tk_root.minsize(440, 300)
    tk_root.configure(bg='#18181b')
    tk_root.report_callback_exception = lambda exc, val, tb: _log_exception(exc, val, tb)

    try:
        icon_img = Image.open(resource_path('image/video.png'))
        icon_img.thumbnail((64, 64))
        from PIL import ImageTk
        tk_icon = ImageTk.PhotoImage(icon_img)
        tk_root.iconphoto(True, tk_icon)
        tk_root._icon_ref = tk_icon
    except Exception as e:
        print('[launcher] icon failed:', e)

    pv_state.state['tk_root'] = tk_root
    pv_state.load_config()
    pv_state.scan_source()

    # Основной фрейм без expand, чтобы не было пустоты
    root_frame = tk.Frame(tk_root, bg='#18181b')
    root_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

    head = tk.Frame(root_frame, bg='#18181b')
    head.pack(fill=tk.X, pady=(0, 4))

    status_label = tk.Label(head, text='Running.', bg='#18181b',
                            fg='#e5e7eb', font=('Segoe UI', 11, 'bold'),
                            anchor='w')
    status_label.pack(side=tk.LEFT)

    tk.Label(head, text=f'v{CURRENT_VERSION}', bg='#18181b', fg='#6b7280',
             font=('Segoe UI', 8), anchor='e').pack(side=tk.RIGHT)

    update_frame = tk.Frame(root_frame, bg='#18181b')
    update_frame.pack(fill=tk.X)

    update_btn = tk.Button(update_frame, text='', command=trigger_update,
                           bg='#3a1f24', fg='#fca5a5',
                           activebackground='#4b3248', activeforeground='#ffffff',
                           relief='flat', bd=0, highlightthickness=1,
                           highlightbackground='#7a2630',
                           font=('Segoe UI', 9, 'bold'), cursor='hand2',
                           padx=10, pady=5)

    urls_frame = tk.Frame(root_frame, bg='#141417', padx=6, pady=4)
    urls_frame.pack(fill=tk.X, pady=(0, 6))

    make_url_row(urls_frame, 'On this PC:', local_url)

    if shown_ips:
        tk.Label(urls_frame, text='Network:',
                 bg='#141417', fg='#6b7280',
                 font=('Segoe UI', 8), anchor='w').pack(fill=tk.X, pady=(4, 0))
        for ip in shown_ips:
            make_url_row(urls_frame, '', f'http://{ip}:{port}')

    # ---- live log panel -------------------------------------------------
    log_wrap = tk.Frame(root_frame, bg='#141417',
                        highlightthickness=1, highlightbackground='#26262c')
    log_wrap.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

    log_text = tk.Text(log_wrap, height=7, bg='#101013', fg='#9ca3af',
                       insertbackground='#e6e7ea', relief='flat', bd=0,
                       font=('Consolas', 8), wrap='word', padx=6, pady=4,
                       state='disabled')
    log_scroll = tk.Scrollbar(log_wrap, command=log_text.yview, width=10,
                              bg='#1b1b20', troughcolor='#101013', relief='flat', bd=0,
                              activebackground='#2b2b33')
    log_text.configure(yscrollcommand=log_scroll.set)
    log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # One-line info above the buttons
    info_label = tk.Label(root_frame, text=f'VideoManager — running: {local_url}',
                         bg='#18181b', fg='#6b7280', font=('Segoe UI', 8), anchor='w')
    info_label.pack(fill=tk.X, pady=(0, 2))

    btn_bar = tk.Frame(root_frame, bg='#18181b')
    btn_bar.pack(fill=tk.X, pady=(6, 0))

    append_log(f'VideoManager v{CURRENT_VERSION} starting...')
    append_log(f'Local URL: {local_url}')
    for ip in shown_ips:
        append_log(f'Network URL: http://{ip}:{port}')

    def mk_btn(text, cmd, primary=False, width=130):
        return RoundedButton(btn_bar, text, cmd, primary=primary, width=width, height=34)

    mk_btn('Check Updates', force_check_update, width=118).pack(side=tk.LEFT, padx=(0, 7))
    mk_btn('Restart Server', show_restart_dialog, width=122).pack(side=tk.LEFT, padx=(0, 7))
    mk_btn('Open Browser', lambda: webbrowser.open(local_url_global), primary=True, width=118).pack(side=tk.RIGHT)

    tk_root.protocol('WM_DELETE_WINDOW', hide_window)

    def run_flask():
        try:
            flask_app.run(host='0.0.0.0', port=port,
                          debug=False, use_reloader=False, threaded=True)
        except Exception as e:
            print('[flask] error:', e)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()

    create_tray()
    tk_root.after(100, tk_pump)
    tk_root.after(200, drain_log)

    try:
        tk_root.mainloop()
    except KeyboardInterrupt:
        print('\nShutting down...')
        quit_requested.set()
    finally:
        if tray_icon:
            try:
                tray_icon.stop()
            except Exception:
                pass
        try:
            tk_root.destroy()
        except Exception:
            pass


if __name__ == '__main__':
    main()


    