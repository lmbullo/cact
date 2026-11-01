from .api import parse


def describe_charlie(text):
    row = parse(text)
    return "charlie: %s %s %s" % (row["name"], row["amount"], row["currency"])
