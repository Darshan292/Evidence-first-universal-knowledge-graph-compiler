"""Case: an imported name shadowed by a local definition."""
from pkg.core import helper


def helper(x):
    """Shadows the imported helper. Calls below bind to THIS one."""
    return x - 1


def run(x):
    return helper(x)
