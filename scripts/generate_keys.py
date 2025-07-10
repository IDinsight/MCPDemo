"""This script generates a pair of RSA keys for testing."""

# Standard Library
import os

from getpass import getpass
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PATHS_PROJECT_DIR = Path(os.getenv("PATHS_PROJECT_DIR", ""))
assert Path.is_dir(PATHS_PROJECT_DIR)
secrets_dir = PATHS_PROJECT_DIR / "secrets"
if not Path.is_dir(secrets_dir):
    Path.mkdir(secrets_dir, exist_ok=True, mode=0o777, parents=True)

passphrase = getpass("PEM passphrase: ").encode()

key = rsa.generate_private_key(key_size=3072, public_exponent=65537)  # RSA-3072
pem = key.private_bytes(
    encoding=serialization.Encoding.PEM,
    encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
    format=serialization.PrivateFormat.TraditionalOpenSSL,
)
(secrets_dir / "private.pem").write_bytes(pem)

pub_pem = key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)
(secrets_dir / "public.pem").write_bytes(pub_pem)
