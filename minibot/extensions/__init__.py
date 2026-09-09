"""Bundled extensions: thin ``register(mb)`` entry points over adapter code.

Loaded ahead of user modules by ``minibot.app.extensions._BUNDLED_MODULES``. Each module
decides for itself whether it is active, from ``mb.settings`` and ``mb.entrypoint``.
"""
