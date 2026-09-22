"""Core module. Defines helper and a same-named symbol as pkg.other."""
import os


def helper(x):
    """Defined here; re-exported by pkg/__init__.py."""
    return x + 1


def shared_name(a):
    """Same name exists in pkg/other.py -- must NOT be conflated."""
    return ("core", a)


def uses_stdlib(p):
    return os.path.join(p, "x")
