import base64
import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from app.native_browser_enrollment import router, proof_message, issue_certificate
from app.app_client import sign
from app.store import MemoryStore, get_store


@pytest.fixture
def enrollment(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test-native-ca')])
    now = datetime.now(timezone.utc)
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
          .not_valid_after(now+timedelta(days=30)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
          .sign(key,hashes.SHA256()))
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    (tmp_path/'ca.pem').write_bytes(ca_pem)
    (tmp_path/'ca.key').write_bytes(key_pem); (tmp_path/'ca.key').chmod(0o600)
    app = FastAPI(); app.include_router(router)
    app.state.settings = SimpleNamespace(native_browser_enrollment=True, native_browser_ca=str(tmp_path/'ca.pem'),
        native_browser_ca_key=str(tmp_path/'ca.key'),app_keys=['test-enrollment-key'])
    store = MemoryStore(); app.dependency_overrides[get_store] = lambda: store
    private = ec.generate_private_key(ec.SECP256R1())
    csr = (x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME,'Untrusted requested name')]))
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(private,hashes.SHA256()))
    with TestClient(app) as client:
        yield client, csr.public_bytes(serialization.Encoding.DER), private, ca_pem, key_pem


def payload(client, csr):
    digest=hashlib.sha256(csr).hexdigest()
    nonce=client.post('/api/native-browser/enrollment/challenge',json={'csr_sha256':digest}).json()['nonce']
    return {'nonce':nonce,'csr':base64.b64encode(csr).decode(),
            'signature':sign('test-enrollment-key',proof_message(nonce,digest))}


def test_certificate_uses_requested_key_but_never_requested_ca_permissions(enrollment):
    client,csr,key,ca,key_pem=enrollment
    body=payload(client,csr)
    result=client.post('/api/native-browser/enrollment/certificate',json=body)
    assert result.status_code==200
    cert=x509.load_der_x509_certificate(base64.b64decode(result.json()['certificate']))
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value==x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH])
    assert cert.public_key().public_numbers()==key.public_key().public_numbers()
    assert 'Untrusted' not in cert.subject.rfc4514_string()
    assert client.post('/api/native-browser/enrollment/certificate',json=body).status_code==409


def test_wrong_app_proof_or_changed_csr_cannot_enroll(enrollment):
    client,csr,*_=enrollment
    body=payload(client,csr)
    original=body['signature']; body['signature']='0'*64
    assert client.post('/api/native-browser/enrollment/certificate',json=body).status_code==403
    body['signature']=original
    body['csr']=base64.b64encode(b'not-a-csr').decode()
    assert client.post('/api/native-browser/enrollment/certificate',json=body).status_code==403


def test_enrollment_is_off_without_explicit_configuration(enrollment):
    client,csr,*_=enrollment
    client.app.state.settings.native_browser_enrollment=False
    assert client.post('/api/native-browser/enrollment/challenge',json={'csr_sha256':hashlib.sha256(csr).hexdigest()}).status_code==404


def test_certificate_lifetime_defaults_to_thirty_days_and_follows_the_setting(enrollment):
    client,csr,key,ca,key_pem=enrollment
    def issued():
        r=client.post('/api/native-browser/enrollment/certificate',json=payload(client,csr))
        return x509.load_der_x509_certificate(base64.b64decode(r.json()['certificate']))
    cert=issued()
    assert cert.not_valid_after_utc-cert.not_valid_before_utc==timedelta(days=30,minutes=1)
    client.app.state.settings.native_browser_cert_days=10
    cert=issued()
    assert cert.not_valid_after_utc-cert.not_valid_before_utc==timedelta(days=10,minutes=1)


def test_challenge_ip_limit_is_a_setting_and_school_nets_get_a_higher_one(enrollment):
    client,csr,*_=enrollment
    s=client.app.state.settings
    s.native_browser_enroll_ip_limit=2
    s.in_school_net=lambda ip: False
    body={'csr_sha256':hashlib.sha256(csr).hexdigest()}
    url='/api/native-browser/enrollment/challenge'
    assert [client.post(url,json=body).status_code for _ in range(3)]==[200,200,429]
    s.in_school_net=lambda ip: True
    s.native_browser_enroll_school_ip_limit=6
    assert [client.post(url,json=body).status_code for _ in range(4)]==[200,200,200,429]
