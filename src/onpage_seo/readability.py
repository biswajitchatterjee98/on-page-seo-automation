"""Readability helpers (no LLM required)."""

from __future__ import annotations

import re


def flesch_reading_ease(text: str) -> float:
    sentences = max(1, len(re.findall(r"[.!?]+", text)) or 1)
    words = re.findall(r"[A-Za-z0-9']+", text)
    if not words:
        return 0.0
    syllable_count = 0
    for word in words:
        syllable_count += _syllables(word)
    # Flesch Reading Ease
    return round(
        206.835
        - 1.015 * (len(words) / sentences)
        - 84.6 * (syllable_count / len(words)),
        2,
    )


def _syllables(word: str) -> int:
    word = word.lower()
    vowels = "aeiouy"
    count = 0
    prev_vowel = False
    for char in word:
        is_vowel = char in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)
