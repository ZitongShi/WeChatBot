from __future__ import annotations

import json
import time
from typing import Any

import requests


def post_json_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int = 60,
    attempts: int = 3,
) -> requests.Response:
    last: requests.Response | None = None
    last_exc: Exception | None = None
    for idx in range(attempts):
        try:
            res = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(1.5 * (idx + 1))
            continue
        last = res
        if res.status_code < 500:
            return res
        time.sleep(1.5 * (idx + 1))
    if last is None and last_exc is not None:
        raise last_exc
    assert last is not None
    return last
