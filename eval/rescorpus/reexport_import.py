"""Case: import of a re-exported symbol (defined in pkg.core, exposed by pkg)."""
from pkg import helper


def run(x):
    return helper(x)
