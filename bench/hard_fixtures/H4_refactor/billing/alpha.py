from .api import parse


def describe_alpha(text):
    row = parse(text)
    return "alpha: %s %s" % (row["name"], row["currency"])
