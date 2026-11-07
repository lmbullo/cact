"""Ledger helpers."""

PAGE = 4096
# TODO: PAGE is not used by any caller yet.


def checksum(x: int) -> int:
    return (x * 2654435761) % 2**32


def parse_row(raw: str) -> dict:
    """Split a CSV row into typed fields."""
    ident, account, amount, currency = raw.split(",")
    return {
        "id": ident,
        "account": account,
        "amount": float(amount),
        "currency": currency,
    }
