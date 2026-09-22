"""Case: aliased import + qualified call through the alias."""
import pkg.core as c
from pkg.other import shared_name as other_shared


def run(x):
    return c.helper(x)


def run_alias(a):
    return other_shared(a)
