"""Case: circular import, side B."""


def beta(x):
    return x * 3


def call_alpha(x):
    from cycle_a import alpha
    return alpha(x)
