"""HTTP transport lifecycle only; renderer is controlled, no fake MP4 acceptance."""
import asyncio
import pytest
from fastapi import HTTPException
import main
from test_auto_review_export import session


def test_accepted_route_returns_before_render_and_releases_owner(session,monkeypatch):
    item,_=session
    async def scenario():
        release=asyncio.Event()
        async def render(req):
            main.active_export_tasks['export_'+req.task_id]={'status':'RUNNING'}
            await release.wait()
            main.active_export_tasks['export_'+req.task_id]={'status':'COMPLETED','video_url':'/api/outputs/test.mp4'}
            return {'status':'ok'}
        monkeypatch.setattr(main,'export_hq',render)
        response=await main.start_export_hq(main.ExportHQRequest(task_id=item.task_id))
        assert response['status']=='ACCEPTED'
        owner=main._accepted_export_owners[response['export_id']]
        assert not owner.done()
        with pytest.raises(HTTPException) as error:
            await main.start_export_hq(main.ExportHQRequest(task_id=item.task_id))
        assert error.value.status_code==409
        release.set();await owner;await asyncio.sleep(0)
        assert response['export_id'] not in main._accepted_export_owners
    asyncio.run(scenario())


def test_preflight_failure_is_returned_without_orphan_owner(session):
    item,exporter=session
    item.is_running=True
    async def scenario():
        with pytest.raises(HTTPException) as error:
            await main.start_export_hq(main.ExportHQRequest(task_id=item.task_id))
        assert error.value.status_code==409
        await asyncio.sleep(0)
        assert f'export_{item.task_id}' not in main._accepted_export_owners
        exporter.export.assert_not_called()
    asyncio.run(scenario())


def test_accepted_real_handler_publishes_same_metadata_and_sidecar(session):
    item,exporter=session
    async def scenario():
        accepted=await main.start_export_hq(main.ExportHQRequest(task_id=item.task_id))
        owner=main._accepted_export_owners.get(accepted['export_id'])
        if owner: await owner
        await asyncio.sleep(0)
        status=main.active_export_tasks[accepted['export_id']]
        assert status['status']=='COMPLETED'
        assert status['output_filename']=='reviewed.mp4'
        assert status['output_is_draft'] is True
        assert item.export_task is None
        assert accepted['export_id'] not in main._accepted_export_owners
        exporter.export.assert_called_once()
    asyncio.run(scenario())


def test_cancel_request_is_honoured_by_detached_owner_and_cleanup(session,monkeypatch):
    item,_=session
    async def scenario():
        release=asyncio.Event()
        async def render(req):
            job={'status':'RUNNING'}
            main.active_export_tasks['export_'+req.task_id]=job
            await release.wait()
            assert job['cancelled'] is True
            job['status']='CANCELLED'
        monkeypatch.setattr(main,'export_hq',render)
        accepted=await main.start_export_hq(main.ExportHQRequest(task_id=item.task_id))
        owner=main._accepted_export_owners[accepted['export_id']]
        await main.cancel_export_hq(item.task_id)
        release.set();await owner;await asyncio.sleep(0)
        assert accepted['export_id'] not in main._accepted_export_owners
    asyncio.run(scenario())


def test_render_exception_is_terminal_and_owner_is_cleared(session):
    item,exporter=session
    exporter.export.side_effect=RuntimeError('Controlled render failure')
    async def scenario():
        accepted=await main.start_export_hq(main.ExportHQRequest(task_id=item.task_id))
        owner=main._accepted_export_owners.get(accepted['export_id'])
        if owner:
            try: await owner
            except HTTPException: pass
        await asyncio.sleep(0)
        assert main.active_export_tasks[accepted['export_id']]['status']=='FAILED'
        assert item.export_task is None
        assert accepted['export_id'] not in main._accepted_export_owners
    asyncio.run(scenario())


def test_http_submit_contract_is_202_and_retains_legacy_route():
    routes={route.path:route for route in main.app.routes if hasattr(route,'status_code')}
    assert routes['/api/streaming/export-hq/start'].status_code==202
    assert '/api/streaming/export-hq' in routes
    assert '/api/streaming/export-hq/status/{task_id}' in routes
