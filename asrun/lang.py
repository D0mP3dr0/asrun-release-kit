"""A deliberately simple test for text that is probably not English."""
import re
import unicodedata

WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def has_non_ascii_letter(s):
    return any(ord(c) > 127 and unicodedata.category(c).startswith("L") for c in s)


ENGLISH_FUNCTION_WORDS = {
    "the", "of", "to", "and", "in", "is", "with", "for", "on", "by", "from", "at", "this", "that", "it", "are",
    "was", "not", "or", "an", "be", "has", "have", "if", "no", "all", "per", "into", "than", "then", "each", "its",
    "use", "using", "when", "which", "will", "can", "must", "should", "only", "after", "before", "over",
}


def looks_non_english(s, foreign_words, min_hits=2, broad=False):
    """Non-ASCII letters, or min_hits words of the foreign list. With broad=True (used to list
    literals for a translator, where a false alarm costs nothing) a text of four or more words
    with no common English function word also counts."""
    if has_non_ascii_letter(s):
        return True
    found = [w.lower() for w in WORD.findall(s)]
    words = {w.lower() for w in foreign_words}
    if sum(1 for w in found if w in words) >= min_hits:
        return True
    return broad and len(found) >= 4 and not any(w in ENGLISH_FUNCTION_WORDS for w in found)
