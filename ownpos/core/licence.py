"""Licences: the outer protection layer. It never touches sales, stock or accounting data.

How it works
- Aayush keeps a private Ed25519 signing key on his own PC, encrypted with his passphrase
  (tools/licence_tool.py). Only that key can make a licence.
- OwnPOS contains only the matching PUBLIC key (core/licence_key.py). It checks a licence's signature; a licence
  with one character changed, or made with any other key, is refused. The public key cannot make licences.
- A licence names the store (store ID = a random number created with the store's database), how many stations may
  sell, and optionally an expiry date. It is saved inside the store database, so it keeps working when a station
  PC is replaced or the server is restored from a backup.

What happens when
- Valid licence: nothing shows.
- Expires within 14 days: a reminder banner.
- Expired: 7 days of grace with a banner, then selling pauses. Reports, Excel/PDF export, backups, the time clock
  and Settings ALWAYS keep working, so the store's records are never held hostage.
- No licence / wrong store / bad signature: the activation screen shows the store ID to send to Aayush; the
  program can still be opened read-only for reports and backups.
- More stations than licensed: the extra station cannot start selling; the others are not affected.
- Developer copy (no public key configured): a banner says so; nothing is blocked.

Honest limit: this stops copying and key-guessing. Like every licence check in a program that runs on someone
else's computer, it does not stop a skilled programmer from changing the program itself."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date, timedelta

from .db import Database, now_str, today_str

BEGIN = "-----BEGIN OWNPOS LICENCE-----"
END = "-----END OWNPOS LICENCE-----"
WARN_DAYS = 14
GRACE_DAYS = 7


class LicenceError(Exception):
    pass


@dataclass
class LicenceStatus:
    state: str                  # ok / expiring / grace / expired / missing / invalid / dev
    message: str
    can_sell: bool
    payload: dict | None = None

    @property
    def banner(self) -> str:
        return "" if self.state == "ok" else self.message


def public_key_b64() -> str | None:
    try:
        from .licence_key import PUBLIC_KEY_B64
    except ImportError:
        return None
    return PUBLIC_KEY_B64 or None


def _b64d(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def pack(payload: dict, signature: bytes) -> str:
    body = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{BEGIN}\n{body}.{_b64e(signature)}\n{END}\n"


def unpack(text: str) -> tuple[dict, bytes, bytes]:
    """(payload, the exact signed bytes, signature)."""
    t = text.strip()
    if BEGIN in t and END in t:
        t = t.split(BEGIN, 1)[1].split(END, 1)[0]
    t = "".join(t.split())
    if "." not in t:
        raise LicenceError("This is not an OwnPOS licence")
    body, sig = t.split(".", 1)
    try:
        signed = _b64d(body)
        payload = json.loads(signed.decode("utf-8"))
        signature = _b64d(sig)
    except (ValueError, UnicodeDecodeError) as e:
        raise LicenceError("The licence text is damaged (copy it again, all lines)") from e
    return payload, signed, signature


def verify_text(text: str, public_b64: str | None = None) -> dict:
    """The licence's payload if its signature is valid, else LicenceError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    public_b64 = public_b64 or public_key_b64()
    if not public_b64:
        raise LicenceError("This copy of OwnPOS has no licence key built in")
    payload, signed, signature = unpack(text)
    try:
        Ed25519PublicKey.from_public_bytes(_b64d(public_b64)).verify(signature, signed)
    except InvalidSignature as e:
        raise LicenceError("The licence signature is not valid (it was changed, or not made by OwnPOS)") from e
    if payload.get("product") != "OwnPOS":
        raise LicenceError("This licence is for another product")
    return payload


def store_uid(db: Database) -> str:
    return db.val("SELECT value FROM meta WHERE key = 'store_uid'", default="") or ""


def active_licence_text(db: Database) -> str | None:
    return db.val("SELECT text FROM licences WHERE active = 1 ORDER BY id DESC LIMIT 1")


def install(db: Database, text: str, user_id: int | None) -> dict:
    """Check a licence and save it to the store database. Returns its payload."""
    payload = verify_text(text)
    if payload.get("store_uid") != store_uid(db):
        raise LicenceError(f"This licence is for another store (store ID {payload.get('store_uid')}); this store's ID "
                           f"is {store_uid(db)}")
    from .auth import audit
    with db.tx():
        db.exec("UPDATE licences SET active = 0 WHERE active = 1")
        db.exec("INSERT INTO licences(added_at, added_by, text) VALUES(?,?,?)", (now_str(), user_id, text.strip()))
        audit(db, user_id, "licence_install", "licence", payload.get("serial"),
              {"licensee": payload.get("licensee"), "stations": payload.get("stations"),
               "expires": payload.get("expires")})
    return payload


def status(db: Database, today: date | None = None) -> LicenceStatus:
    if not public_key_b64():
        return LicenceStatus("dev", "Developer copy: licensing is switched off.", True)
    text = active_licence_text(db)
    if not text and db.get_setting("is_demo"):
        # the training database made by the sample-store button: every receipt and screen says TRAINING
        return LicenceStatus("demo", "TRAINING DATABASE (sample store): nothing here is a real sale.", True)
    if not text:
        return LicenceStatus("missing", "OwnPOS is not activated for this store yet.", False)
    try:
        payload = verify_text(text)
    except LicenceError as e:
        return LicenceStatus("invalid", f"The saved licence is not valid: {e}", False)
    if payload.get("store_uid") != store_uid(db):
        return LicenceStatus("invalid", "The saved licence belongs to another store.", False, payload)
    today = today or date.fromisoformat(today_str())
    exp = payload.get("expires")
    if exp:
        exp_d = date.fromisoformat(exp)
        if today > exp_d + timedelta(days=GRACE_DAYS):
            return LicenceStatus("expired", f"The OwnPOS licence ended on {exp_d:%B %d, %Y}. Selling is paused; "
                                            "reports, exports and backups still work. Contact Aayush to renew.",
                                 False, payload)
        if today > exp_d:
            left = (exp_d + timedelta(days=GRACE_DAYS) - today).days
            return LicenceStatus("grace", f"The OwnPOS licence ended on {exp_d:%B %d, %Y}. Selling stops in "
                                          f"{left + 1} day(s) unless it is renewed.", True, payload)
        if (exp_d - today).days <= WARN_DAYS:
            return LicenceStatus("expiring", f"The OwnPOS licence ends on {exp_d:%B %d, %Y}.", True, payload)
    return LicenceStatus("ok", "", True, payload)


def allowed_stations(db: Database) -> int | None:
    """How many stations may sell (None = no limit: developer copy)."""
    st = status(db)
    if st.state in ("dev", "demo"):
        return None
    if st.payload is None:
        return 0
    return int(st.payload.get("stations") or 1)
