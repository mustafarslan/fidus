export interface Session {
  token: string;
}

const KEY = "acme.session";

export function loadSession(): Session | null {
  const raw = localStorage.getItem(KEY);
  return raw ? (JSON.parse(raw) as Session) : null;
}

export function saveSession(session: Session): void {
  localStorage.setItem(KEY, JSON.stringify(session));
}
