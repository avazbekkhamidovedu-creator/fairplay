#!/bin/bash
# One-time: make the fixed self-signed code-signing identity used when there is no Apple
# Developer ID (FAIRPLAY_SIGN_SELF="FairPlay Code Signing"). Keep the .p12 and its password
# safe (CI secrets MAC_SIGN_P12 = base64 of the .p12, MAC_SIGN_PASSWORD): every release must be
# signed with the SAME certificate, so macOS keeps the Accessibility permission across updates
# and the in-app updater accepts the new copy. Losing it means one manual reinstall for everyone.
set -euo pipefail
NAME="${1:-FairPlay Code Signing}"
OUT="${2:-FairPlay-signing.p12}"
PASS="${P12_PASSWORD:-$(uuidgen)}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/openssl.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$TMP/openssl.cnf" \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" 2>/dev/null
openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -name "$NAME" -out "$OUT" -passout "pass:$PASS"
echo "Wrote $OUT (identity name: $NAME). Password: $PASS"
echo "CI: MAC_SIGN_P12=\$(base64 < $OUT), MAC_SIGN_PASSWORD=<password>. Local: security import $OUT -k ~/Library/Keychains/login.keychain-db -T /usr/bin/codesign"
