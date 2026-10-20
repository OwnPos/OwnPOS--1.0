"""Barcodes: UPC/EAN codes as scanned at the register, and Code 128 for printed shelf labels.

Scanners and spreadsheets do not always agree on how a UPC is written. The same can of beer may be
'012345678905' (UPC-A, 12 digits), '0012345678905' (EAN-13 with a leading 0), '01234567890' (no check
digit, as some systems store it) or '12345678905' (Excel removed the leading zero). lookup_keys() turns a
scanned code into every form it could be stored as, without leading zeros, so the register finds the item
whichever way it was entered. Only exact digit-for-digit matches are used: a code is never "guessed"."""
from __future__ import annotations

# Code 128 bar/space module widths for symbol values 0..105, then STOP.
PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232",
]
STOP = "2331112"
START_B, START_C = 104, 105
CODE_B = 100

def code128_values(text: str) -> list[int]:
    """Symbol values (start, data, checksum) for text. Uses code set C for even-length digit strings."""
    if not text:
        raise ValueError("Nothing to encode")
    if text.isdigit() and len(text) >= 4:
        # code set C packs two digits per symbol; an odd last digit switches to set B (value 100 in set C)
        even = len(text) - len(text) % 2
        vals = [START_C] + [int(text[i:i + 2]) for i in range(0, even, 2)]
        if len(text) % 2:
            vals += [CODE_B, ord(text[-1]) - 32]
    else:
        vals = [START_B]
        for ch in text:
            o = ord(ch)
            if o < 32 or o > 126:
                raise ValueError(f"Character {ch!r} cannot go in a barcode; use letters, digits and symbols only")
            vals.append(o - 32)
    check = (vals[0] + sum(i * v for i, v in enumerate(vals[1:], start=1))) % 103
    return vals + [check]


def code128_modules(text: str) -> list[int]:
    """Alternating bar/space widths (in modules), starting with a bar, ending with the stop bar."""
    out = []
    for v in code128_values(text):
        out += [int(c) for c in PATTERNS[v]]
    out += [int(c) for c in STOP]
    return out


def upc_check_digit(body: str) -> int:
    """GS1 check digit for the digits before it (works for UPC-A, EAN-8, EAN-13, GTIN-14 bodies)."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def has_valid_check_digit(code: str) -> bool:
    return code.isdigit() and len(code) in (8, 12, 13, 14) and upc_check_digit(code[:-1]) == int(code[-1])


def normalize_code(text) -> str:
    """Clean a code typed, scanned or read from Excel: trims spaces; turns 1.23457E+11 / 123456789012.0
    back into digits when that is exact."""
    if text is None:
        return ""
    if isinstance(text, bool):
        return ""
    if isinstance(text, int):
        return str(text)
    if isinstance(text, float):
        return str(int(text)) if text == int(text) and abs(text) < 1e15 else str(text)
    s = str(text).strip()
    if s.startswith("'") and s[1:].strip().isdigit():     # Excel's "keep as text" mark typed into the cell
        s = s[1:].strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def lookup_keys(code: str) -> list[str]:
    """Every stored form a scanned numeric code could match, as keys without leading zeros
    (the database keeps items.upc_key / item_units.barcode_key the same way)."""
    code = normalize_code(code)
    if not code.isdigit():
        return []
    keys = []

    def add(c: str):
        k = c.lstrip("0")
        if k and k not in keys:
            keys.append(k)
    add(code)
    if has_valid_check_digit(code):
        add(code[:-1])                    # stored without its check digit
    elif len(code) in (7, 11, 12, 13):
        add(code + str(upc_check_digit(code)))   # scanned/stored without its check digit
    return keys
