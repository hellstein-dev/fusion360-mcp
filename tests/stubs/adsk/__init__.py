"""Import-only stand-in for Fusion's ``adsk`` package, so add-in code loads outside Fusion.

Stubs cover imports only. Touching the API raises ``FusionUnavailableError``; behaviour
is tested with per-handler fakes and with smoke tests on a live Fusion (``-m fusion``).
"""


class FusionUnavailableError(RuntimeError):
    """Raised when test code reaches into the stubbed Fusion API."""


class Unavailable:
    """Placeholder for a Fusion object: any attribute access raises a clear error."""

    def __init__(self, what: str) -> None:
        self._what = what

    def __getattr__(self, name: str) -> object:
        raise FusionUnavailableError(
            f"{self._what}.{name}: adsk is stubbed in tests, a running Fusion 360 is required"
        )
