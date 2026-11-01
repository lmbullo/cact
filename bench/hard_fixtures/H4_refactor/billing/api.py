def parse(text):
    parts = text.split(",")
    fields = [part.strip() for part in parts]
    while len(fields) < 3:
        fields.append("")
    return {"name": fields[0], "amount": fields[1], "currency": fields[2]}
