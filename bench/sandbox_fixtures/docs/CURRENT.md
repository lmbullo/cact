# Current pricing contract

This file is the source of truth for this checkout, not a model's training data.

- Base charge: 400 cents.
- Free shipping threshold: 4500 cents (the total is the order total, before shipping).
- Zone surcharge, in cents: north 0, south 150, west 275.
- `quote(total_cents, zone)` returns 0 when `total_cents >= 4500`; otherwise it returns
  the base charge plus the zone surcharge. An unknown zone raises `ValueError`.
- Keep the existing `shipping(weight_g, zone)` behavior unchanged.
