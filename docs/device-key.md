# Device key binding (client contract)

One exam attempt stays on the device it first bound to. The device id is
no longer a client-chosen string: it is derived from a key pair whose private half never leaves
the device. Old apps keep working until the server sets `FAIRPLAY_REQUIRE_DEVICE_KEY=1`.

## Key

- P-256 (secp256r1) key pair, created once per install, private key in the Keychain / Secure
  Enclave / DPAPI / TPM (non-exportable where the platform allows).
- `device_pub` = base64url (no padding; padding is tolerated) of the DER SubjectPublicKeyInfo of
  the public key (91 bytes for P-256).
- `device_id` = first 20 bytes of `SHA-256(DER SubjectPublicKeyInfo)` as lowercase hex (40
  chars). Lowercase unpadded base32 (32 chars) of the same 20 bytes is also accepted.

## Where it is sent

The existing `client` object of `POST /api/student/exams/{id}/start`
and `POST /api/student/trial/start` gets two optional fields next
to `device_id`, `nonce`, `signature`:

```
"device_pub": "<base64url DER SPKI>",
"device_sig": "<base64url ECDSA-P256-SHA256 signature>"
```

`signature` (the app-key HMAC) is unchanged. Send both new fields or neither.

## What is signed

`device_sig` is an ECDSA signature with SHA-256 over the bytes

```
b"fp-device-v1|" + b"start|<nonce>|<device_id>"      (UTF-8, no trailing newline)
```

i.e. the same message string the HMAC signs, prefixed with `fp-device-v1|`. The nonce is the
single-use nonce from the challenge endpoint, as before. The signature may be ASN.1 DER or raw
`r || s` (64 bytes, big-endian, 32 each); the server accepts both. Apple CryptoKit's
`derRepresentation` and .NET's `SignData(..., DSASignatureFormat.Rfc3279DerSequence)` both work.

## Server behaviour

- Key present: `device_sig` must verify and `device_id` must be the derived id, otherwise 403
  with the normal app-proof error ("The FairPlay app could not be verified...").
- First bind of an attempt stores `device_pub` next to the device id.
- After that, the same attempt needs the same `device_pub` with a valid signature. A
  missing or different key, even with the same `device_id`, is a different device: start answers
  409 `swap_needed` and a `second_device` event is recorded. (The removed access-code sign-in
  also recorded `access_code_second_device`; old events may still show it.)
- Upgrade of an existing install: an attempt bound before this feature (device id stored,
  no key) accepts the first request that carries a valid key together with that same legacy
  `device_id` (the id is then not the derived one; keep sending the legacy id and the key).
  The key is stored once and an event `device_key_upgraded` is recorded. New installs must use
  the derived id.
- Teacher release-device and an approved swap clear the stored key together with the device id.
- With `FAIRPLAY_REQUIRE_DEVICE_KEY=1`, a start without a valid key is refused with
  426 `{"detail": "Update FairPlay to continue", "min_app_version": ""}` (same shape as the
  version gate).
- Re-sending the start within 15 s from the same device and key returns the same session.
