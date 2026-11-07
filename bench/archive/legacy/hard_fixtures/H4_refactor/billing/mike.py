from .api import parse


def describe_mike(text):
    row = parse(text)
    return "mike: %s %s %s" % (row["name"], row["amount"], row["currency"])
