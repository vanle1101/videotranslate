"""Shared transient backoff parsing; durable bursts belong to the task owner."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random


def retry_after_seconds(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            moment = parsedate_to_datetime(value)
            seconds = (moment - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def transient_delay(attempt, retry_after=None):
    # Equal jitter stays within the documented 2..60-second backoff range.
    base = min(60.0, 2.0 * 2 ** attempt)
    delay = min(60.0, max(2.0, base * random.uniform(.75, 1.25)))
    requested = retry_after_seconds(retry_after)
    return max(delay, requested) if requested is not None else delay
