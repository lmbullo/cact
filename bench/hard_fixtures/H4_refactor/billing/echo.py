from .api import parse


def describe_echo(text):
    row = parse(text)
    return "echo: %s %s %s" % (row["name"], row["amount"], row["currency"])
