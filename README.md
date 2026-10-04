# antseed-price-sync

Keep [antseed](https://antseed.com) node pricing aligned with upstream cost.

## Why

Seller pricing drifts. Upstream raises a price, you forget to update, and you spend
a week selling at a loss before anyone notices.

This tool pulls the current upstream price and compares it against what your node
advertises, reporting — or optionally fixing — the drift.

## Design notes

Two things that are easy to get wrong:

1. **Model names are not unique across sources.** Match on a normalised key, never on
   the raw string, or you will silently match the wrong model.

2. **"Official price is 0" does not mean "upstream is free".** Some models are listed
   at zero by the provider and still cost money from every upstream reseller.
   Always take the cheapest *paid* upstream as your cost basis.

## Usage

    python price_sync.py --config seller.json            # report drift
    python price_sync.py --config seller.json --apply    # rewrite prices

## Status

Early.
