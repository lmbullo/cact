from .api import parse


def describe_foxtrot(text):
    row = parse(text)
    return "foxtrot: %s %s %s" % (row["name"], row["amount"], row["currency"])
