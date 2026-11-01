from .api import parse


def describe_foxtrot(text):
    row = parse(text)
    return "foxtrot: %s %s" % (row["name"], row["currency"])
