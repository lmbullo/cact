from .api import parse


def describe_november(text):
    row = parse(text)
    return "november: %s %s %s" % (row["name"], row["amount"], row["currency"])
