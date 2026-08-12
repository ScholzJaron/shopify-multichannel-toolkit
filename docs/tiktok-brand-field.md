# The TikTok Shop Brand field, and the three problems it causes

If products in your TikTok Shop catalogue are deactivating themselves, failing
bulk activation, or refusing to sync from a connected storefront, check the
Brand field before you investigate anything else.

## The symptoms

A missing Brand value produces three distinct failures, on three different
screens, with no shared error text:

| Where you see it | What it says |
| --- | --- |
| Manage Products, status column | `Deactivated`, no reason given |
| Bulk activation | `System error`, no detail |
| Storefront sync log | `Sync failed`, no field named |

None of these mentions Brand. It is easy to open three support tickets, or to
treat them as a platform outage, when there is one root cause.

## Why it happens

Brand is required on most TikTok Shop categories. Products created through a
sync integration frequently arrive without it, because the source platform has
no equivalent field. The listing is accepted at creation and then fails
validation later, at activation time.

## The fix

Brand cannot reliably be set in bulk through the product edit UI. Use a
bulk-edit template round trip:

1. Seller Centre, Manage Products, Bulk edit, Export.
   Select the affected products and export the template.
2. Fill the Brand column with an authorised brand on your account.
   `src/tiktok_brand_repair.py repair` does this and splits the output.
3. Upload the corrected files, one at a time.
   Wait for each to finish processing before starting the next.

Roughly 100 rows per file uploads reliably. Larger files are rejected without a
useful message. If you have thousands of products this means a lot of files;
the script names them in order so you can track where you are.

After the upload completes, previously deactivated products return to Live
without any further action. Sync failures stop on the next cycle.

## The second failure: brand contamination

If your listing pipeline derives Brand from the product or design name, any
word resembling a trademark will be mapped to that trademark. A shirt called
"Champion of the Week" acquires the brand CHAMPION. A design mentioning a
vehicle acquires that manufacturer.

The consequence is worse than a wrong value. TikTok blocks every subsequent
edit to that listing with `Unauthorised brand`, and the listing can end up
locked, unable to be corrected through the same bulk mechanism that would
otherwise fix it.

Audit for this on a schedule:

```bash
python src/tiktok_brand_repair.py audit export.xlsx --authorised "Your Brand"
```

Anything not on your authorised list gets flagged. Correct it through the same
bulk upload before the listings lock.

## Related dead ends

Two things that look like solutions and are not:

- **The Map Now screen.** Listings created directly on TikTok, with no
  counterpart on your connected storefront, appear here as unmappable SKUs.
  They cannot be mapped, because there is nothing to map them to. Do not click
  `Complete map` -- it does not create the missing products.
- **Appealing policy rejections.** Category policy blocks are a separate
  mechanism from field validation. They prevent edits, not sales, and the
  appeal window expires. Brand repair will not clear them.

## Prevention

Set Brand explicitly at creation time rather than letting it be derived. If your
pipeline builds listing payloads, treat Brand as required in your own schema so
a missing value fails locally instead of silently downstream.
