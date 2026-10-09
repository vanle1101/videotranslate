"""Real owned processes; these checks are not provider acceptance."""
import sys
import time

import pytest

from core.media_process import run_media


def test_real_output_is_returned():
    assert run_media([sys.executable, "-c", "print('readable')"], capture_output=True).strip() == b"readable"


def test_real_media_timeout_kills_and_reaps():
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        run_media([sys.executable, "-c", "import time;time.sleep(30)"], timeout=.3)
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_real_unbounded_output_is_rejected(stream):
    command = [sys.executable, "-c", f"import sys;sys.{stream}.write('x'*10000);sys.{stream}.flush()"]
    with pytest.raises(RuntimeError, match="vượt giới hạn|quá nhiều"):
        run_media(command, capture_output=True, max_capture_bytes=1000, max_diagnostic_bytes=1000)


def test_real_process_cancellation_is_prompt():
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="hủy"):
        run_media([sys.executable, "-c", "import time;time.sleep(30)"],
                  cancel_check=lambda: time.monotonic() - started > .2)
    assert time.monotonic() - started < 5
