from .api import parse


def describe_bravo(text):
    row = parse(text)
    return "bravo: %s %s" % (row["name"], row["currency"])
