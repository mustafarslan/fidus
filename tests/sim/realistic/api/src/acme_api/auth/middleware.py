"""Route decorator that rejects requests without a valid bearer token."""

from acme_api.auth.tokens import verify_token


def require_token(handler):
    def wrapped(request: dict) -> dict:
        header = request.get("headers", {}).get("Authorization", "")
        user_id = verify_token(header.removeprefix("Bearer "))
        if user_id is None:
            return {"status": 401, "body": {"error": "invalid or expired token"}}
        request["user_id"] = user_id
        return handler(request)

    return wrapped
