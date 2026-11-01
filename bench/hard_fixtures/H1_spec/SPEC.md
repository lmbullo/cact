# textkit contract

`slugify(text)` -> lowercase; every run of characters outside [a-z0-9] becomes one `-`;
leading and trailing `-` are removed. Input with no alphanumerics -> `""`.

`truncate(text, limit, suffix="...")` -> `text` unchanged when `len(text) <= limit`.
Otherwise the result ends with `suffix` and has length exactly `limit` when
`limit >= len(suffix)`. When `limit < len(suffix)`, return `suffix[:limit]`.

`parse_duration(text)` -> integer seconds. Accepts one or more `<n><unit>` components where
unit is `h`, `m` or `s`, e.g. `45s`, `2m`, `1h30m`, `2h15m10s`. Each component may appear at
most once and the units must descend h, m, s when combined. Empty or malformed input raises
`ValueError`.

`chunk(items, size)` -> a list of lists, in order, each of length `size` except the last.
Empty input -> `[]`. `size <= 0` raises `ValueError`.

`word_count(text)` -> the number of whitespace-separated tokens that contain at least one
alphanumeric character. Punctuation-only tokens are not words.
