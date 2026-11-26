#!/usr/bin/env python3
"""One-command release pipeline for VideoManager (called by release.bat).

Steps
-----
1. optional version bump in launcher.py;
2. build the onedir exe with PyInstaller;
3. pack VideoManager-v<ver>-win64-onedir.zip;
4. publish the GitHub release (gh CLI, or API token), or print manual steps;
5. open the release page in the browser.

Examples
--------
    release.bat                     build + publish current version
    release.bat 1.0.1               bump to 1.0.1, build, publish
    release.bat 1.0.1 --push        ... and commit/push the source
    release.bat --zip-only          build + zip, do not touch GitHub
    release.bat --skip-build        repack the existing dist only
    release.bat --fast              incremental PyInstaller (no --clean)
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_release as mr  # noqa: E402

LOG_PATH = ROOT / 'release.log'


class _Tee:
    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, data):
        for stream in self.streams:
            try:
                stream.write(data)
                stream.flush()
            except Exception:
                pass
        return len(data or '')

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


def log(msg=''):
    print(msg, flush=True)


def run_streamed(cmd, cwd=ROOT):
    """Run a command, stream its output to the console and release.log."""
    log('$ ' + ' '.join(str(c) for c in cmd))
    proc = subprocess.Popen(
        [str(c) for c in cmd], cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end='', flush=True)
    return proc.wait()


def set_version(new_version):
    new_version = str(new_version).lstrip('v').strip()
    if not re.fullmatch(r'\d+(\.\d+)*', new_version):
        raise SystemExit(f'invalid version: {new_version!r} (expected e.g. 1.0.1)')
    path = ROOT / 'launcher.py'
    with open(path, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    updated, count = re.subn(r"CURRENT_VERSION\s*=\s*'[^']*'",
                             f"CURRENT_VERSION = '{new_version}'", text, count=1)
    if not count:
        raise SystemExit('CURRENT_VERSION not found in launcher.py')
    if updated != text:
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(updated)
        log(f'[*] CURRENT_VERSION -> {new_version}')
    else:
        log(f'[*] version already {new_version}')
    return new_version


def ensure_gh():
    """Return path to an authenticated gh, or None (then the release is zip-only)."""
    gh = mr.find_gh()
    if not gh:
        log('[!] GitHub CLI (gh) not found.')
        log('    Install: winget install --id GitHub.cli -e')
        log('    Or set GH_TOKEN / .gh_token — make_release supports both.')
        return None

    status = subprocess.run([gh, 'auth', 'status'], capture_output=True, text=True)
    if status.returncode == 0:
        log(f'[*] GitHub CLI ready: {gh}')
        return gh

    log('[!] gh is installed but not authenticated.')
    try:
        answer = input('    Log in to GitHub now? [Y/n] ').strip().lower()
    except EOFError:
        answer = 'n'
    if answer not in ('', 'y', 'yes', 'д', 'да'):
        return None

    log('[*] Starting "gh auth login" — follow the prompts...')
    rc = subprocess.run([gh, 'auth', 'login']).returncode
    if rc != 0:
        log('[!] gh auth login did not complete.')
        return None
    status = subprocess.run([gh, 'auth', 'status'], capture_output=True, text=True)
    return gh if status.returncode == 0 else None


def build_exe(fast=False):
    if shutil.which(sys.executable) is None and not os.path.isfile(sys.executable):
        raise SystemExit('python executable not found')
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm',
           '--distpath', str(ROOT / 'dist'),
           '--workpath', str(ROOT / 'build'),
           str(ROOT / 'VideoManager.spec')]
    if not fast:
        cmd.insert(3, '--clean')

    log('[*] Building onedir (launcher entry point)...')
    started = time.time()
    rc = run_streamed(cmd)
    if rc != 0:
        raise SystemExit(f'PyInstaller failed (exit code {rc})')

    exe = ROOT / 'dist' / 'VideoManager' / 'VideoManager.exe'
    if not exe.is_file():
        raise SystemExit(f'build finished but {exe} is missing')
    log(f'[*] build OK in {time.time() - started:.0f}s: {exe}')


def git_push(tag):
    if not (ROOT / '.git').is_dir():
        log('[!] not a git repository — skipping --push')
        return False
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=str(ROOT),
                            capture_output=True, text=True)
    if not status.stdout.strip():
        log('[*] git: nothing to commit')
        return True
    log('[*] git: committing and pushing source...')
    if subprocess.run(['git', 'add', '-A'], cwd=str(ROOT)).returncode != 0:
        log('[!] git add failed')
        return False
    msg = f'release {tag}'
    if subprocess.run(['git', 'commit', '-m', msg], cwd=str(ROOT)).returncode != 0:
        log('[!] git commit failed')
        return False
    if subprocess.run(['git', 'push'], cwd=str(ROOT)).returncode != 0:
        log('[!] git push failed (credentials?)')
        return False
    log('[*] git: push OK')
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Build and publish a VideoManager release.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument('version', nargs='?', help='new version, e.g. 1.0.1')
    parser.add_argument('--zip-only', action='store_true', help='build + zip, skip GitHub')
    parser.add_argument('--skip-build', action='store_true', help='reuse dist/')
    parser.add_argument('--fast', action='store_true', help='incremental PyInstaller')
    parser.add_argument('--push', action='store_true', help='commit and push the source')
    parser.add_argument('--notes', default='', help='release notes')
    parser.add_argument('--no-open', action='store_true', help='do not open the browser')
    parser.add_argument('--set-version', metavar='VER',
                        help='only bump CURRENT_VERSION in launcher.py and exit')
    args = parser.parse_args()

    if args.set_version:
        set_version(args.set_version)
        return 0

    log_file = open(LOG_PATH, 'a', encoding='utf-8')
    log_file.write(f'\n===== {time.strftime("%Y-%m-%d %H:%M:%S")} =====\n')
    log_file.flush()
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)

    started = time.time()
    log('=' * 60)
    log(' VideoManager release')
    log('=' * 60)

    if args.version:
        set_version(args.version)

    version = mr.read_version()
    user, repo = mr.read_repo()
    tag = f'v{version}'
    log(f'[*] target: {user}/{repo}  {tag}')

    if args.skip_build:
        exe = ROOT / 'dist' / 'VideoManager' / 'VideoManager.exe'
        if not exe.is_file():
            raise SystemExit('--skip-build given but dist/VideoManager/VideoManager.exe is missing')
        log(f'[*] reusing build: {exe}')
    else:
        build_exe(fast=args.fast)

    asset_path, asset_name = mr.build_zip(version)
    checksum_path = mr.build_checksum(asset_path, asset_name)

    published = False
    release_url = ''
    if args.zip_only:
        log('[*] --zip-only: skipping GitHub publish')
    else:
        gh = ensure_gh()
        if gh is None:
            log('[!] publishing skipped - archive is ready for a manual upload')
        else:
            try:
                published = mr.publish(
                    user, repo, tag, asset_path, asset_name,
                    args.notes or f'VideoManager {tag}',
                    extra_assets=[(checksum_path, os.path.basename(checksum_path))])
                if published:
                    release_url = f'https://github.com/{user}/{repo}/releases/tag/{tag}'
            except Exception as exc:
                log(f'[!] publish failed: {exc}')
                published = False

    if args.push and published:
        git_push(tag)

    log('')
    log('-' * 60)
    log(f' version : {version}')
    log(f' archive : {asset_path}  ({os.path.getsize(asset_path) / 1048576:.1f} MB)')
    if published:
        log(f' release : {release_url}')
    elif args.zip_only:
        log(' release : skipped (--zip-only)')
    else:
        log(' release : NOT published (manual upload or token required)')
        log(f' page    : https://github.com/{user}/{repo}/releases/new?tag={tag}')
    log(f' log     : {LOG_PATH}')
    log(f' time    : {time.time() - started:.0f}s')
    log('-' * 60)

    if published and not args.no_open:
        try:
            webbrowser.open(release_url)
        except Exception:
            pass

    log_file.flush()
    return 0 if published or args.zip_only else 3


if __name__ == '__main__':
    sys.exit(main())
