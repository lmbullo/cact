from .api import parse


def describe_india(text):
    row = parse(text)
    return "india: %s %s %s" % (row["name"], row["amount"], row["currency"])
