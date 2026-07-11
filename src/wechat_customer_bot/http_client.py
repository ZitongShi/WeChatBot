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
    for idx in range(attempts):
        res = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
        last = res
        if res.status_code < 500:
            return res
        time.sleep(1.5 * (idx + 1))
    assert last is not None
    return last
