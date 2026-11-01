# billing API

`billing.api.parse_row(text, *, strict=False)` splits one row on commas into
`{"name": str, "amount": str, "currency": str}`.

- Non-strict: missing trailing fields become `""`.
- Strict: fewer than three fields raises `ValueError`.
- The old name `parse` no longer exists.

Every module in `billing/` must call `parse_row`.
