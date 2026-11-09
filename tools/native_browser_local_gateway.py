"""Loopback-only TLS client-certificate gateway for isolated native E2E tests.

Never deploy this fixture. Production ingress needs private listener isolation,
managed CA issuance/revocation, and shared replay storage. No system trust changes.
"""
import hashlib
import http.client
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import time

ROOT = Path('/tmp/fairplay-native-gateway')
ROOT.mkdir(mode=0o700, exist_ok=True)
os.chmod(ROOT, 0o700)

def run(*args):
    subprocess.run(['/usr/bin/openssl', *args], cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=30)

if not (ROOT / 'client.p12').exists():
    run('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key', '-out', 'ca.pem',
        '-subj', '/CN=FairPlay isolated local test CA', '-days', '2',
        '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
    for name in ('server', 'client'):
        run('req', '-newkey', 'rsa:2048', '-nodes', '-keyout', name+'.key', '-out', name+'.csr', '-subj', '/CN='+name)
        (ROOT/(name+'.ext')).write_text('subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n' if name=='server'
                                      else 'extendedKeyUsage=clientAuth\n')
        run('x509', '-req', '-in', name+'.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial',
            '-out', name+'.pem', '-days', '2', '-extfile', name+'.ext')
    run('pkcs12', '-export', '-inkey', 'client.key', '-in', 'client.pem', '-out', 'client.p12',
        '-passout', 'pass:local-native-fixture')
    run('x509', '-in', 'ca.pem', '-outform', 'DER', '-out', 'ca.der')
    for name in ('proxy-secret', 'binding-secret'):
        (ROOT/name).write_text(secrets.token_hex(32))
    for path in ROOT.iterdir():
        os.chmod(path, 0o600)

from judge.utils.fairplay_native_admission import session_digest
from judge.utils.fairplay_native_proxy import sign_assertion
PROXY_SECRET = (ROOT/'proxy-secret').read_text().strip().encode()
BINDING_SECRET = (ROOT/'binding-secret').read_text().strip()
DROP = {'host', 'connection', 'transfer-encoding', 'proxy-connection', 'keep-alive', 'upgrade',
        'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto'}

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass
    def forward(self):
        if not self.path.startswith('/') or self.path.startswith('//'):
            self.send_error(400); return
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > 25*1024*1024 or self.headers.get('Transfer-Encoding'):
            self.send_error(413); return
        body = self.rfile.read(length) if length else None
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in DROP and not key.lower().startswith('x-fairplay-tls-')}
        headers.update(Host='localhost:18443', **{'X-Forwarded-Proto': 'https', 'Connection': 'close'})
        certificate = self.connection.getpeercert(binary_form=True)
        cookies = SimpleCookie(self.headers.get('Cookie', ''))
        session = cookies.get('sessionid')
        if certificate and session:
            from cryptography import x509
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            public = x509.load_der_x509_certificate(certificate).public_key()
            fingerprint = hashlib.sha256(public.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).hexdigest()
            timestamp, nonce = int(time.time()), secrets.token_hex(16)
            signature = sign_assertion(PROXY_SECRET, self.command, self.path,
                session_digest(session.value, BINDING_SECRET), fingerprint, timestamp, nonce)
            headers.update({'X-FairPlay-TLS-Fingerprint': fingerprint, 'X-FairPlay-TLS-Timestamp': str(timestamp),
                            'X-FairPlay-TLS-Nonce': nonce, 'X-FairPlay-TLS-Signature': signature})
        upstream = http.client.HTTPConnection('127.0.0.1', 18081, timeout=30)
        try:
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            data = response.read()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in ('connection', 'transfer-encoding', 'content-length', 'server', 'date'):
                    self.send_header(name, value)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
        except (OSError, http.client.HTTPException):
            self.send_error(502)
        finally:
            upstream.close()
            self.close_connection = True
    do_GET = do_POST = do_DELETE = do_HEAD = forward

server = ThreadingHTTPServer(('127.0.0.1', 18443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.minimum_version = ssl.TLSVersion.TLSv1_2
context.load_cert_chain(str(ROOT/'server.pem'), str(ROOT/'server.key'))
context.load_verify_locations(str(ROOT/'ca.pem'))
context.verify_mode = ssl.CERT_OPTIONAL
context.set_alpn_protocols(['http/1.1'])
server.socket = context.wrap_socket(server.socket, server_side=True)
print('Local native gateway ready on https://localhost:18443', flush=True)
server.serve_forever()
