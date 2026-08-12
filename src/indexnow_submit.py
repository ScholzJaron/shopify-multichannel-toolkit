"""Submit sitemap URLs to IndexNow (Bing, Yandex, Seznam, Naver).

IndexNow lets you tell participating search engines that URLs have changed,
instead of waiting for a crawl. Useful for large catalogues where a bulk SEO
change touches thousands of pages at once.

Shopify serves a nested sitemap: ``/sitemap.xml`` is an index pointing at
per-resource sitemaps. This script resolves that tree recursively, then submits
in batches.

Setup
-----
1. Generate a key: 8-128 hex characters. ``openssl rand -hex 16`` works.
2. Host it at ``https://<your-host>/<key>.txt`` containing exactly the key.
   On Shopify, a URL redirect from ``/<key>.txt`` to a page containing only the
   key is the usual workaround, since you cannot add arbitrary root files.
3. Set INDEXNOW_KEY and INDEXNOW_HOST in the environment.

There is deliberately no hardcoded key fallback. A key committed to source is a
key anyone can use to submit URL changes for your domain.

Usage::

    python src/indexnow_submit.py sitemap
    python src/indexnow_submit.py urls https://example.com/a https://example.com/b
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ENDPOINT = "https://api.indexnow.org/IndexNow"
BATCH_SIZE = 10_000  # IndexNow's documented per-request ceiling
SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
USER_AGENT = "indexnow-submitter/1.0"


def require_env(name: str, hint: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing {name}. {hint}")
    return value


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return gzip.decompress(raw) if url.endswith(".gz") else raw


def sitemap_urls(url: str, seen: set[str] | None = None) -> list[str]:
    """Resolve a sitemap or sitemap index recursively into a flat URL list."""
    seen = set() if seen is None else seen
    if url in seen:
        return []
    seen.add(url)

    try:
        root = ET.fromstring(fetch(url))
    except (urllib.error.URLError, ET.ParseError, OSError) as exc:
        print(f"  ! could not read {url}: {exc}")
        return []

    found: list[str] = []
    for child in root.findall(".//s:sitemap/s:loc", SITEMAP_NS):
        if child.text:
            found += sitemap_urls(child.text.strip(), seen)
    for child in root.findall(".//s:url/s:loc", SITEMAP_NS):
        if child.text:
            found.append(child.text.strip())
    return found


def submit(urls: list[str], host: str, key: str, key_location: str) -> int:
    """Post URLs to IndexNow in batches. Returns the count accepted."""
    if not urls:
        print("No URLs to submit.")
        return 0

    total = 0
    for start in range(0, len(urls), BATCH_SIZE):
        chunk = urls[start : start + BATCH_SIZE]
        payload = json.dumps(
            {
                "host": host,
                "key": key,
                "keyLocation": key_location,
                "urlList": chunk,
            }
        ).encode()

        request = urllib.request.Request(
            ENDPOINT,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": USER_AGENT,
            },
        )

        batch_number = start // BATCH_SIZE + 1
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                print(f"  batch {batch_number}: {len(chunk)} URLs -> HTTP {response.status}")
                total += len(chunk)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            print(f"  batch {batch_number}: HTTP {exc.code} -- {detail}")
        except urllib.error.URLError as exc:
            print(f"  batch {batch_number}: failed -- {exc}")

        time.sleep(1)

    return total


def main(argv: list[str]) -> int:
    key = require_env("INDEXNOW_KEY", "Generate one with: openssl rand -hex 16")
    host = require_env("INDEXNOW_HOST", "For example: www.example.com")
    key_location = os.environ.get("INDEXNOW_KEY_URL", f"https://{host}/{key}.txt")

    mode = argv[1] if len(argv) > 1 else "sitemap"

    if mode == "sitemap":
        source = os.environ.get("SITEMAP_URL", f"https://{host}/sitemap.xml")
        print(f"Resolving {source} ...")
        urls = sitemap_urls(source)
        print(f"Found {len(urls)} URLs.")
    elif mode == "urls":
        urls = argv[2:]
        if not urls:
            print("Pass one or more URLs after 'urls'.")
            return 1
    else:
        print(__doc__)
        return 1

    accepted = submit(urls, host=host, key=key, key_location=key_location)
    print(f"Submitted {accepted} URLs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
