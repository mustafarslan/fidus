"""Order endpoints."""

from acme_api.cache.redis_cache import cached
from acme_api.models.order import Order

_ORDERS: dict[int, Order] = {}


@cached(ttl_seconds=30)
def list_orders(request: dict) -> dict:
    mine = [o for o in _ORDERS.values() if o.user_id == request["user_id"]]
    return {"status": 200, "body": [o.__dict__ for o in mine]}


def create_order(request: dict) -> dict:
    order = Order(
        id=len(_ORDERS) + 1, user_id=request["user_id"], total_cents=request["body"]["total_cents"]
    )
    _ORDERS[order.id] = order
    return {"status": 201, "body": order.__dict__}
