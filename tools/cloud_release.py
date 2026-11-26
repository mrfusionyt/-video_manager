#!/usr/bin/env python3
"""Trigger the GitHub Actions build/release pipeline (called by release.bat).

Nothing is built locally. This script:
  1. optionally bumps CURRENT_VERSION in launcher.py;
  2. commits all local changes (the workflow file included);
  3. creates and pushes a v<version> tag;
  4. GitHub Actions builds the onedir exe, packs the zip and publishes
     the release with the built-in GITHUB_TOKEN.

Examples:
    python tools/cloud_release.py               # current version
    python tools/cloud_release.py 1.0.1         # bump, commit, tag, push
    python tools/cloud_release.py --dry-run     # preview, change nothing
    python tools/cloud_release.py --watch      # wait for the Actions run
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import make_release as mr  # noqa: E402
from release_cli import set_version  # noqa: E402

WORKFLOW = 'release.yml'
LOG_PATH = ROOT / 'cloud_release.log'


def log(msg=''):
    line = str(msg)
    print(line, flush=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def git(args, capture=True, check=False, env=None):
    cmd = ['git', '-C', str(ROOT)] + list(args)
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=capture,
                            text=True, encoding='utf-8', errors='replace', env=env)
    if check and result.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} failed: '
                           f'{(result.stderr or result.stdout or "").strip()}')
    return result


def git_token():
    """PAT from env/.gh_token. gh's gho_* token is NOT valid as an HTTP bearer for git."""
    return mr.get_token()


def ensure_auth():
    """Return a PAT for git, or None to use the git credential manager."""
    token = git_token()
    if token:
        return token

    gh = mr.find_gh()
    if gh:
        status = subprocess.run([gh, 'auth', 'status'], capture_output=True,
                                text=True, encoding='utf-8', errors='replace')
        if status.returncode == 0:
            return None  # plain git push uses the credential manager / gh setup-git
        log('[!] GitHub CLI is installed but not logged in.')
        try:
            answer = input('    Log in with gh auth login now? [Y/n] ').strip().lower()
        except EOFError:
            answer = 'n'
        if answer in ('', 'y', 'yes', 'д', 'да'):
            log('[*] starting gh auth login - follow the prompts...')
            subprocess.run([gh, 'auth', 'login'])
            return git_token()
    return None


def push_args(extra, token):
    """Build a complete git command, with an auth header when possible."""
    cmd = ['git', '-C', str(ROOT)]
    if token:
        cmd += ['-c', 'http.extraHeader=AUTHORIZATION: bearer ' + token]
    return cmd + list(extra)


def run_streamed(cmd, env=None):
    log('$ ' + ' '.join(str(c) if 'AUTHORIZATION' not in str(c)
                       else '<auth header>' for c in cmd))
    result = subprocess.run([str(c) for c in cmd], cwd=str(ROOT), env=env)
    return result.returncode


def watch_run(gh):
    if not gh:
        return
    log('[*] waiting for the Actions run...')
    time.sleep(5)
    result = subprocess.run(
        [gh, 'run', 'list', '--workflow', WORKFLOW, '--limit', '1',
         '--json', 'databaseId', '--jq', '.[0].databaseId'],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding='utf-8', errors='replace')
    run_id = (result.stdout or '').strip()
    if not run_id or not run_id.isdigit():
        log('[!] could not find the Actions run; check the Actions tab')
        return
    subprocess.run([gh, 'run', 'watch', run_id], cwd=str(ROOT))
    subprocess.run([gh, 'run', 'view', run_id, '--json',
                    'status,conclusion,url'], cwd=str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', nargs='?', help='new version, e.g. 1.0.1')
    parser.add_argument('--dry-run', action='store_true',
                        help='show what would happen, change nothing')
    parser.add_argument('--force-tag', action='store_true',
                        help='replace an existing tag with the same name')
    parser.add_argument('--watch', action='store_true',
                        help='wait for the GitHub Actions run to finish')
    args = parser.parse_args()

    version = (args.version or '').lstrip('v').strip() or mr.read_version()
    tag = 'v' + version
    user, repo = mr.read_repo()
    actions_url = f'https://github.com/{user}/{repo}/actions/workflows/{WORKFLOW}'
    release_url = f'https://github.com/{user}/{repo}/releases/tag/{tag}'

    log('=' * 64)
    log(' VideoManager - cloud release')
    log('=' * 64)
    log(f'[*] target : {user}/{repo}')
    log(f'[*] version: {version}  (tag {tag})')
    log(f'[*] build  : GitHub Actions (windows-latest), no local build')

    if not (ROOT / '.git').is_dir():
        raise SystemExit('not a git repository')
    remote = git(['remote', 'get-url', 'origin'], check=True).stdout.strip()
    log(f'[*] remote : {remote}')

    if args.dry_run:
        log('')
        log('[dry-run] planned steps:')
        if args.version:
            log(f'  1. set CURRENT_VERSION = {version} in launcher.py')
        log('  2. git add -A && git commit -m "release ' + tag + '"')
        log(f'  3. git tag {tag}' + (' (force)' if args.force_tag else ''))
        log(f'  4. git push -u origin HEAD && git push origin {tag}')
        log(f'  5. Actions builds, packs and publishes: {release_url}')
        log('[dry-run] nothing was changed')
        return 0

    token = ensure_auth()
    log(f'[*] auth   : {"PAT/token" if token else "git credential manager"}')

    if args.version:
        set_version(args.version)

    # Stage the workflow file together with every other local change.
    changes = git(['status', '--porcelain']).stdout.strip()
    if changes:
        log(f'[*] committing {len(changes.splitlines())} changed/untracked entries')
        git(['add', '-A'], check=True)
        commit = git(['commit', '-m', f'release {tag}'])
        if commit.returncode != 0:
            log('[!] git commit failed:')
            log((commit.stderr or commit.stdout or '').strip())
            return 2
    else:
        log('[*] no local changes to commit')

    local_tag = git(['rev-parse', '-q', '--verify', f'refs/tags/{tag}']).returncode == 0
    remote_tag = git(['ls-remote', '--tags', 'origin', f'refs/tags/{tag}']).stdout.strip()
    head = git(['rev-parse', 'HEAD']).stdout.strip()
    tag_commit = ''
    if local_tag:
        tag_commit = git(['rev-parse', f'{tag}^{{commit}}']).stdout.strip()

    # A tag that exists only locally was created by a previous failed run.
    # It is safe to move it to the new HEAD so a plain release.bat just works.
    local_only = bool(local_tag and not remote_tag)
    reuse_tag = False
    if local_only:
        if tag_commit != head:
            log(f'[*] moving unpublished local tag {tag} to HEAD')
            moved = git(['tag', '-f', '-a', tag, '-m', f'VideoManager {tag}'])
            if moved.returncode != 0:
                log('[!] could not move the local tag:')
                log((moved.stderr or moved.stdout or '').strip())
                return 2
        reuse_tag = True

    if reuse_tag:
        log(f'[*] reusing unpublished local tag {tag}')
    elif local_tag or remote_tag:
        if not args.force_tag:
            log(f'[!] tag {tag} already exists (local={local_tag}, remote={bool(remote_tag)})')
            log('    use --force-tag to replace it, or pass a new version')
            return 3
        log(f'[*] removing existing tag {tag}')
        if local_tag:
            git(['tag', '-d', tag])
        if remote_tag:
            rc = run_streamed(push_args(['push', 'origin', f':refs/tags/{tag}'], token))
            if rc != 0:
                log('[!] could not delete the remote tag')
                return 2

    if not reuse_tag:
        tag_result = git(['tag', '-a', tag, '-m', f'VideoManager {tag}'])
        if tag_result.returncode != 0:
            log((tag_result.stderr or '').strip())
            return 2

    env = os.environ.copy()
    if token:
        env['GIT_TERMINAL_PROMPT'] = '0'

    log('[*] pushing source...')
    rc = run_streamed(push_args(['push', '-u', 'origin', 'HEAD'], token), env=env)
    if rc != 0:
        log('[!] push failed. Authenticate with: gh auth login')
        return 2

    log(f'[*] pushing tag {tag} - this starts the build...')
    rc = run_streamed(push_args(['push', 'origin', tag], token), env=env)
    if rc != 0:
        log('[!] tag push failed')
        return 2

    log('')
    log('-' * 64)
    log(f' Actions : {actions_url}')
    log(f' Release : {release_url}')
    log(' The exe is built on GitHub; the zip is attached to the release.')
    log('-' * 64)

    if args.watch:
        watch_run(mr.find_gh())
    return 0


if __name__ == '__main__':
    sys.exit(main())
