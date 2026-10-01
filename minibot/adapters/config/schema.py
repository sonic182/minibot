"""Compatibility alias for the shared configuration schema."""

import sys

from minibot.config import schema

sys.modules[__name__] = schema
