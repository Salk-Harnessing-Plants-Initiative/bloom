import jwt
import time
import os

jwt_secret = os.environ.get("BLOOM_JWT_SECRET")
if not jwt_secret:
    raise RuntimeError("BLOOM_JWT_SECRET environment variable is required")

payload = {
    "sub": "test-user",
    "aud": "authenticated",
    "iat": int(time.time()),
    "exp": int(time.time()) + 3600
}

token = jwt.encode(payload, jwt_secret, algorithm="HS256")
print(token)
