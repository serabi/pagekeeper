# pyright: reportMissingImports=false, reportMissingModuleSource=false

import logging
import time

import requests

logger = logging.getLogger(__name__)


class JsonHttpClientBase:
    """Small helper for JSON POST calls with retry/backoff handling."""

    def post_json_with_retries(
        self,
        url,
        *,
        json_body,
        headers,
        timeout=20,
        max_retries=0,
        retry_statuses=None,
        backoff_seconds=1,
        request_func=None,
        retry_label="request",
        on_retry=None,
    ):
        retry_statuses = set(retry_statuses or [])
        request_func = request_func or requests.post
        response = request_func(url, json=json_body, headers=headers, timeout=timeout)
        attempt = 0
        backoff = backoff_seconds

        while response.status_code in retry_statuses and attempt < max_retries:
            attempt += 1
            logger.warning(
                "%s returned %s, retry %s/%s after %ss",
                retry_label,
                response.status_code,
                attempt,
                max_retries,
                backoff,
            )
            time.sleep(backoff)
            if on_retry:
                on_retry(attempt, response)
            response = request_func(url, json=json_body, headers=headers, timeout=timeout)
            backoff *= 2

        return response
