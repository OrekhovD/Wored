"""QA over PostgreSQL's published port; never run tests on a production database.

Requires Python 3.11 with the supplied QA dependencies. Does not install software.
Reads .env/.env.postgres locally. Secrets are not printed or written to reports.
"""
import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit

import asyncpg


def read_env(path):
    result = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in '\"\'':
            value = value[1:-1]
        result[key.strip()] = value
    return result


async def prepare(env_root):
    config = read_env(env_root/'.env')
    pg = read_env(env_root/'.env.postgres')
    parsed = urlsplit(config.get('DATABASE_URL', '').replace('postgresql+asyncpg://','postgresql://'))
    username = pg.get('POSTGRES_USER') or unquote(parsed.username or 'postgres')
    password = pg.get('POSTGRES_PASSWORD') or unquote(parsed.password or '')
    database = pg.get('POSTGRES_DB') or parsed.path.lstrip('/') or 'postgres'
    connection = await asyncpg.connect(host='127.0.0.1', port=5432, user=username,
        password=password, database=database, timeout=5, command_timeout=15,
        server_settings={'application_name':'wored_isolated_qa_setup'})
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname='wored_qa'")
        if not exists:
            await connection.execute('CREATE DATABASE "wored_qa"')
        print('Dedicated wored_qa database available; production tables untouched', flush=True)
    finally:
        await connection.close()
    return f"postgresql://{quote(username,safe='')}:{quote(password,safe='')}@127.0.0.1:5432/wored_qa"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--env-root', required=True, type=Path)
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    repo = args.repo.resolve()
    if not (repo/'scripts/check_stabilization.py').is_file():
        raise RuntimeError('Prepared stabilization checkout required')
    dsn = asyncio.run(prepare(args.env_root.resolve()))
    if urlsplit(dsn).path != '/wored_qa':
        raise RuntimeError('QA_DATABASE_GUARD_FAILED')
    env = os.environ.copy()
    # Deliberately do not import production env into the QA process.
    for key in tuple(env):
        if any(part in key for part in ('API_KEY','TOKEN','PASSWORD','DATABASE_URL','REDIS_URL')):
            env.pop(key, None)
    env['WORED_TEST_DATABASE_URL'] = dsn
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONPATH'] = os.pathsep.join(str(repo/p) for p in ('webui','chatbot'))
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH','')
    if args.full:
        command = [sys.executable,'-B','scripts/check_stabilization.py']
    else:
        command = [sys.executable,'-B','-m','pytest','-q','-p','no:cacheprovider',
            'tests/stabilization/test_postgres_queue.py','tests/stabilization/test_postgres_sessions.py']
    return subprocess.run(command, cwd=repo, env=env).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('QA failed:',type(exc).__name__, flush=True)
        raise SystemExit(1)
