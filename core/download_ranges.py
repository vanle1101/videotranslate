"""Bounded adaptive byte ranges; only join responses for one strong entity.

The initial stream remains the default. A slow large transfer tests two
connections using bytes that become part of the file, then increases only
when the measured gain exceeds 25%. At most 8 x 4 MiB are buffered.
"""
from concurrent.futures import ThreadPoolExecutor
import re
import time
from core.douyin_resolver import _retry_after_seconds


SAMPLE_BYTES = 4 * 1024 * 1024
SEGMENT_BYTES = 4 * 1024 * 1024
MIN_REMAINING = 64 * 1024 * 1024
FAST_ENOUGH = 6 * 1024 * 1024
MAX_CONNECTIONS = 8


class RangeTransferError(Exception):
    def __init__(self, code, status_code=None, *, retry_after=None):
        self.code, self.status_code = code, status_code
        self.retry_after = _retry_after_seconds(retry_after)


def _segment(opener, url, start, end, total, etag):
    response = opener(url, headers={"Range": f"bytes={start}-{end}", "If-Range": etag})
    try:
        if response.status != 206:
            raise RangeTransferError("range_ignored", response.status,
                                     retry_after=response.headers.get("Retry-After"))
        match = re.fullmatch(r"bytes (\d{1,20})-(\d{1,20})/(\d{1,20})",
                             response.headers.get("Content-Range", ""))
        if not match or tuple(map(int, match.groups())) != (start, end, total):
            raise RangeTransferError("range_invalid", response.status)
        if response.headers.get("ETag") != etag:
            raise RangeTransferError("resource_changed", response.status)
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise RangeTransferError("range_invalid", response.status)
        kind = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if kind not in {"video/mp4", "application/octet-stream", "binary/octet-stream"}:
            raise RangeTransferError("content_type", response.status)
        expected = end - start + 1
        length = response.headers.get("Content-Length")
        if length is not None and (not str(length).isdigit() or int(length) != expected):
            raise RangeTransferError("length_mismatch", response.status)
        body = bytearray()
        for chunk in response.iter_content(256 * 1024):
            if len(body) + len(chunk) > expected:
                raise RangeTransferError("length_mismatch", response.status)
            body.extend(chunk)
        if len(body) != expected:
            raise RangeTransferError("incomplete_read", response.status)
        return body
    finally:
        response.close()


def adaptive_chunks(response, *, total, offset, etag, opener, observe):
    """Yield contiguous verified bytes, never write out of order or retry here.

    `observe` is called on the consuming thread. HTTP/read failures escape to
    the existing bounded retry/resume owner; no range failure can append an
    unverified segment. Small/fast/nonvalidated responses stay single-stream.
    """
    chunks = iter(response.iter_content(256 * 1024))
    url = getattr(response, "_request_url", None) or getattr(response, "url", None)
    eligible = (url and total and total - offset >= MIN_REMAINING
                and etag and not str(etag).startswith("W/"))
    if not eligible:
        yield from chunks
        return

    received, read_seconds = 0, 0.0
    while received < SAMPLE_BYTES:
        started = time.monotonic()
        try:
            chunk = next(chunks)
        except StopIteration:
            return
        read_seconds += time.monotonic() - started
        received += len(chunk)
        yield chunk
    baseline = received / max(read_seconds, .001)
    if baseline >= FAST_ENOUGH or total - offset - received < MIN_REMAINING:
        yield from chunks
        return

    # Every test batch is useful file data, so probing never redownloads the
    # completed prefix. Closing the original connection bounds active sockets.
    response.close()
    position = offset + received
    count, best_count, best_speed = 2, 1, baseline
    probing = True
    with ThreadPoolExecutor(max_workers=MAX_CONNECTIONS, thread_name_prefix="VideoRange") as pool:
        while position < total:
            spans = [(start, min(start + SEGMENT_BYTES - 1, total - 1))
                     for start in range(position, min(position + count * SEGMENT_BYTES, total), SEGMENT_BYTES)]
            started = time.monotonic()
            observe("parallel-range", len(spans), probing)
            futures = [pool.submit(_segment, opener, url, start, end, total, etag) for start, end in spans]
            # Complete and validate the whole bounded batch before publishing
            # any of it. A failure leaves only the durable contiguous prefix.
            try:
                bodies = [future.result() for future in futures]
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
            elapsed = max(time.monotonic() - started, .001)
            speed = sum(map(len, bodies)) / elapsed
            for body in bodies:
                for start in range(0, len(body), 256 * 1024):
                    yield memoryview(body)[start:start + 256 * 1024]
                position += len(body)
            if probing:
                if speed >= best_speed * 1.25:
                    best_count, best_speed = count, speed
                    if count < MAX_CONNECTIONS and speed < FAST_ENOUGH:
                        count *= 2
                    else:
                        probing = False
                else:
                    count, probing = best_count, False
            # Prefer fewer connections if throughput deteriorates materially.
            elif count > 1 and speed < best_speed * .6:
                count = max(1, count // 2)
                best_speed = speed
            # Futures retain their results. Release this batch before opening
            # the next one to keep live segment storage within 32 MiB.
            del body, bodies, futures
            if count == 1:
                observe("single-stream", 1, False)
                if position < total:
                    remaining = opener(url, headers={"Range": f"bytes={position}-", "If-Range": etag})
                    try:
                        match = re.fullmatch(r"bytes (\d{1,20})-(\d{1,20})/(\d{1,20})",
                                             remaining.headers.get("Content-Range", ""))
                        content_length = remaining.headers.get("Content-Length")
                        if (remaining.status != 206 or not match
                                or tuple(map(int, match.groups())) != (position, total - 1, total)
                                or remaining.headers.get("ETag") != etag
                                or remaining.headers.get("Content-Encoding", "identity").lower() != "identity"
                                or (content_length is not None and
                                    (not str(content_length).isdigit() or int(content_length) != total - position))
                                or remaining.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                                not in {"video/mp4", "application/octet-stream", "binary/octet-stream"}):
                            raise RangeTransferError(
                                "range_ignored" if remaining.status == 429 else "range_invalid",
                                remaining.status,
                                retry_after=remaining.headers.get("Retry-After"))
                        yield from remaining.iter_content(256 * 1024)
                    finally:
                        remaining.close()
                return
