import re


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def truncate(text, limit, suffix="..."):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + suffix


def parse_duration(text):
    match = re.fullmatch(r"(\d+)([hms])", str(text).strip())
    if not match:
        raise ValueError("bad duration: %r" % (text,))
    scale = {"h": 3600, "m": 60, "s": 1}[match.group(2)]
    return int(match.group(1)) * scale


def chunk(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def word_count(text):
    return len(str(text).split())
