# billing API

`billing.api.parse_row(text, *, strict=False)` splits one row on commas into
`{"name": str, "cents": str, "currency": str}`.

- Non-strict: missing trailing fields become `""`.
- Strict: fewer than three fields raises `ValueError`.
- The old name `parse` no longer exists, and the old `amount` field is now `cents`.

Every module in `billing/` must call `parse_row` and read the `cents` field.
