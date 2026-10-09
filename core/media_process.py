"""Cancellable media processes with disk-backed, bounded diagnostics/output."""
import math
import subprocess
import tempfile
import time


def run_media(command, cancel_check=None, capture_output=False, *, timeout=21600,
              max_capture_bytes=32 * 1024 * 1024, max_diagnostic_bytes=8 * 1024 * 1024):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Media timeout must be finite and positive")
    if any(type(value) is not int or value <= 0 for value in (max_capture_bytes, max_diagnostic_bytes)):
        raise ValueError("Media output limits must be positive integers")
    if cancel_check and cancel_check():
        raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")
    deadline = time.monotonic() + timeout
    # communicate() retains every error byte in RAM. Redirect to bounded
    # temporary files instead; cleanup closes/removes both on every outcome.
    with tempfile.TemporaryFile() as diagnostics, tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command, stdout=output if capture_output else subprocess.DEVNULL,
            stderr=diagnostics,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            while True:
                if cancel_check and cancel_check():
                    raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Xử lý media quá thời gian; checkpoint và đầu ra trước vẫn được giữ.")
                if diagnostics.seek(0, 2) > max_diagnostic_bytes:
                    raise RuntimeError("FFmpeg trả quá nhiều lỗi; dừng để bảo vệ tài nguyên.")
                if capture_output and output.seek(0, 2) > max_capture_bytes:
                    raise RuntimeError("Dữ liệu media vượt giới hạn bộ nhớ; cần xử lý qua tệp.")
                try:
                    process.wait(timeout=min(.2, max(.001, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if cancel_check and cancel_check():
                raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")
            diagnostic_size = diagnostics.seek(0, 2)
            diagnostics.seek(max(0, diagnostic_size - 2000))
            if process.returncode:
                detail = diagnostics.read(2000).decode("utf-8", errors="replace")
                raise RuntimeError(f"FFmpeg thất bại: {detail}")
            if diagnostic_size > max_diagnostic_bytes:
                raise RuntimeError("FFmpeg trả quá nhiều lỗi; dừng để bảo vệ tài nguyên.")
            if not capture_output:
                return b""
            if output.seek(0, 2) > max_capture_bytes:
                raise RuntimeError("Dữ liệu media vượt giới hạn bộ nhớ; cần xử lý qua tệp.")
            output.seek(0)
            return output.read(max_capture_bytes)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
