"""Case: from x import y, plus a local function with the same call shape."""
from pkg.core import helper, shared_name


def local_helper(x):
    """Local function -- calls to this are intra-module and deterministic."""
    return x * 2


def run(x):
    a = helper(x)
    b = local_helper(x)
    c = shared_name(x)
    return a, b, c
