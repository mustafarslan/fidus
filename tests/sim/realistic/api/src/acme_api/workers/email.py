"""Background worker that sends order notification emails."""

OUTBOX: list[tuple[str, str]] = []


def send_order_email(to: str, order_id: int) -> None:
    OUTBOX.append((to, f"Your order #{order_id} was received."))
