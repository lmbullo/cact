def checksum(order_id):
    h = 0
    for ch in str(order_id):
        h = (h * 31 + ord(ch)) % 1000003
    return h
