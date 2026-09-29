import { fetchOrders } from "../api/client";
import type { Session } from "../lib/session";

export async function renderOrders(root: HTMLElement, session: Session): Promise<void> {
  const orders = await fetchOrders(session.token);
  root.innerHTML = orders.map((o) => `<li>#${o.id} — ${(o.total_cents / 100).toFixed(2)}</li>`).join("");
}
