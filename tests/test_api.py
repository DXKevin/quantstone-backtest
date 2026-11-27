import time

import pytest
from fastapi.testclient import TestClient

from quantstone_backtest.api import create_app, local_allowed_hosts
from quantstone_backtest.example import EXAMPLE
from quantstone_backtest.jobs import JobManager
from quantstone_backtest.config import RunConfig

HEADERS = {'X-Quantstone-Client': 'workbench'}


def wait_job(client, identifier):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f'/api/runs/{identifier}').json()
        if job['status'] != 'running':
            return job
        time.sleep(.05)
    pytest.fail('worker timed out')


def test_api_runs_real_worker_and_persists_snapshot(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        assert c.get('/api/health').json()['status'] == 'ok'
        assert '策略编辑器' in c.get('/').text
        response = c.post('/api/runs', json={'strategy': EXAMPLE}, headers=HEADERS)
        assert response.status_code == 201, response.text
        identifier = response.json()['id']
        result = wait_job(c, identifier)
        assert result['status'] == 'completed', result
        assert result['result']['metrics']['trade_count'] == 1
        assert (tmp_path / identifier / 'strategy.py').read_text() == EXAMPLE
        assert c.get('/api/runs').json()[0]['id'] == identifier
    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f'/api/runs/{identifier}').json()['status'] == 'completed'


def test_cancel_infinite_strategy(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        strategy = 'def handle_bar(c, bars):\n    while True: pass\n'
        identifier = c.post('/api/runs', json={'strategy': strategy}, headers=HEADERS).json()['id']
        response = c.post(f'/api/runs/{identifier}/cancel', headers=HEADERS)
        assert response.json()['status'] == 'cancelled'
        assert c.get(f'/api/runs/{identifier}').json()['status'] == 'cancelled'


def test_strategy_exception_is_reported(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        strategy = 'def handle_bar(c, bars):\n    raise ValueError("strategy exploded")\n'
        identifier = c.post('/api/runs', json={'strategy': strategy}, headers=HEADERS).json()['id']
        job = wait_job(c, identifier)
        assert job['status'] == 'failed'
        assert 'strategy exploded' in job['error']


def test_validation_and_cross_site_requests(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        assert c.post('/api/runs', json={'strategy': EXAMPLE}).status_code == 403
        assert c.post('/api/runs', json={'strategy': EXAMPLE}, headers={**HEADERS,'Origin':'https://example.com'}).status_code == 403
        assert c.post('/api/runs', json={'strategy': 'def broken('}, headers=HEADERS).status_code == 422
        assert c.post('/api/runs', json={'strategy': EXAMPLE,'start':'2026-11-05','end':'2026-11-01'}, headers=HEADERS).status_code == 422
        assert c.post('/api/runs', json={'strategy': EXAMPLE,'symbols':['688001.SH']}, headers=HEADERS).status_code == 422
        assert c.get('/api/runs/invalid').status_code == 404
        assert c.get('/api/health', headers={'Host':'evil.example'}).status_code == 400


def test_lan_host_can_be_allowed_explicitly(tmp_path):
    with TestClient(create_app(tmp_path, allowed_hosts=['192.168.10.20'])) as c:
        response = c.get('/api/health', headers={'Host': '192.168.10.20:8010'})
        assert response.status_code == 200


def test_private_lan_address_is_allowed_without_dns(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        assert c.get('/api/health', headers={'Host': '10.20.30.40:8010'}).status_code == 200
        assert c.get('/api/health', headers={'Host': '172.16.2.3:8010'}).status_code == 200
        assert c.get('/api/health', headers={'Host': '192.168.1.2:8010'}).status_code == 200
        assert c.get('/api/health', headers={'Host': '8.8.8.8:8010'}).status_code == 400


def test_local_hosts_include_loopback_and_extra_value():
    hosts = local_allowed_hosts(['backtest.lan'])
    assert {'localhost', 'backtest.lan'} <= set(hosts)


def test_worker_timeout(tmp_path):
    manager = JobManager(tmp_path, timeout=.3)
    try:
        job = manager.submit(RunConfig(strategy='def handle_bar(c, bars):\n    while True: pass\n'))
        deadline = time.monotonic() + 10
        while manager.get(job['id'])['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.05)
        result = manager.get(job['id'])
        assert result['status'] == 'failed'
        assert '时限' in result['error']
    finally:
        manager.close()


def test_second_server_cannot_own_same_jobs(tmp_path):
    manager = JobManager(tmp_path)
    try:
        with pytest.raises(RuntimeError, match='另一个服务'):
            JobManager(tmp_path)
    finally:
        manager.close()
