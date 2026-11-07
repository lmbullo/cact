from .api import parse


def describe_oscar(text):
    row = parse(text)
    return "oscar: %s %s %s" % (row["name"], row["amount"], row["currency"])
