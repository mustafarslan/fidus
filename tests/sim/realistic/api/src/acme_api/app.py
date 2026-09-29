"""Application factory: wires configuration, auth middleware and routes."""

from acme_api.auth.middleware import require_token
from acme_api.config import Settings
from acme_api.routes import orders


def create_app(settings: Settings | None = None) -> dict:
    settings = settings or Settings.from_env()
    routes = {
        "GET /orders": require_token(orders.list_orders),
        "POST /orders": require_token(orders.create_order),
    }
    return {"settings": settings, "routes": routes}
