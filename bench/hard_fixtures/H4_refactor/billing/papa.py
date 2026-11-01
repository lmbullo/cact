from .api import parse


def describe_papa(text):
    row = parse(text)
    return "papa: %s %s %s" % (row["name"], row["amount"], row["currency"])
