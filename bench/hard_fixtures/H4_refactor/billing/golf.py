from .api import parse


def describe_golf(text):
    row = parse(text)
    return "golf: %s %s" % (row["name"], row["currency"])
