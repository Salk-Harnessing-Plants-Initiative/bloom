import jwt
import time
import os

jwt_secret = os.environ.get("BLOOM_JWT_SECRET", "super-secret-jwt-token-with-at-least-32-characters-long")

payload = {
    "sub": "test-user",
    "aud": "authenticated",
    "iat": int(time.time()),
    "exp": int(time.time()) + 3600
}

token = jwt.encode(payload, jwt_secret, algorithm="HS256")
print(token)