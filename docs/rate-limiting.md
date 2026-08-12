# Shopify Admin GraphQL rate limiting

## The trap

The Admin GraphQL API returns throttling errors with **HTTP 200**. The body
looks like this:

```json
{
  "errors": [{
    "message": "Throttled",
    "extensions": { "code": "THROTTLED" }
  }],
  "extensions": {
    "cost": {
      "requestedQueryCost": 302,
      "throttleStatus": {
        "maximumAvailable": 2000,
        "currentlyAvailable": 118,
        "restoreRate": 100
      }
    }
  }
}
```

A client that checks `response.status_code == 200` and reads `data` gets `None`
and carries on. Across a bulk job this means a run that reports success while
having written nothing for a large fraction of its records. The failure is
silent, and you find it days later when the catalogue is inconsistent.

Always check `errors` for `extensions.code == "THROTTLED"`, not just the status
code.

## The bucket

Rate limiting is a leaky bucket measured in cost points, not requests.

- The bucket holds `maximumAvailable` points, 2000 on a standard plan.
- Every query costs `requestedQueryCost` points, computed from the shape of the
  query -- how many connections, how many nodes requested.
- It refills at `restoreRate` points per second, 100 on a standard plan.

So a query costing 300 points can run roughly every 3 seconds sustained, and
you can burst until the bucket empties.

## Backing off correctly

Shopify tells you everything needed to wait exactly long enough:

```
shortfall = requestedQueryCost - currentlyAvailable
wait      = shortfall / restoreRate
```

Blind exponential backoff either wastes time or hammers the endpoint. Reading
the throttle status is strictly better, with exponential backoff kept only as a
fallback when the extensions block is absent.

`shopify_client.py` also pauses pre-emptively when the bucket drops below a
reserve threshold, which keeps long jobs from oscillating between full speed
and throttled.

## Reduce cost before adding retries

Retries treat the symptom. Cheaper queries treat the cause:

- Request only the fields you use. Cost scales with requested nodes.
- Lower page sizes. `first: 250` is not free; 50 is often faster end to end
  because it throttles less.
- Use `bulkOperationRunQuery` for genuinely large reads. It runs asynchronously
  against a separate limit and returns JSONL, which is the right tool for
  reading an entire catalogue.

## userErrors

Separate from throttling, and the same class of silent failure: mutations
return HTTP 200 with a populated `userErrors` array when validation fails.
Nothing is written. Walk the response and raise on any non-empty `userErrors`
so a rejected mutation cannot pass for a successful one.
