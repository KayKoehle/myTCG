"""Card behavior modules, one per finished deck.

Importing this package populates the effect registries in `engine.effects`.
Add a new module here when a new deck's card effects are implemented.
"""
from . import flood, gilgamesh, inanna, odin, osiris, troy  # noqa: F401
