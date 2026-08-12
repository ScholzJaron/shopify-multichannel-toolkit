# Shopify multichannel toolkit

Scripts and notes for running a large Shopify catalogue across TikTok Shop,
Pinterest, Google Merchant Center and the search engines, without paid tooling.

Built while operating a print-on-demand store with several thousand designs and
six figures of catalogue variants. The problems here are the ones that cost
days to diagnose and are documented nowhere obvious.

This is a template. Fork it, strip what you do not need, and change the parts
that assume your setup. Nothing here is a published package and there is no
stability guarantee.

## What is in it

| File | What it does |
| --- | --- |
| [`src/shopify_client.py`](src/shopify_client.py) | Admin GraphQL client that survives rate limiting |
| [`src/indexnow_submit.py`](src/indexnow_submit.py) | Push sitemap URLs to Bing, Yandex, Seznam, Naver |
| [`src/tiktok_brand_repair.py`](src/tiktok_brand_repair.py) | Audit and bulk-fix the TikTok Shop Brand field |
| [`docs/tiktok-brand-field.md`](docs/tiktok-brand-field.md) | Why one missing field breaks three things |
| [`docs/rate-limiting.md`](docs/rate-limiting.md) | How the Shopify leaky bucket actually behaves |

## The two things worth reading

**[Shopify rate limiting](docs/rate-limiting.md).** The Admin GraphQL API
returns `THROTTLED` with HTTP 200. A client that only checks the status code
treats a throttled response as a successful empty one, and a bulk job will
report success while writing nothing. `shopify_client.py` reads the throttle
status Shopify returns and waits exactly as long as the bucket needs.

**[The TikTok Brand field](docs/tiktok-brand-field.md).** A missing Brand value
produces deactivations, `System error` on bulk activation, and sync failures --
three symptoms on three screens, none of which names the field. This one is
worth reading before you open a support ticket.

## Setup

```bash
git clone https://github.com/<you>/shopify-multichannel-toolkit
cd shopify-multichannel-toolkit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill it in
```

Every script reads its configuration from the environment and fails loudly if
something is missing. There are no hardcoded defaults for keys, tokens or store
identifiers, and there should never be — a key committed to source is a key
anyone can use against your domain.

## Usage

```bash
# Submit everything in your sitemap to IndexNow
python src/indexnow_submit.py sitemap

# Submit specific URLs
python src/indexnow_submit.py urls https://example.com/a https://example.com/b

# See what is wrong with your TikTok brand column
python src/tiktok_brand_repair.py audit export.xlsx --authorised "Your Brand"

# Write corrected bulk-upload files
python src/tiktok_brand_repair.py repair export.xlsx \\
    --brand "Your Brand" --out-dir uploads --chunk-size 100
```

Using the Shopify client directly:

```python
from src.shopify_client import ShopifyClient

client = ShopifyClient.from_env()

QUERY = """
query Products($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    edges { node { id title } }
    pageInfo { hasNextPage endCursor }
  }
}
"""

for product in client.paginate(QUERY, path="products"):
    print(product["title"])
```

## Scheduling

`.github/workflows/indexnow.yml.example` runs the IndexNow submitter on a
schedule using GitHub Actions. Rename it to `.yml`, set the secrets under
Settings, Secrets and variables, Actions, and adjust the cron.

Scheduled workflows on a repository with no other activity get disabled after
60 days of inactivity. GitHub emails you first.

## Contributing

Issues and pull requests welcome, particularly:

- Failure modes on other channels that present the same way — one missing field
  surfacing as several unrelated-looking errors
- Corrections where platform behaviour has changed
- Column-name variants for TikTok bulk templates in other regions

## Licence

MIT. See [LICENSE](LICENSE).
