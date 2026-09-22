"""Case: unresolved third-party symbol -- the package is not in the corpus."""
import requests
from nonexistent_pkg.sub import mystery


def run(url):
    resp = requests.get(url)
    return mystery(resp)
