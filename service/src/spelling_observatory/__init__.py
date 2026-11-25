"""Personal Spelling Observatory: local longitudinal spelling-error analysis engine."""

__version__ = "0.1.0"

# Bump whenever tokenisation, eligibility or classification *rules* change in a way that
# could alter outputs. It is part of the classifier fingerprint (see classify/version.py).
RULES_VERSION = "rules-2026.11.1"

# Bump when the SQLite schema changes.
SCHEMA_VERSION = 1
