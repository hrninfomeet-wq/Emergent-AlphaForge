"""Execute the approved production paper-stop function with isolated fake I/O."""
import ast
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('mode_changes_after_read', [False, True])
@pytest.mark.parametrize('close_summaries,closed_count', [
    ([], 0),
    ([{'id': 'paper-position'}], 1),
    ([{'id': 'paper-position', 'error': 'write failed'}], 0),
    ([{'id': 'closed'}, {'id': 'failed', 'error': 'write failed'},
      {'id': 'skipped', 'skipped': 'allow_overnight'}], 1),
])
def test_paper_stop_cannot_pause_live_or_change_any_mode(mode_changes_after_read, close_summaries, closed_count):
    source = (Path(__file__).resolve().parents[1] /
              'backend/app/routers/deployments.py').read_text(encoding='utf-8')
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == 'stop_all_paper_deployments')
    node.decorator_list = []
    rows = [dict(id='paper', mode='paper', status='ACTIVE'),
            dict(id='live', mode='live', status='ACTIVE'),
            dict(id='signals', mode='signal_only', status='ACTIVE')]
    def matches(row, query):
        return all(row.get(k) in v['$in'] if isinstance(v, dict) else row.get(k) == v
                   for k, v in query.items())
    class Collection:
        def find(self, query, projection):
            selected = [dict(id=row['id']) for row in rows if matches(row, query)]
            async def to_list(**kw):
                if mode_changes_after_read:
                    rows[0]['mode'] = 'live'
                return selected
            return SimpleNamespace(to_list=to_list)
        async def update_one(self, query, update):
            assert 'mode' not in update['$set']
            changed = 0
            for row in rows:
                if matches(row, query):
                    assert row['mode'] != 'live'
                    row.update(update['$set']); changed += 1
            return SimpleNamespace(modified_count=changed)
    async def close_paper(*args, **kwargs):
        assert kwargs['reason'] == 'manual_stop_all_paper'
        return close_summaries
    scope = dict(get_db=lambda: SimpleNamespace(strategy_deployments=Collection()),
                 square_off_open_paper_trades=close_paper, serialize_doc=lambda x:x,
                 upstox_stream_manager=SimpleNamespace(latest_tick_map=lambda: {}),
                 datetime=datetime, timezone=timezone)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<isolated-paper-stop>', 'exec'), scope)
    result = asyncio.run(scope[node.name]())
    assert rows[1] == dict(id='live', mode='live', status='ACTIVE')
    assert rows[0]['status'] == ('ACTIVE' if mode_changes_after_read else 'PAUSED')
    assert result['paused_deployment_ids'] == (['signals'] if mode_changes_after_read else ['paper','signals'])
    assert result['squared_off_count'] == closed_count
    assert len(result['failed']) == sum('error' in s for s in close_summaries)
    assert len(result['skipped']) == sum('skipped' in s for s in close_summaries)
    assert all('error' not in s and 'skipped' not in s for s in result['squared_off'])


@pytest.mark.parametrize('failed,skipped,level', [(0, 0, 'success'), (1, 0, 'warning'), (0, 1, 'warning')])
def test_paper_stop_notification_reports_partial_failure_without_any_live_api(failed, skipped, level):
    source = (Path(__file__).resolve().parents[1] /
              'frontend/src/pages/PaperTrading.jsx').read_text(encoding='utf-8')
    start = source.index('  const doStopAll = async () => {')
    end = source.index('\n  };', start) + len('\n  };')
    body = source[start:end]
    response = dict(squared_off_count=1, paused_deployment_ids=['paper'],
                    failed=[{'error': 'fixture'}] * failed, skipped=[{'skipped': 'fixture'}] * skipped)
    script = '''
    const messages = [];
    const toast = Object.fromEntries(['success','warning','error'].map(level =>
      [level, message => messages.push({level,message})]));
    const factory = new Function('livePos','window','setBusy','api','toast','refreshAll', BODY + ';return doStopAll;');
    const action = factory({items:[]}, {confirm:()=>true}, ()=>{},
      {stopAllPaperDeployments:async()=>(RESPONSE)}, toast, async()=>{});
    await action();
    process.stdout.write(JSON.stringify(messages));
    '''.replace('BODY', json.dumps(body)).replace('RESPONSE', json.dumps(response))
    result = subprocess.run(['node', '--input-type=module', '-e', script],
                            capture_output=True, text=True, check=True, timeout=15)
    messages = json.loads(result.stdout)
    assert len(messages) == 1
    assert messages[0]['level'] == level
    assert 'closed 1' in messages[0]['message']
    if level == 'warning':
        assert f'failed {failed}' in messages[0]['message']
        assert f'skipped {skipped}' in messages[0]['message']
