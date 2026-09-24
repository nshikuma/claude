#!/usr/bin/env python3
"""Lab-passphrase encryption shared by the workflows and the website.

When the LAB_KEY secret is set, search requests (issue text), results and
database info are stored only in encrypted form, so a public repository
reveals nothing about the genome or the searches. The website decrypts in the
browser with the same passphrase (docs/app.js implements the same format with
Web Crypto).

Format: a JSON "envelope"
    {"v": 1, "kdf": "pbkdf2-sha256", "iter": 250000,
     "salt": base64(16 bytes), "iv": base64(12 bytes), "ct": base64(AES-256-GCM ciphertext+tag)}

CLI:  LAB_KEY=... labcrypt.py encrypt IN OUT
      LAB_KEY=... labcrypt.py decrypt IN OUT
"""

import base64
import hashlib
import json
import os
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 250000
MARKER = "-----BEGIN LAB-ENCRYPTED-----"
END_MARKER = "-----END LAB-ENCRYPTED-----"


class DecryptError(Exception):
    pass


def _key(passphrase, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, 32)


def encrypt(data, passphrase):
    if isinstance(data, str):
        data = data.encode("utf-8")
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(passphrase, salt, ITERATIONS)).encrypt(iv, data, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return json.dumps({"v": 1, "kdf": "pbkdf2-sha256", "iter": ITERATIONS,
                       "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}, separators=(",", ":"))


def decrypt(envelope, passphrase):
    try:
        env = json.loads(envelope) if isinstance(envelope, (str, bytes)) else envelope
        key = _key(passphrase, base64.b64decode(env["salt"]), int(env["iter"]))
        return AESGCM(key).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]), None)
    except Exception as e:  # wrong passphrase, corrupted or not an envelope
        raise DecryptError("could not decrypt (%s)" % type(e).__name__)


def find_envelope(text):
    """Pull the envelope out of an issue body written by the website."""
    if MARKER not in (text or ""):
        return None
    inner = text.split(MARKER, 1)[1].split(END_MARKER, 1)[0]
    return "".join(inner.split())  # GitHub may re-wrap long lines


def encrypt_file(src, dst, passphrase):
    with open(src, "rb") as fh:
        data = fh.read()
    with open(dst, "w") as fh:
        fh.write(encrypt(data, passphrase))


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("encrypt", "decrypt"):
        sys.exit(__doc__)
    key = os.environ.get("LAB_KEY")
    if not key:
        sys.exit("LAB_KEY is not set")
    mode, src, dst = sys.argv[1:]
    if mode == "encrypt":
        encrypt_file(src, dst, key)
    else:
        with open(src) as fh:
            data = decrypt(fh.read(), key)
        with open(dst, "wb") as fh:
            fh.write(data)


if __name__ == "__main__":
    main()
