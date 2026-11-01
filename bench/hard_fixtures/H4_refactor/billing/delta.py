from .api import parse


def describe_delta(text):
    row = parse(text)
    return "delta: %s %s" % (row["name"], row["currency"])
