"""Read-only manifest, source baseline and git apply validation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if result.returncode:
        raise RuntimeError('GIT_CHECK_FAILED:' + args[0])
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--payload', required=True, type=Path)
    args = parser.parse_args()
    repo, payload = args.repo.resolve(), args.payload.resolve()
    manifest = json.loads((payload/'manifest.json').read_text(encoding='utf-8'))
    dirty = run(repo, 'diff', '--name-only') + run(repo, 'diff', '--cached', '--name-only')
    if dirty.strip():
        raise RuntimeError('TRACKED_MODIFICATIONS: do not overwrite the working tree')
    installed = 0
    baseline_conflicts = []
    for item in manifest['files']:
        relative = Path(item['path'])
        target = (payload/'changed-files'/relative).resolve()
        target.relative_to((payload/'changed-files').resolve())
        current = (repo/relative).resolve()
        current.relative_to(repo)
        expected = target.read_bytes()
        if digest(expected) != item['sha256']:
            raise RuntimeError('PAYLOAD_HASH_MISMATCH:' + item['path'])
        if current.is_file() and current.read_bytes().replace(b'\r\n', b'\n') == expected.replace(b'\r\n', b'\n'):
            installed += 1
        if item['new']:
            if current.exists():
                baseline_conflicts.append(item['path'])
        else:
            actual = run(repo, 'show', 'HEAD:' + item['path'])
            if digest(actual) != item['base_git_content_sha256']:
                baseline_conflicts.append(item['path'])
    if installed == len(manifest['files']):
        print(json.dumps({'status':'already_applied','files':installed}))
        return
    if baseline_conflicts:
        raise RuntimeError('BASE_DRIFT:' + ','.join(baseline_conflicts))
    run(repo, 'apply', '--check', str(payload/'stabilization.patch'))
    print(json.dumps({'status':'ready_to_apply','files':len(manifest['files']),
        'repo_head':run(repo, 'rev-parse', 'HEAD').decode().strip(),
        'patch_base':manifest['base_commit'],'mutations':False}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc))
        raise SystemExit(1)
