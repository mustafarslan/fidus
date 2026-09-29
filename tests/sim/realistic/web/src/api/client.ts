export interface Order {
  id: number;
  total_cents: number;
  status: "pending" | "paid" | "shipped";
}

export async function fetchOrders(token: string): Promise<Order[]> {
  const res = await fetch("/orders", { headers: { Authorization: `Bearer ${token}` } });
  if (res.status === 401) throw new Error("session expired");
  return res.json();
}
