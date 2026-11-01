# ledger invariants

`tax.with_tax(cents, rate_bp)` -> `cents` plus tax, where `rate_bp` is basis points
(10000 bp = 100%). The tax is rounded half up: `(cents * rate_bp + 5000) // 10000`.

`dates.days_between(a, b)` -> the absolute number of calendar days between two `date`
objects. `days_between(d, d) == 0`.

`money.split(cents, n)` -> a list of `n` ints whose sum is `cents`; the first `cents % n`
entries are one cent larger than the rest. `n <= 0` raises `ValueError`.

`text.normalize(s)` -> lowercase; every run of characters outside `[a-z0-9]` becomes one
space; leading and trailing spaces are removed.

`stats.median(xs)` -> the middle value when the length is odd, the mean of the two middle
values (a float) when even. Empty input raises `ValueError`.
