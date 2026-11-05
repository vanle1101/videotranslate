"""Native fault cleanup for the bounded QA experiment, without loading a model."""
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from workspace.audit.recovery_20261010 import guard_omni7b_qualification as guard


@pytest.mark.parametrize('fault', ['publish', 'reserve', 'deadline'])
def test_guard_reclaims_real_experiment_tree_on_fault(tmp_path, monkeypatch, fault):
    marker=tmp_path/'child.txt'
    script=("import subprocess,sys,time; from pathlib import Path; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            f"Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(60)")
    child=subprocess.Popen([sys.executable,'-c',script],
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    targets=[]
    try:
        deadline=time.monotonic()+10
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.02)
        assert marker.exists(), 'Fixture did not start its descendant'
        targets,_=guard.process_tree(child)
        assert len(targets)>=2
        status={'state':'QUALIFICATION_RUNNING'}
        def publish(value):
            if fault=='publish':raise OSError('Injected status writer failure')
        if fault=='reserve':
            monkeypatch.setattr(guard.psutil,'virtual_memory',lambda:SimpleNamespace(available=1))
        if fault=='publish':
            with pytest.raises(OSError,match='status writer'):
                guard.supervise_child(child,status,publish,sample_interval=.01)
            assert status['state']=='GUARD_FAILED'
            assert 'Injected status writer failure' in status['traceback']
        else:
            guard.supervise_child(child,status,publish,duration=-1 if fault=='deadline' else 10,
                                  sample_interval=.01)
            assert status['state']=='STOPPED_RESOURCE_GUARD'
            assert status['reason']==('RAM_RESERVE_REQUIRED' if fault=='reserve'
                                      else 'BOUNDED_QUALIFICATION_DEADLINE')
        assert child.poll() is not None
        assert not any(p.is_running() for p in targets)
    finally:
        live,_=guard.process_tree(child)
        guard.terminate_tree(live+targets)
        child.wait(timeout=10)


def test_success_retains_actual_child_exit_code(monkeypatch):
    child=subprocess.Popen([sys.executable,'-c','raise SystemExit(7)'],
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        status={'state':'QUALIFICATION_RUNNING'}
        assert guard.supervise_child(child,status,lambda value:None,sample_interval=.02)==7
        assert status['state']=='QUALIFICATION_RUNNING'
    finally:
        targets,_=guard.process_tree(child)
        guard.terminate_tree(targets)
