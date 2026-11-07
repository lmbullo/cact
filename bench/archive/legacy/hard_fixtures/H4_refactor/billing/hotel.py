from .api import parse


def describe_hotel(text):
    row = parse(text)
    return "hotel: %s %s %s" % (row["name"], row["amount"], row["currency"])
