"""Same-name symbol as pkg.core.shared_name."""


def shared_name(a):
    """Same name exists in pkg/core.py -- must NOT be conflated."""
    return ("other", a)
