ZONE_SURCHARGE = {"north": 0, "south": 150}
BASE_CHARGE = 400


def shipping(weight_g, zone):
    if zone not in ZONE_SURCHARGE:
        raise ValueError("unknown zone: %r" % (zone,))
    return BASE_CHARGE + ZONE_SURCHARGE[zone] + max(0, int(weight_g) - 500) // 100
