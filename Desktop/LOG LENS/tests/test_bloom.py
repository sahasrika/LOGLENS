"""
Tests for loglens.bloom.BloomFilter.

Coverage
--------
Construction  — valid params; invalid capacity/error_rate raise ValueError
Sizing        — m and k are positive; larger capacity → larger m; tighter rate → larger m
Insertion     — inserted items are always found (no false negatives)
Unseen items  — empty filter returns False; random unseen items mostly False
False-positive rate — stays within 3× configured error_rate at capacity
Count         — increments on each add()
Repr          — contains capacity and count
"""

import random
import string
import pytest

from loglens.bloom import BloomFilter


def _rfp(n: int = 16) -> str:
    return "".join(random.choices("0123456789abcdef", k=n))


# ===========================================================================
# Construction
# ===========================================================================

class TestConstruction:
    def test_defaults(self):
        bf = BloomFilter()
        assert bf.capacity == 100_000
        assert bf.error_rate == 0.01

    def test_custom_params(self):
        bf = BloomFilter(capacity=1000, error_rate=0.05)
        assert bf.capacity == 1000

    def test_zero_capacity_raises(self):
        with pytest.raises(ValueError, match="capacity"):
            BloomFilter(capacity=0)

    def test_negative_capacity_raises(self):
        with pytest.raises(ValueError, match="capacity"):
            BloomFilter(capacity=-1)

    def test_zero_error_rate_raises(self):
        with pytest.raises(ValueError, match="error_rate"):
            BloomFilter(capacity=100, error_rate=0.0)

    def test_error_rate_one_raises(self):
        with pytest.raises(ValueError, match="error_rate"):
            BloomFilter(capacity=100, error_rate=1.0)

    def test_error_rate_above_one_raises(self):
        with pytest.raises(ValueError, match="error_rate"):
            BloomFilter(capacity=100, error_rate=1.5)


# ===========================================================================
# Sizing
# ===========================================================================

class TestSizing:
    def test_bit_size_positive(self):
        assert BloomFilter(capacity=1000, error_rate=0.01).bit_size > 0

    def test_num_hashes_positive(self):
        assert BloomFilter(capacity=1000, error_rate=0.01).num_hashes >= 1

    def test_larger_capacity_larger_bit_array(self):
        small = BloomFilter(capacity=100, error_rate=0.01)
        large = BloomFilter(capacity=100_000, error_rate=0.01)
        assert large.bit_size > small.bit_size

    def test_tighter_error_rate_larger_bit_array(self):
        loose = BloomFilter(capacity=1000, error_rate=0.1)
        tight = BloomFilter(capacity=1000, error_rate=0.001)
        assert tight.bit_size > loose.bit_size


# ===========================================================================
# Insertion and membership — no false negatives
# ===========================================================================

class TestInsertion:
    def test_single_item_found(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        bf.add("abc123def456")
        assert bf.might_contain("abc123def456") is True

    def test_many_items_all_found(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        items = [_rfp() for _ in range(100)]
        for item in items:
            bf.add(item)
        for item in items:
            assert bf.might_contain(item) is True

    def test_no_false_negatives(self):
        """Inserted items must ALWAYS be found."""
        bf = BloomFilter(capacity=10_000, error_rate=0.01)
        items = [_rfp(20) for _ in range(1000)]
        for item in items:
            bf.add(item)
        for item in items:
            assert bf.might_contain(item) is True, f"False negative: {item}"

    def test_count_increments(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        assert bf.count == 0
        bf.add("x")
        assert bf.count == 1
        bf.add("y")
        assert bf.count == 2


# ===========================================================================
# Unseen items
# ===========================================================================

class TestUnseenItems:
    def test_empty_filter_returns_false(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        assert bf.might_contain("neveradded") is False

    def test_unseen_items_mostly_false(self):
        bf = BloomFilter(capacity=10_000, error_rate=0.01)
        bf.add("only_one_item")
        fp_count = sum(1 for _ in range(1000) if bf.might_contain(_rfp(20)))
        # With error_rate=0.01 and 1 item inserted, well under 5% false positives expected
        assert fp_count < 50, f"Too many false positives from near-empty filter: {fp_count}"


# ===========================================================================
# False-positive rate bound
# ===========================================================================

class TestFalsePositiveRate:
    def test_rate_within_bound_at_capacity(self):
        """
        At exactly *capacity* insertions, the observed FP rate must be
        ≤ 3× the configured error_rate (statistical tolerance at small N).
        """
        random.seed(42)
        capacity, error_rate = 1000, 0.05
        bf = BloomFilter(capacity=capacity, error_rate=error_rate)

        inserted: set[str] = set()
        while len(inserted) < capacity:
            inserted.add(_rfp(20))
        for item in inserted:
            bf.add(item)

        unseen: set[str] = set()
        while len(unseen) < capacity:
            c = _rfp(20)
            if c not in inserted:
                unseen.add(c)

        fp_rate = sum(1 for item in unseen if bf.might_contain(item)) / capacity
        assert fp_rate <= error_rate * 3, (
            f"FP rate {fp_rate:.3f} exceeds 3× error_rate ({error_rate * 3:.3f})"
        )


# ===========================================================================
# Repr
# ===========================================================================

class TestRepr:
    def test_repr_has_capacity(self):
        assert "500" in repr(BloomFilter(capacity=500, error_rate=0.02))

    def test_repr_has_count(self):
        bf = BloomFilter(capacity=500, error_rate=0.02)
        bf.add("x")
        assert "count=1" in repr(bf)
