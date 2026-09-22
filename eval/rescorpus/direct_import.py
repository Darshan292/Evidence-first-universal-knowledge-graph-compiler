"""Case: direct import + qualified call."""
import pkg.core


def run(x):
    return pkg.core.helper(x)
