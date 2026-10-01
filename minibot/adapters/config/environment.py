"""Compatibility alias for shared configuration expansion helpers."""

import sys

from minibot.config import environment

sys.modules[__name__] = environment
