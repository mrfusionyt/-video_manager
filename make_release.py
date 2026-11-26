"""Build a GitHub release (onedir zip + exe) for VideoManager.

The archive contains the PyInstaller onedir layout
(dist/VideoManager/VideoManager.exe + _internal) so the launcher can download,
extract, copy over itself and restart the service.

Usage
-----
    python make_release.py                 # zip + GitHub release
    python make_release.py --no-release    # zip only
    python make_release.py --notes "..."   # custom release notes

How the release is published (in this order):
    1. GitHub CLI (gh) if it is installed;
    2. GitHub REST API with a token from GH_TOKEN / GITHUB_TOKEN
       or from the file .gh_token in the project folder;
    3. otherwise the script prints exact manual instructions.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE, 'dist', 'VideoManager')
API = 'https://api.github.com'

# Folders/files that are useless inside a release archive.
SKIP_DIRS = {'__pycache__', 'SelfTest'}
SKIP_SUFFIXES = ('.pyc', '.pdb', '.pyo', '.chm')
# Runtime databases / caches must never leak from a local build.
SKIP_NAMES = {
    'videos.db', 'videos.db-wal', 'videos.db-shm',
    'hash_cache.db', 'scan_cache.json', 'duplicates_cache.json',
    'duplicates_hash_cache.json', 'probe_cache.json',
    'config.json', 'folder_covers.json',
}


def read_version():
    src = open(os.path.join(BASE, 'launcher.py'), encoding='utf-8').read()
    m = re.search(r"CURRENT_VERSION\s*=\s*'([^']+)'", src)
    if not m:
        raise RuntimeError('CURRENT_VERSION not found in launcher.py')
    return m.group(1)


def read_repo():
    src = open(os.path.join(BASE, 'launcher.py'), encoding='utf-8').read()
    user = re.search(r"GITHUB_USER\s*=\s*'([^']+)'", src)
    repo = re.search(r"GITHUB_REPO\s*=\s*'([^']+)'", src)
    if not user or not repo:
        raise RuntimeError('GITHUB_USER / GITHUB_REPO not found in launcher.py')
    return user.group(1), repo.group(1)


def build_zip(version):
    if not os.path.isdir(DIST_DIR):
        print(f'ERROR: {DIST_DIR} not found.')
        print('Build the exe first:  python -m PyInstaller --noconfirm --clean VideoManager.spec')
        sys.exit(1)
    exe = os.path.join(DIST_DIR, 'VideoManager.exe')
    if not os.path.isfile(exe):
        print(f'ERROR: {exe} not found — the onedir build is incomplete.')
        sys.exit(1)

    asset_name = f'VideoManager-v{version}-win64-onedir.zip'
    asset_path = os.path.join(BASE, asset_name)
    if os.path.exists(asset_path):
        os.remove(asset_path)

    files = []
    for root, dirs, names in os.walk(DIST_DIR):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        for name in sorted(names):
            if name.startswith('.') or name.lower().endswith(SKIP_SUFFIXES):
                continue
            if name.lower() in SKIP_NAMES:
                continue
            full = os.path.join(root, name)
            files.append((full, os.path.relpath(full, DIST_DIR)))

    total_bytes = sum(os.path.getsize(f) for f, _ in files)
    print(f'Packing {len(files)} files ({total_bytes / 1048576:.1f} MB) -> {asset_name}')
    print('Compression level 1 - fast enough for the onedir build.')

    started = time.time()
    written = 0
    with zipfile.ZipFile(asset_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for index, (full, rel) in enumerate(files, 1):
            try:
                z.write(full, rel)
                written += os.path.getsize(full)
            except (PermissionError, FileNotFoundError) as exc:
                print(f'  skipped {rel}: {exc}')
            if index % 250 == 0:
                done = index * 100 / len(files)
                speed = written / 1048576 / max(time.time() - started, 0.001)
                print(f'  {done:5.1f}%  {index}/{len(files)}  {speed:.1f} MB/s')

    size_mb = os.path.getsize(asset_path) / 1048576
    print(f'created: {asset_path}  ({size_mb:.1f} MB, {time.time() - started:.0f}s)')
    return asset_path, asset_name


def build_checksum(asset_path, asset_name):
    """Publish a .sha256 sidecar so the updater can verify the download."""
    digest = hashlib.sha256()
    with open(asset_path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    checksum_path = asset_path + '.sha256'
    with open(checksum_path, 'w', encoding='ascii', newline='\n') as f:
        f.write(f'{digest.hexdigest()}  {asset_name}\n')
    print(f'checksum: {checksum_path} ({digest.hexdigest()})')
    return checksum_path


# ---------------------------------------------------------------- publishing
def get_token():
    for var in ('GH_TOKEN', 'GITHUB_TOKEN'):
        value = (os.environ.get(var) or '').strip()
        if value:
            return value
    token_file = os.path.join(BASE, '.gh_token')
    if os.path.isfile(token_file):
        with open(token_file, encoding='utf-8-sig') as f:
            value = f.read().strip()
            if value:
                return value
    return None


def find_gh():
    """gh.exe from PATH, or from the default winget/MSI locations (PATH may be stale)."""
    exe = shutil.which('gh')
    if exe:
        return exe
    candidates = [
        os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'GitHub CLI', 'gh.exe'),
        os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'), 'GitHub CLI', 'gh.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'GitHubCLI', 'bin', 'gh.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'GitHub CLI', 'gh.exe'),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def release_via_gh(gh_path, tag, assets, notes):
    cmd = [gh_path, 'release', 'create', tag] + [p for p, _ in assets] + [
        '--title', f'VideoManager {tag}', '--notes', notes]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(result.stdout.strip())
        return True
    print('[gh] create failed, trying upload to an existing release...')
    print(result.stderr.strip())
    upload = subprocess.run(
        [gh_path, 'release', 'upload', tag]
        + [p for p, _ in assets] + ['--clobber'],
        capture_output=True, text=True)
    if upload.returncode == 0:
        print(upload.stdout.strip())
        return True
    print('[gh] upload failed:')
    print(upload.stderr.strip())
    return False


def release_via_api(user, repo, tag, assets, notes, token):
    # Imported here so read_version()/build_zip() work before dependencies are installed.
    import requests

    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    base = f'{API}/repos/{user}/{repo}'

    r = requests.get(f'{base}/releases/tags/{tag}', headers=headers, timeout=20)
    if r.status_code == 404:
        payload = {'tag_name': tag, 'name': f'VideoManager {tag}',
                   'body': notes, 'draft': False, 'prerelease': False}
        r = requests.post(f'{base}/releases', headers=headers, json=payload, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f'release create failed: {r.status_code} {r.text[:300]}')
        release = r.json()
        print(f'release created: {release["html_url"]}')
    elif r.status_code == 200:
        release = r.json()
        print(f'release {tag} already exists: {release["html_url"]} — uploading asset')
    else:
        raise RuntimeError(f'cannot read release: {r.status_code} {r.text[:300]}')

    # Replace assets with the same name, if present.
    existing = {a.get('name'): a.get('id') for a in release.get('assets', [])}
    upload_url = release['upload_url'].split('{')[0]
    for path, name in assets:
        if name in existing:
            requests.delete(
                f'{API}/repos/{user}/{repo}/releases/assets/{existing[name]}',
                headers=headers, timeout=20)
        size_mb = os.path.getsize(path) / 1048576
        content_type = 'application/zip' if name.endswith('.zip') else 'text/plain'
        print(f'uploading {name} ({size_mb:.1f} MB) — please wait...')
        with open(path, 'rb') as f:
            up = requests.post(upload_url,
                               headers={**headers, 'Content-Type': content_type},
                               params={'name': name},
                               data=f, timeout=(15, 3600))
        if up.status_code >= 400:
            raise RuntimeError(
                f'asset upload failed ({name}): {up.status_code} {up.text[:300]}')
        print(f'asset uploaded: {up.json().get("browser_download_url")}')
    return release['html_url']


def publish(user, repo, tag, asset_path, asset_name, notes, extra_assets=None):
    """Publish the zip and optional sidecar files (e.g. the .sha256 checksum)."""
    assets = [(asset_path, asset_name)] + list(extra_assets or [])
    gh_path = find_gh()
    if gh_path:
        print(f'publishing with GitHub CLI ({gh_path})...')
        if release_via_gh(gh_path, tag, assets, notes):
            return True
        print('hint: authenticate once with   gh auth login')

    token = get_token()
    if token:
        print('publishing with GitHub API token...')
        url = release_via_api(user, repo, tag, assets, notes, token)
        print(f'release page: {url}')
        return True

    print()
    print('=' * 68)
    print('RELEASE NOT PUBLISHED - no GitHub CLI and no token found.')
    print('=' * 68)
    print('Option A - install GitHub CLI (recommended):')
    print('    winget install --id GitHub.cli -e')
    print('    gh auth login')
    print('    python make_release.py')
    print()
    print('Option B - personal access token (no CLI needed):')
    print('    1. https://github.com/settings/tokens/new  (scope: repo)')
    print('    2. set GH_TOKEN=ghp_xxxxxxxx')
    print('    3. python make_release.py')
    print()
    print('Option C - upload by hand (30 seconds):')
    print(f'    open https://github.com/{user}/{repo}/releases/new?tag={tag}')
    print(f'    attach: {asset_path}')
    print('=' * 68)
    print()
    print('The archive itself is ready, so the release can be finished manually.')
    return False


def main():
    parser = argparse.ArgumentParser(description='Build and publish a VideoManager release.')
    parser.add_argument('--no-release', action='store_true', help='only build the zip')
    parser.add_argument('--notes', default='', help='release notes')
    args = parser.parse_args()

    version = read_version()
    user, repo = read_repo()
    tag = f'v{version}'
    notes = args.notes or f'VideoManager {tag}'
    print(f'VideoManager {tag}  ({user}/{repo})')

    asset_path, asset_name = build_zip(version)
    checksum_path = build_checksum(asset_path, asset_name)
    if args.no_release:
        return

    try:
        published = publish(
            user, repo, tag, asset_path, asset_name, notes,
            extra_assets=[(checksum_path, os.path.basename(checksum_path))])
    except Exception as exc:
        print(f'ERROR while publishing: {exc}')
        print(f'Upload manually: https://github.com/{user}/{repo}/releases/new?tag={tag}')
        sys.exit(2)

    if not published:
        sys.exit(3)


if __name__ == '__main__':
    main()
