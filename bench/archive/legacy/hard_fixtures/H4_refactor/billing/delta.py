from .api import parse


def describe_delta(text):
    row = parse(text)
    return "delta: %s %s %s" % (row["name"], row["amount"], row["currency"])
