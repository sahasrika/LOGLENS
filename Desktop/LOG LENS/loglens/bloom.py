"""
Bloom Filter for LogLens.

Guarantees
----------
  might_contain() == False  →  item is DEFINITELY NOT present
  might_contain() == True   →  item is POSSIBLY present (false positives possible)

This is NOT the source of truth for error records.  Its role is to provide
a fast pre-check before the more expensive exact storage lookup.

Implementation
--------------
  - MurmurHash3 (mmh3) with k independent seeds via double-hashing.
  - bitarray for memory-efficient bit storage.
  - Optimal m (bit-array size) and k (hash functions) derived from
    the desired capacity n and error rate p:

        m = ⌈ −n·ln(p) / (ln 2)² ⌉
        k = round( (m/n)·ln 2 )

References
----------
  Bloom, B.H. (1970). Space/time trade-offs in hash coding with allowable errors.
  Communications of the ACM, 13(7), 422–426.
"""

from __future__ import annotations

import math

import mmh3
from bitarray import bitarray


class BloomFilter:
    """
    Space-efficient probabilistic set-membership data structure.

    Parameters
    ----------
    capacity:
        Expected maximum number of items before the false-positive rate
        exceeds *error_rate*.  Must be >= 1.
    error_rate:
        Desired maximum false-positive probability at *capacity* items.
        Must be in (0, 1).
    """

    def __init__(self, capacity: int = 100_000, error_rate: float = 0.01) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        if not (0 < error_rate < 1):
            raise ValueError(f"error_rate must be in (0, 1), got {error_rate}")

        self.capacity   = capacity
        self.error_rate = error_rate
        self._m: int    = self._optimal_m(capacity, error_rate)
        self._k: int    = self._optimal_k(self._m, capacity)
        self._bits: bitarray = bitarray(self._m)
        self._bits.setall(0)
        self._count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, item: str) -> None:
        """Insert *item* into the filter."""
        for idx in self._indices(item):
            self._bits[idx] = 1
        self._count += 1

    def might_contain(self, item: str) -> bool:
        """
        Return False (definitely absent) or True (possibly present).

        A True result does not guarantee the item was inserted.
        A False result guarantees the item was NOT inserted.
        """
        return all(self._bits[idx] for idx in self._indices(item))

    @property
    def count(self) -> int:
        """Number of items inserted (may exceed capacity)."""
        return self._count

    @property
    def bit_size(self) -> int:
        """Total bits in the underlying array."""
        return self._m

    @property
    def num_hashes(self) -> int:
        """Number of hash functions k."""
        return self._k

    def __repr__(self) -> str:
        return (
            f"BloomFilter(capacity={self.capacity}, error_rate={self.error_rate}, "
            f"m={self._m}, k={self._k}, count={self._count})"
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _indices(self, item: str) -> list[int]:
        """
        Return k bit indices for *item* using double-hashing:
            index_i = (h1 + i·h2) mod m
        Two MurmurHash3 seeds produce h1 and h2.
        """
        h1 = mmh3.hash(item, seed=0) % self._m
        h2 = mmh3.hash(item, seed=1) % self._m or 1   # ensure stride ≥ 1
        return [(h1 + i * h2) % self._m for i in range(self._k)]

    @staticmethod
    def _optimal_m(n: int, p: float) -> int:
        return max(1, math.ceil(-(n * math.log(p)) / (math.log(2) ** 2)))

    @staticmethod
    def _optimal_k(m: int, n: int) -> int:
        return max(1, round((m / n) * math.log(2)))
