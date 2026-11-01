def with_tax(cents, rate_bp):
    return cents + cents * rate_bp // 100
