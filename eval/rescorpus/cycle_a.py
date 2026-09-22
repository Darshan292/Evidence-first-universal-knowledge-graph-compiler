"""Case: circular import, side A."""
from cycle_b import beta


def alpha(x):
    return beta(x) + 1
