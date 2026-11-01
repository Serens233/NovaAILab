"""Independent, reproducible benchmarks for Nova local Ollama models.

The package deliberately does not import ``nova`` runtime modules. It talks to
Ollama over its loopback HTTP API and records measured resource usage, so a
benchmark result cannot change production routing, GPU state, or the database.
"""

from __future__ import annotations

__version__ = "0.1.0"
SCHEMA_VERSION = "1.0"
