from .api import parse


def describe_bravo(text):
    row = parse(text)
    return "bravo: %s %s %s" % (row["name"], row["amount"], row["currency"])
