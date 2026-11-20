"""Utilities for encoding fixed-length dispatch decision sequences."""

from collections.abc import Iterable


def encode_decisions(decisions: Iterable[int], base: int) -> int:
    """Encode action digits as one nonnegative integer in the given base.

    The first decision is the least significant digit. The sequence length is
    intentionally not stored in the integer, so callers must retain it when
    decoding sequences that may contain leading zero digits.

    >>> encode_decisions([2, 0, 4], base=5)
    102
    """
    if not isinstance(base, int) or isinstance(base, bool) or base < 2:
        raise ValueError("base must be an integer of at least 2")

    encoded = 0
    place = 1
    for digit in decisions:
        if not isinstance(digit, int) or isinstance(digit, bool):
            raise TypeError("each decision must be an integer action code")
        if not 0 <= digit < base:
            raise ValueError(f"decision codes must be in the range [0, {base})")
        encoded += digit * place
        place *= base
    return encoded


def decode_decisions(encoded: int, base: int, length: int) -> list[int]:
    """Decode a fixed-length action sequence from its base-``base`` integer.

    ``length`` preserves leading zero action codes and must match the length
    used to form the original sequence. Values outside the representable range
    ``[0, base**length)`` are rejected rather than silently truncated.

    >>> decode_decisions(102, base=5, length=3)
    [2, 0, 4]
    """
    if not isinstance(base, int) or isinstance(base, bool) or base < 2:
        raise ValueError("base must be an integer of at least 2")
    if not isinstance(length, int) or isinstance(length, bool) or length < 0:
        raise ValueError("length must be a nonnegative integer")
    if not isinstance(encoded, int) or isinstance(encoded, bool):
        raise TypeError("encoded value must be an integer")
    if encoded < 0 or encoded >= base**length:
        raise ValueError("encoded value does not fit the requested base and length")

    decisions = []
    for _ in range(length):
        encoded, digit = divmod(encoded, base)
        decisions.append(digit)
    return decisions


__all__ = ["decode_decisions", "encode_decisions"]
