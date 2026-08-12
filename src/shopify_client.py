"""Throttle-aware Shopify Admin GraphQL client.

The Shopify Admin GraphQL API uses a leaky-bucket rate limiter. Every query has
a cost; the bucket refills at a fixed restore rate. When you drain it, Shopify
returns a THROTTLED error instead of your data -- with HTTP 200, so naive
clients silently treat it as a successful empty response and corrupt their run.

This client handles that properly:

  * reads ``extensions.cost.throttleStatus`` from every response and waits only
    as long as the bucket actually needs to refill
  * retries THROTTLED errors with exponential backoff and jitter
  * honours ``Retry-After`` on HTTP 429 and retries 5xx
  * raises on userErrors so mutations fail loudly instead of silently

No credentials or store identifiers are embedded here. Everything comes from
the environment -- see .env.example.

Usage::

    from shopify_client import ShopifyClient

    client = ShopifyClient.from_env()
    data = client.execute(QUERY, {"first": 50})

    for product in client.paginate(PRODUCTS_QUERY, path="products"):
        print(product["title"])
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Iterator

import requests

DEFAULT_API_VERSION = "2025-07"
DEFAULT_MAX_RETRIES = 6
DEFAULT_TIMEOUT = 60


class ShopifyError(RuntimeError):
    """Base error for anything the Shopify API refuses to do."""


class ShopifyUserError(ShopifyError):
    """A mutation returned userErrors -- the request was valid, the data wasn't."""


class ShopifyThrottleError(ShopifyError):
    """Still throttled after exhausting retries."""


class ShopifyClient:
    """Minimal, throttle-aware wrapper around the Admin GraphQL endpoint."""

    def __init__(
        self,
        store: str,
        access_token: str,
        api_version: str = DEFAULT_API_VERSION,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: int = DEFAULT_TIMEOUT,
        min_bucket_reserve: float = 100.0,
    ) -> None:
        """
        Args:
            store: your ``*.myshopify.com`` handle, with or without the suffix.
            access_token: Admin API access token for a custom app.
            api_version: Admin API version string, e.g. ``2025-07``.
            max_retries: attempts before giving up on a throttled request.
            timeout: per-request timeout in seconds.
            min_bucket_reserve: pre-emptively sleep when the bucket drops below
                this many points. Keeps long batch jobs from thrashing.
        """
        if not store:
            raise ValueError("store is required")
        if not access_token:
            raise ValueError("access_token is required")

        self.store = store if store.endswith(".myshopify.com") else f"{store}.myshopify.com"
        self.endpoint = f"https://{self.store}/admin/api/{api_version}/graphql.json"
        self.max_retries = max_retries
        self.timeout = timeout
        self.min_bucket_reserve = min_bucket_reserve

        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Shopify-Access-Token": access_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> "ShopifyClient":
        """Build a client from SHOPIFY_STORE and SHOPIFY_ADMIN_TOKEN.

        Raises a clear error rather than falling back to a default, so a missing
        variable fails at startup instead of halfway through a batch job.
        """
        store = os.environ.get("SHOPIFY_STORE")
        token = os.environ.get("SHOPIFY_ADMIN_TOKEN")
        if not store or not token:
            raise ShopifyError(
                "Set SHOPIFY_STORE and SHOPIFY_ADMIN_TOKEN in the environment. "
                "See .env.example."
            )
        kwargs: dict[str, Any] = {
            "api_version": os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
        }
        kwargs.update(overrides)
        return cls(store=store, access_token=token, **kwargs)

    # ------------------------------------------------------------------
    # core request path
    # ------------------------------------------------------------------

    def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        check_user_errors: bool = True,
    ) -> dict[str, Any]:
        """Run a GraphQL document and return its ``data`` payload.

        Retries THROTTLED, 429 and 5xx. Raises ShopifyUserError if the response
        carries userErrors, so a failed mutation never looks like a success.
        """
        payload = {"query": query, "variables": variables or {}}
        delay = 1.0

        for attempt in range(1, self.max_retries + 1):
            response = self.session.post(self.endpoint, json=payload, timeout=self.timeout)

            if response.status_code == 429:
                wait = float(response.headers.get("Retry-After", delay))
                self._sleep(wait, attempt, "HTTP 429")
                delay = min(delay * 2, 60)
                continue

            if response.status_code >= 500:
                self._sleep(delay, attempt, f"HTTP {response.status_code}")
                delay = min(delay * 2, 60)
                continue

            if response.status_code != 200:
                raise ShopifyError(
                    f"HTTP {response.status_code} from Shopify: {response.text[:500]}"
                )

            body = response.json()

            if self._is_throttled(body):
                wait = self._throttle_wait(body, fallback=delay)
                self._sleep(wait, attempt, "THROTTLED")
                delay = min(delay * 2, 60)
                continue

            if body.get("errors"):
                raise ShopifyError(f"GraphQL errors: {body['errors']}")

            data = body.get("data") or {}
            if check_user_errors:
                self._raise_for_user_errors(data)

            self._respect_bucket(body)
            return data

        raise ShopifyThrottleError(
            f"Still throttled after {self.max_retries} attempts. "
            "Lower your batch size or raise max_retries."
        )

    def paginate(
        self,
        query: str,
        *,
        path: str,
        variables: dict[str, Any] | None = None,
        page_size: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Walk a Relay connection, yielding one node at a time.

        ``query`` must accept ``$first`` and ``$after`` and request
        ``pageInfo { hasNextPage endCursor }`` on the connection at ``path``.
        ``path`` may be dotted, e.g. ``"collection.products"``.
        """
        cursor: str | None = None
        base = dict(variables or {})

        while True:
            page_vars = {**base, "first": page_size, "after": cursor}
            data = self.execute(query, page_vars)

            connection = data
            for segment in path.split("."):
                connection = connection.get(segment) or {}

            for edge in connection.get("edges", []):
                node = edge.get("node")
                if node is not None:
                    yield node

            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")
            if not cursor:
                return

    # ------------------------------------------------------------------
    # throttle handling
    # ------------------------------------------------------------------

    @staticmethod
    def _is_throttled(body: dict[str, Any]) -> bool:
        for error in body.get("errors") or []:
            code = (error.get("extensions") or {}).get("code", "")
            if code == "THROTTLED":
                return True
            if "throttled" in str(error.get("message", "")).lower():
                return True
        return False

    @staticmethod
    def _throttle_status(body: dict[str, Any]) -> dict[str, Any]:
        return ((body.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}

    def _throttle_wait(self, body: dict[str, Any], fallback: float) -> float:
        """Work out how long the leaky bucket needs to refill.

        Shopify tells us the current fill level, the restore rate and what the
        query costs. Waiting exactly that long beats a blind exponential sleep.
        """
        status = self._throttle_status(body)
        cost = (body.get("extensions") or {}).get("cost") or {}

        available = status.get("currentlyAvailable")
        restore_rate = status.get("restoreRate")
        requested = cost.get("requestedQueryCost")

        if available is None or not restore_rate or requested is None:
            return fallback

        shortfall = float(requested) - float(available)
        if shortfall <= 0:
            return fallback
        return min(shortfall / float(restore_rate) + 0.5, 60.0)

    def _respect_bucket(self, body: dict[str, Any]) -> None:
        """Pre-emptively pause when the bucket is nearly drained."""
        status = self._throttle_status(body)
        available = status.get("currentlyAvailable")
        restore_rate = status.get("restoreRate")

        if available is None or not restore_rate:
            return
        if float(available) >= self.min_bucket_reserve:
            return

        deficit = self.min_bucket_reserve - float(available)
        time.sleep(min(deficit / float(restore_rate), 10.0))

    @staticmethod
    def _sleep(seconds: float, attempt: int, reason: str) -> None:
        jittered = seconds + random.uniform(0, 0.5)
        print(f"  [{reason}] attempt {attempt}, waiting {jittered:.1f}s")
        time.sleep(jittered)

    # ------------------------------------------------------------------
    # error surfacing
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_for_user_errors(data: Any) -> None:
        """Walk the response and raise on any populated userErrors list.

        Shopify returns HTTP 200 with an empty result when a mutation fails
        validation. Without this check a bulk job reports success while writing
        nothing at all.
        """
        stack = [data]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key, value in current.items():
                    if key in ("userErrors", "mediaUserErrors") and value:
                        raise ShopifyUserError(str(value))
                    stack.append(value)
            elif isinstance(current, list):
                stack.extend(current)
