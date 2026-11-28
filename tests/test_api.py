import json
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
    with TestClient(create_app(tmp_path, data_source='demo')) as c:
        health = c.get('/api/health')
        assert health.json()['status'] == 'ok'
        assert "style-src 'self' 'unsafe-inline'" in health.headers['content-security-policy']
        assert '策略研究' in c.get('/').text
        editor_asset = c.get('/editor.js')
        assert editor_asset.status_code == 200
        assert len(editor_asset.content) > 100_000
        response = c.post('/api/runs', json={'strategy': EXAMPLE}, headers=HEADERS)
        assert response.status_code == 201, response.text
        identifier = response.json()['id']
        result = wait_job(c, identifier)
        assert result['status'] == 'completed', result
        assert result['result']['metrics']['trade_count'] == 1
        preview = json.loads((tmp_path / identifier / 'preview.json').read_text())
        assert preview['equity_curve'][-1]['date'] == '2026-11-13'
        assert (tmp_path / identifier / 'strategy.py').read_text() == EXAMPLE
        assert c.get('/api/runs').json()[0]['id'] == identifier
    with TestClient(create_app(tmp_path, data_source='demo')) as c:
        assert c.get(f'/api/runs/{identifier}').json()['status'] == 'completed'


def test_cancel_infinite_strategy(tmp_path):
    with TestClient(create_app(tmp_path, data_source='demo')) as c:
        strategy = 'def handle_bar(c, bars):\n    while True: pass\n'
        identifier = c.post('/api/runs', json={'strategy': strategy}, headers=HEADERS).json()['id']
        response = c.post(f'/api/runs/{identifier}/cancel', headers=HEADERS)
        assert response.json()['status'] == 'cancelled'
        assert c.get(f'/api/runs/{identifier}').json()['status'] == 'cancelled'


def test_strategy_exception_is_reported(tmp_path):
    with TestClient(create_app(tmp_path, data_source='demo')) as c:
        strategy = 'def handle_bar(c, bars):\n    raise ValueError("strategy exploded")\n'
        identifier = c.post('/api/runs', json={'strategy': strategy}, headers=HEADERS).json()['id']
        job = wait_job(c, identifier)
        assert job['status'] == 'failed'
        assert 'strategy exploded' in job['error']


def test_validation_and_cross_site_requests(tmp_path):
    with TestClient(create_app(tmp_path, data_source='demo')) as c:
        assert c.post('/api/runs', json={'strategy': EXAMPLE}).status_code == 403
        assert c.post('/api/runs', json={'strategy': EXAMPLE}, headers={**HEADERS,'Origin':'https://example.com'}).status_code == 403
        assert c.post('/api/runs', json={'strategy': 'def broken('}, headers=HEADERS).status_code == 422
        assert c.post('/api/runs', json={'strategy': EXAMPLE,'start':'2026-11-05','end':'2026-11-01'}, headers=HEADERS).status_code == 422
        assert c.post('/api/runs', json={'strategy': EXAMPLE,'symbols':['688001.SH']}, headers=HEADERS).status_code == 422
        assert c.get('/api/runs/invalid').status_code == 404
        assert c.get('/api/health', headers={'Host':'evil.example'}).status_code == 400


def test_strategy_repository_and_run_summary(tmp_path):
    runs, strategy_root = tmp_path / 'runs', tmp_path / 'strategies'
    with TestClient(create_app(runs, strategy_root=strategy_root, data_source='demo')) as c:
        seeded = c.get('/api/strategies').json()
        assert len(seeded) == 1
        assert seeded[0]['name'] == '我的第一个策略'
        assert 'code' not in seeded[0]
        assert seeded[0]['run_count'] == 0

        response = c.post(
            '/api/strategies',
            json={
                'name': '  分钟突破  ',
                'description': '下一交易日寻找突破信号',
                'market': 'A股',
                'frequency': '日线 + 分钟',
                'folder': '趋势策略/突破',
                'tags': ['突破', '突破', '盘前选股'],
                'code': EXAMPLE,
            },
            headers=HEADERS,
        )
        assert response.status_code == 201, response.text
        strategy = response.json()
        assert strategy['name'] == '分钟突破'
        assert strategy['tags'] == ['突破', '盘前选股']
        assert strategy['folder'] == '趋势策略/突破'

        renamed = c.post(
            '/api/strategy-folders/rename',
            json={'current': '趋势策略/突破', 'replacement': '趋势策略/均线'},
            headers=HEADERS,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()['strategy_count'] == 1
        assert c.get(f"/api/strategies/{strategy['id']}").json()['folder'] == '趋势策略/均线'
        assert c.post(
            '/api/strategy-folders/rename',
            json={'current': '趋势策略/均线', 'replacement': '默认目录'},
            headers=HEADERS,
        ).status_code == 409
        assert c.post(
            '/api/strategy-folders/rename',
            json={'current': '不存在', 'replacement': '新目录'},
            headers=HEADERS,
        ).status_code == 404

        updated = c.put(
            f"/api/strategies/{strategy['id']}",
            json={**strategy, 'description': '盘后选股，次日日内触发', 'status': 'active'},
            headers=HEADERS,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()['description'] == '盘后选股，次日日内触发'

        run = c.post(
            '/api/runs',
            json={'strategy_id': strategy['id'], 'strategy': EXAMPLE},
            headers=HEADERS,
        )
        assert run.status_code == 201, run.text
        result = wait_job(c, run.json()['id'])
        assert result['status'] == 'completed'
        summary = next(item for item in c.get('/api/strategies').json() if item['id'] == strategy['id'])
        assert summary['run_count'] == 1
        assert summary['last_run']['id'] == run.json()['id']

        assert c.post('/api/strategies', json={'name': '坏策略', 'code': 'def bad('}, headers=HEADERS).status_code == 422
        assert c.get('/api/strategies/not-an-id').status_code == 404
        assert c.post('/api/runs', json={'strategy_id': '0' * 32, 'strategy': EXAMPLE}, headers=HEADERS).status_code == 422


def test_lan_host_can_be_allowed_explicitly(tmp_path):
    with TestClient(create_app(tmp_path, allowed_hosts=['192.168.10.20'], data_source='demo')) as c:
        response = c.get('/api/health', headers={'Host': '192.168.10.20:8010'})
        assert response.status_code == 200


def test_private_lan_address_is_allowed_without_dns(tmp_path):
    with TestClient(create_app(tmp_path, data_source='demo')) as c:
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


def test_separate_strategy_page_and_legacy_instrument(tmp_path):
    import json
    with TestClient(create_app(tmp_path / 'runs', data_source='demo')) as c:
        strategy = c.get('/api/strategies').json()[0]
        path = tmp_path / 'strategies' / f"{strategy['id']}.json"
        old = json.loads(path.read_text())
        old.pop('instrument_type')
        path.write_text(json.dumps(old))
        assert c.get(f"/api/strategies/{strategy['id']}").json()['instrument_type'] == 'stock'
        assert 'workbench-view' not in c.get('/').text
        page = c.get(f"/strategies/{strategy['id']}")
        assert page.status_code == 200
        assert 'strategy-view' not in page.text
        assert 'id="source"' not in page.text
        assert '策略工作台' not in page.text
        assert '合成示例环境' not in page.text
        assert '载入示例' not in page.text
        assert '接口说明' not in page.text
        assert '生命周期与交易接口' not in page.text
        assert 'class="section-label">Python 策略' not in page.text
        assert 'strategy.py' not in page.text
        assert 'Python · UTF-8' not in page.text
        assert 'id="config-dialog"' in page.text
        assert 'id="leave-dialog"' in page.text
        assert 'id="strategy-editor"' in page.text
        assert 'src="/editor.js?v=' in page.text
        assert page.headers['cache-control'] == 'no-cache'
        assert c.get('/research.css').headers['cache-control'] == 'no-cache'
        assert 'breadcrumb-strategy' not in page.text
        assert 'strategy-status-dot' not in page.text
        assert '>保存策略<' not in page.text
        assert c.get('/strategies/not-found').status_code == 404
        for field, value in [('instrument_type', 'future'), ('market', '美股')]:
            assert c.post('/api/strategies', json={'name': 'x', 'code': EXAMPLE, field: value}, headers=HEADERS).status_code == 422
            assert c.post('/api/runs', json={'strategy': EXAMPLE, field: value}, headers=HEADERS).status_code == 422


def test_data_source_is_owned_by_server_and_instrument_is_persisted(tmp_path):
    with TestClient(create_app(tmp_path, data_source='demo')) as c:
        assert c.get('/api/environment').json() == {'data_source': 'demo'}
        response = c.post('/api/runs', json={'strategy': EXAMPLE, 'source': 'sdk'}, headers=HEADERS)
        assert response.status_code == 201
        job = wait_job(c, response.json()['id'])
        assert job['status'] == 'completed'
        for config in [job, job['config'], job['result']['configuration']]:
            assert config['source'] == 'demo'
            assert config['instrument_type'] == 'stock'
            assert config['market'] == 'A股'
    with TestClient(create_app(tmp_path, data_source='sdk')) as c:
        assert c.get('/api/environment').json()['data_source'] == 'sdk'
    with pytest.raises(ValueError, match='QUANTSTONE_DATA_SOURCE'):
        create_app(tmp_path, data_source='unknown')


def test_sdk_is_the_default_data_source(tmp_path, monkeypatch):
    monkeypatch.delenv('QUANTSTONE_DATA_SOURCE', raising=False)
    with TestClient(create_app(tmp_path)) as c:
        assert c.get('/api/environment').json() == {'data_source': 'sdk'}


def test_demo_source_rejects_misleading_long_horizon(tmp_path):
    with TestClient(create_app(tmp_path, data_source='demo')) as c:
        response = c.post(
            '/api/runs',
            json={'strategy': EXAMPLE, 'start': '2022-01-01', 'end': '2026-11-13'},
            headers=HEADERS,
        )
        assert response.status_code == 422
        assert '最多 90 天' in response.json()['detail']
