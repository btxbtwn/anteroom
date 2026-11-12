import runpy
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
configure = runpy.run_path(str(ROOT / 'deploy/fly/prepare-staging.py'))['configuration']
production = tomllib.loads((ROOT / 'deploy/fly/fly.toml').read_text())


def test_staging_is_separate_and_can_stop_when_idle():
    value = tomllib.loads(configure('anteroom-test-staging', 'https://staging-example.supabase.co'))
    assert value['app'] != production['app']
    assert value['env']['SUPABASE_URL'] != production['env']['SUPABASE_URL']
    assert value['http_service']['min_machines_running'] == 0
    assert 'secret' not in str(value).lower()


@pytest.mark.parametrize('app,url', [
    (production['app'], 'https://staging-example.supabase.co'),
    ('anteroom-test-staging', production['env']['SUPABASE_URL']),
    ('anteroom-test-staging', production['env']['SUPABASE_URL'].upper().replace('HTTPS:', 'https:')),
    ('anteroom-test-staging', 'http://staging-example.supabase.co'),
    ('anteroom-test-staging', 'https://user:password@staging-example.supabase.co'),
])
def test_staging_refuses_production_or_credentialed_configuration(app, url):
    with pytest.raises(ValueError):
        configure(app, url)


def test_production_serves_sellers_apart_from_draft_and_scheduled_work():
    processes = production['processes']
    # Keep the `app` group name: renaming it would destroy the serving Machine on deploy.
    assert 'ANTEROOM_PROCESS_ROLE=web' in processes['app']
    assert 'ANTEROOM_PROCESS_ROLE=worker' in processes['worker']
    assert production['http_service']['processes'] == ['app']
    assert production['env']['ANTEROOM_BACKGROUND_WORKER'] == '1'
    # Blue-green deploys wait for each new Machine's check, including the worker's.
    checks = [check for check in production['checks'].values() if 'worker' in check['processes']]
    assert checks and checks[0]['path'] == '/api/health'
    sizes = {group: vm for vm in production['vm'] for group in vm['processes']}
    assert set(sizes) == {'app', 'worker'}
    assert sizes['app']['cpu_kind'] == 'performance'
