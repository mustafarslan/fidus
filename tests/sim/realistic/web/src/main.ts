import { renderOrders } from "./pages/orders";
import { loadSession } from "./lib/session";

export function start(root: HTMLElement): void {
  const session = loadSession();
  if (!session) {
    root.textContent = "Please sign in.";
    return;
  }
  renderOrders(root, session);
}
