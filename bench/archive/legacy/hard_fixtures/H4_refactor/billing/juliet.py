from .api import parse


def describe_juliet(text):
    row = parse(text)
    return "juliet: %s %s %s" % (row["name"], row["amount"], row["currency"])
