"""Run FFmpeg work with cooperative cancellation and bounded diagnostic output."""
import subprocess


def run_media(command, cancel_check=None, capture_output=False):
    if cancel_check and cancel_check():
        raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel_check and cancel_check():
                    raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")
        if cancel_check and cancel_check():
            raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")
        if process.returncode:
            detail = (stderr or b"").decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"FFmpeg thất bại: {detail}")
        return stdout or b""
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
