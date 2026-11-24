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
import tkinter as tk
from tkinter import filedialog, messagebox

import requests
from PIL import Image, ImageDraw
import pystray

from app import app as flask_app
from addon.photo_classifier import state as pv_state


# =====================================================================
# UPDATE SETTINGS
# =====================================================================
CURRENT_VERSION = '1.0.0'
GITHUB_USER = 'mrfusionyt'
GITHUB_REPO = '-video_manager'
# =====================================================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

tk_root = None
status_label = None
update_btn = None
pending_update_url = None
ignored_version = None
tray_icon = None
local_url_global = ''
quit_requested = threading.Event()


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
            pystray.MenuItem('PhotoClassifier', open_photo_classifier),
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
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json()
        latest_version = data.get('tag_name', '').lstrip('v')

        if latest_version and latest_version != CURRENT_VERSION and \
                (manual or latest_version != ignored_version):
            download_url = None
            for asset in data.get('assets', []):
                if asset['name'].endswith('.zip'):
                    download_url = asset['browser_download_url']
                    break
            if tk_root and download_url:
                tk_root.after(0, lambda: show_update_indicator(latest_version, download_url))
            return True
        return False
    except Exception as e:
        print(f'[update] check error: {e}')
        return False


def force_check_update():
    def do_check():
        global ignored_version, pending_update_url
        ignored_version = None
        pending_update_url = None

        if status_label:
            tk_root.after(0, lambda: status_label.config(text='Checking...', fg='#ffffff'))

        found = check_for_updates(manual=True)

        if not found and tk_root:
            def show_latest_msg():
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
    while not quit_requested.is_set():
        check_for_updates(manual=False)
        for _ in range(3600):
            if quit_requested.is_set():
                break
            time.sleep(1)


def show_update_indicator(latest_version, download_url):
    global pending_update_url
    pending_update_url = download_url
    if update_btn and not update_btn.winfo_ismapped():
        update_btn.config(text=f'⬇ Update v{latest_version}', bg='#22c55e')
        update_btn.pack(pady=(0, 8), fill=tk.X)
        if status_label:
            status_label.config(text='Update available!', fg='#ff3b5c')


def trigger_update():
    global pending_update_url
    if not pending_update_url:
        return
    if update_btn:
        update_btn.config(state=tk.DISABLED, text='Downloading...', bg='#666666')
    if status_label:
        status_label.config(text='Downloading update...', fg='#ff3b5c')
    threading.Thread(target=download_and_update, args=(pending_update_url,), daemon=True).start()


def download_and_update(download_url):
    if not getattr(sys, 'frozen', False):
        tk_root.after(0, lambda: messagebox.showinfo('Info', 'Auto-update works only in the compiled .exe.', parent=tk_root))
        return
    try:
        exe_path = sys.executable
        exe_dir = os.path.dirname(exe_path)
        exe_name = os.path.basename(exe_path)
        zip_path = os.path.join(os.environ.get('TEMP', '.'), 'vm_update.zip')
        update_dir = os.path.join(os.environ.get('TEMP', '.'), 'vm_update_temp')

        r = requests.get(download_url, stream=True)
        r.raise_for_status()
        with open(zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        if os.path.exists(update_dir):
            shutil.rmtree(update_dir)
        os.makedirs(update_dir)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(update_dir)

        bat_path = os.path.join(os.environ.get('TEMP', '.'), 'vm_update.bat')
        with open(bat_path, 'w') as f:
            f.write(f'''@echo off
timeout /t 3 /nobreak > NUL
taskkill /IM "{exe_name}" /F > NUL 2>&1
robocopy "{update_dir}" "{exe_dir}" /E /IS /IT /NFL /NDL /NJH /NJS /NC /NS /NP > NUL
rmdir /s /q "{update_dir}"
del "{zip_path}"
start "" "{exe_dir}\\{exe_name}"
del "%~f0"
''')
        subprocess.Popen(f'"{bat_path}"', shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        quit_requested.set()
        if tray_icon:
            tray_icon.stop()
    except Exception as e:
        if tk_root:
            tk_root.after(0, lambda: messagebox.showerror('Update Error', f'Failed to update: {e}', parent=tk_root))


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


def main():
    global tk_root, status_label, update_btn, local_url_global, tray_icon

    port = find_free_port(5000, 5100)
    local_url = f'http://127.0.0.1:{port}'
    local_url_global = local_url
    lan_ips = get_local_ips()

    tk_root = tk.Tk()
    tk_root.title('VideoManager')
    
    # Высота 240px, как ты просил
    tk_root.geometry('440x240') 
    tk_root.minsize(400, 200)
    tk_root.configure(bg='#18181b')

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

    update_btn = tk.Button(root_frame, text='', command=trigger_update,
                           bg='#22c55e', fg='#ffffff', activebackground='#16a34a',
                           activeforeground='#ffffff', relief='flat', bd=0,
                           font=('Segoe UI', 9, 'bold'), cursor='hand2',
                           padx=10, pady=4)

    urls_frame = tk.Frame(root_frame, bg='#141417', padx=6, pady=4)
    urls_frame.pack(fill=tk.X, pady=(0, 6))

    make_url_row(urls_frame, 'On this PC:', local_url)

    if lan_ips:
        tk.Label(urls_frame, text='Network:',
                 bg='#141417', fg='#6b7280',
                 font=('Segoe UI', 8), anchor='w').pack(fill=tk.X, pady=(4, 0))
        for ip in lan_ips[:5]:
            make_url_row(urls_frame, '', f'http://{ip}:{port}')

    btn_bar = tk.Frame(root_frame, bg='#18181b')
    btn_bar.pack(fill=tk.X, pady=(6, 0))

    def mk_btn(text, cmd, primary=False):
        bg = '#dc3545' if primary else '#27272a'
        active = '#b02a37' if primary else '#3f3f46'
        b = tk.Button(btn_bar, text=text, command=cmd, bg=bg, fg='#fff',
                      activebackground=active, activeforeground='#fff',
                      relief='flat', bd=0, font=('Segoe UI', 9),
                      cursor='hand2', padx=8, pady=5)
        b.bind('<Enter>', lambda e: b.config(bg=active))
        b.bind('<Leave>', lambda e: b.config(bg=bg))
        return b

    mk_btn('Check Updates', force_check_update).pack(side=tk.LEFT, padx=(0, 5))
    mk_btn('PhotoClassifier', open_photo_classifier).pack(side=tk.LEFT, padx=(0, 5))
    mk_btn('Open Browser', lambda: webbrowser.open(local_url_global), primary=True).pack(side=tk.RIGHT)

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