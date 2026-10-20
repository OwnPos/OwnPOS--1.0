"""OwnPOS licence tool - FOR AAYUSH ONLY. Never give this file or your signing key to a store.

    python tools/licence_tool.py init      make your signing key (once) and build its public half into OwnPOS
    python tools/licence_tool.py issue     make a licence for a store
    python tools/licence_tool.py verify FILE   check a licence file

The signing key file is encrypted with your passphrase. It is saved OUTSIDE the project folder (by default in
your Windows user folder) so it is never zipped, uploaded or built into the program. Keep a copy on a USB stick
in a safe place: if it is lost, you cannot make new licences for stores that already run OwnPOS (you would have
to ship a new build with a new key)."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from ownpos.core.licence import _b64e, pack, verify_text  # noqa: E402

DEFAULT_KEY = Path.home() / "OwnPOS-signing-key.pem"
KEY_MODULE = ROOT / "ownpos" / "core" / "licence_key.py"


def _ask_new_passphrase() -> bytes:
    while True:
        a = getpass.getpass("Choose a passphrase for the signing key (12+ characters): ")
        if len(a) < 12:
            print("Too short.")
            continue
        if a != getpass.getpass("Type it again: "):
            print("They do not match.")
            continue
        return a.encode("utf-8")


def _load_key(path: Path) -> Ed25519PrivateKey:
    data = path.read_bytes()
    pw = getpass.getpass(f"Passphrase for {path.name}: ").encode("utf-8")
    try:
        key = serialization.load_pem_private_key(data, password=pw)
    except (ValueError, TypeError):
        sys.exit("Wrong passphrase (or not an OwnPOS signing key).")
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("That file is not an Ed25519 signing key.")
    return key


def _write_public(key: Ed25519PrivateKey) -> str:
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    b64 = _b64e(pub)
    KEY_MODULE.write_text(
        '"""The public half of Aayush\'s licence signing key. Written by `python tools/licence_tool.py init`.\n\n'
        'Empty = developer copy (licensing off). A release build refuses to build while this is empty."""\n'
        f'PUBLIC_KEY_B64 = "{b64}"\n', encoding="utf-8")
    return b64


def cmd_init(args) -> None:
    path = Path(args.key or DEFAULT_KEY)
    if path.exists():
        print(f"{path} already exists. Using it to (re)write the public key into OwnPOS.")
        key = _load_key(path)
    else:
        if ROOT in path.resolve().parents:
            sys.exit("Keep the signing key outside the OwnPOS project folder.")
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.BestAvailableEncryption(_ask_new_passphrase()))
        path.write_bytes(pem)
        print(f"Signing key saved to {path}. Copy it to a USB stick and keep it safe.")
    b64 = _write_public(key)
    print(f"Public key written to {KEY_MODULE.relative_to(ROOT)}: {b64}")
    print("Build OwnPOS again so the installer contains this public key.")


def cmd_issue(args) -> None:
    key = _load_key(Path(args.key or DEFAULT_KEY))
    licensee = args.licensee or input("Store / business name: ").strip()
    store = args.store_id or input("Store ID (shown on the store's activation screen): ").strip()
    stations = int(args.stations or input("Number of stations: ").strip() or "1")
    expires = args.expires if args.expires is not None else input("Expiry date YYYY-MM-DD (empty = never): ").strip()
    if expires:
        date.fromisoformat(expires)
    serial = args.serial or f"OWN-{date.today():%Y%m%d}-{store[:6].upper()}"
    payload = {"product": "OwnPOS", "v": 1, "serial": serial, "licensee": licensee, "store_uid": store,
               "stations": stations, "expires": expires or None, "issued": date.today().isoformat(),
               "features": ["all"]}
    signed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    text = pack(payload, key.sign(signed))
    out = Path(args.out or f"{licensee.replace(' ', '_')}_{serial}.ownpos-licence")
    out.write_text(text, encoding="utf-8")
    verify_text(text, _b64e(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                          serialization.PublicFormat.Raw)))
    print(f"Licence written to {out}:\n{text}")


def cmd_verify(args) -> None:
    payload = verify_text(Path(args.file).read_text(encoding="utf-8"))
    print("Valid licence:", json.dumps(payload, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--key")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("issue")
    for a in ("--key", "--licensee", "--store-id", "--stations", "--expires", "--serial", "--out"):
        p.add_argument(a)
    p.set_defaults(fn=cmd_issue)
    p = sub.add_parser("verify")
    p.add_argument("file")
    p.set_defaults(fn=cmd_verify)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
