from .api import parse


def describe_hotel(text):
    row = parse(text)
    return "hotel: %s %s" % (row["name"], row["currency"])
