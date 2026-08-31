import os
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

def b64url(b):
    return base64.urlsafe_b64encode(b).decode('utf-8').rstrip('=')

env_file = ".env"

# 1. Generate ECDSA keys (VAPID standard)
private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

# 2. Convert to VAPID-compatible Base64URL strings
# Private key: Raw bytes
priv_bytes = private_key.private_numbers().private_value.to_bytes(32, 'big')
private_string = b64url(priv_bytes)

# Public key: Uncompressed point (X9.62 format)
pub_bytes = public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)
public_string = b64url(pub_bytes)

# 3. Append to .env
new_lines = [
    "\n# VAPID Keys for Web Push\n",
    f"VAPID_PUBLIC_KEY={public_string}\n",
    f"VAPID_PRIVATE_KEY={private_string}\n",
    "VAPID_SENDER_EMAIL=admin@hadi88.online\n"
]

if os.path.exists(env_file):
    with open(env_file, "a") as f:
        f.writelines(new_lines)
    print("✅ VAPID keys successfully generated and added to .env")
    print(f"Public Key: {public_string}")
else:
    print("❌ Error: .env file not found.")
