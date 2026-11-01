from .api import parse


def describe_kilo(text):
    row = parse(text)
    return "kilo: %s %s %s" % (row["name"], row["amount"], row["currency"])
