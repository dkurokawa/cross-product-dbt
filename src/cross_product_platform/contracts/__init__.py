"""Data contracts (pandera) for every landed dataset.

A contract is checked per file at landing time. A file that violates its contract is moved
to ``quarantine/`` with a JSON reason next to it; rows are never dropped silently.
"""

from __future__ import annotations

from .registry import CONTRACTS, Contract, get_contract
from .validate import ContractViolation, validate_frame

__all__ = ["CONTRACTS", "Contract", "ContractViolation", "get_contract", "validate_frame"]
