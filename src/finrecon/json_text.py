"""BOM-tolerant decoding for JSON *text* boundaries.

Windows tooling (Notepad, PowerShell's ``Out-File``/``Set-Content``,
Excel's exports) routinely writes UTF-8 with a byte-order mark. A BOM is
legal in the byte stream but is *not* legal JSON grammar, so
``json.loads`` on a strictly ``utf-8``-decoded string fails with

    Unexpected UTF-8 BOM (decode using utf-8-sig)

which is a decoding accident, not a malformed document. Every JSON text
boundary in this codebase therefore decodes with ``utf-8-sig``: the codec
strips a single leading BOM when present and is byte-for-byte identical to
``utf-8`` when it is absent, so ordinary UTF-8 JSON behaves exactly as
before.

Deliberately narrow: this is *not* encoding auto-detection. No other
encoding is attempted, nothing is sniffed, and this helper is only for
JSON text. Bank CSV bytes keep going through the profile's declared
``encoding`` (see :mod:`finrecon.adapters.bank.csv_parser`) -- a bank
source's encoding is a reviewable per-profile declaration, never a guess.
"""

from __future__ import annotations

JSON_TEXT_ENCODING = "utf-8-sig"


def decode_json_bytes(raw: bytes) -> str:
    """Decode JSON source bytes as UTF-8, tolerating a leading BOM.

    Raises ``UnicodeDecodeError`` for bytes that are not valid UTF-8 at
    all, exactly as a plain ``utf-8`` decode would -- callers keep their
    existing error handling.
    """
    return raw.decode(JSON_TEXT_ENCODING)


__all__ = ["JSON_TEXT_ENCODING", "decode_json_bytes"]
