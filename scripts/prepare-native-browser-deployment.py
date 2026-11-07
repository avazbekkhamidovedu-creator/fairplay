#!/usr/bin/env python3
"""Prepare separate private deployment bundles. Never copies or deploys them.

Run once for a new native-browser CA. Existing output is never overwritten.
Keep owner-server/ on the owner's FairPlay VM only. No FairPlay source or app
HMAC key belongs in dku-server/. Only apps/ may enter signed app resources.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def prepare(output):
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name in ('owner-server','dku-server','apps'):
        (output/name).mkdir(mode=0o700)
    def write(name, content):
        with os.fdopen(os.open(output/name, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600),'wb') as stream:
            stream.write(content if isinstance(content,bytes) else content.encode())
    pem=serialization.Encoding.PEM
    key_bytes=lambda key:key.private_bytes(pem,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    now=datetime.now(timezone.utc)
    authority=ec.generate_private_key(ec.SECP256R1())
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'FairPlay DKU native authority')])
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(authority.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
        .not_valid_after(now+timedelta(days=730)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .add_extension(x509.KeyUsage(True,False,False,False,False,True,True,False,False),True)
        .sign(authority,hashes.SHA256()))
    gateway_key=ec.generate_private_key(ec.SECP256R1())
    gateway=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'10.200.31.64')]))
        .issuer_name(ca.subject).public_key(gateway_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('10.200.31.64'))]),False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),True)
        .sign(authority,hashes.SHA256()))
    crl=(x509.CertificateRevocationListBuilder().issuer_name(ca.subject).last_update(now-timedelta(minutes=1))
         .next_update(now+timedelta(days=30)).sign(authority,hashes.SHA256()))
    signer=ed25519.Ed25519PrivateKey.generate()
    raw=serialization.Encoding.Raw
    policy=base64.b64encode(signer.public_key().public_bytes(raw,serialization.PublicFormat.Raw)).decode()
    write('owner-server/ca.pem',ca.public_bytes(pem)); write('owner-server/ca.key',key_bytes(authority))
    write('dku-server/server-chain.pem',gateway.public_bytes(pem)+ca.public_bytes(pem))
    write('dku-server/server.key',key_bytes(gateway_key))
    write('dku-server/device-ca.pem',ca.public_bytes(pem)); write('dku-server/device-ca.crl',crl.public_bytes(pem))
    write('dku-server/ingress-secret',secrets.token_hex(32))
    write('dku-server/session-binding-secret',secrets.token_hex(32))
    write('dku-server/browser-signing-key',base64.b64encode(signer.private_bytes(raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())))
    write('apps/native-browser-profile.json',json.dumps({'gateway':'https://10.200.31.64:8443/',
        'enrollment':'https://dku.speakupai.uz/api/native-browser/enrollment/',
        'authority':base64.b64encode(ca.public_bytes(serialization.Encoding.DER)).decode(),'policyKey':policy},indent=2))
    write('apps/UniversityPolicyPublicKey.txt',policy+'\n')
    write('EXPIRY.txt','Gateway certificate: '+gateway.not_valid_after_utc.isoformat()+'\nCRL: '+crl.next_update_utc.isoformat()+'\nCA: '+ca.not_valid_after_utc.isoformat()+'\n')
    print('Prepared owner-server, dku-server and public apps bundles. No deployment performed.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    prepare(parser.parse_args().output)
