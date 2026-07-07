from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings

_bearer = HTTPBearer(auto_error=False)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="analyst-session")


def issue_token(username: str) -> str:
    return _serializer().dumps({"u": username})


def require_analyst(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing analyst token")
    try:
        data = _serializer().loads(
            credentials.credentials, max_age=get_settings().session_max_age_seconds
        )
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="Session expired")
    except BadSignature:
        raise HTTPException(status_code=401, detail="Invalid token")
    return data["u"]
