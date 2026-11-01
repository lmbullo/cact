from .api import parse


def describe_lima(text):
    row = parse(text)
    return "lima: %s %s %s" % (row["name"], row["amount"], row["currency"])
