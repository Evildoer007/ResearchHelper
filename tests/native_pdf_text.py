"""Compare extracted native text without rewriting source or rendered PDFs."""
import unicodedata


def pdf_text_key(value):
    # Only known glyph-equivalent CJK radicals. Never substitute numbers,
    # units, qualifiers, punctuation or a missing sentence.
    value = unicodedata.normalize('NFKC', value)
    value = value.translate(str.maketrans({
        '⻅': '见', '⻓': '长', '⻛': '风', '⻔': '门',
        '⻝': '食', '⻰': '龙', '⺟': '母',
    }))
    return ''.join(value.split())
