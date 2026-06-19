// Real auth: JWT in localStorage, identity from /api/auth/me.
const ORIGIN = import.meta.env.VITE_API ?? 'http://127.0.0.1:8077';
const BASE = `${ORIGIN}/api`;
const TOKEN = 'docsensei_token';

export type User = { id: number; email: string; name: string; role: string; auth_source: string };

let cached: User | null = null;

export const auth = {
  token(): string { return localStorage.getItem(TOKEN) || ''; },
  setToken(t: string) { localStorage.setItem(TOKEN, t); },
  isAuthed(): boolean { return !!localStorage.getItem(TOKEN); },
  cachedUser(): User | null { return cached; },

  async config() {
    const r = await fetch(`${BASE}/auth/config`);
    return r.json();
  },

  async login(email: string, password: string, fromAdmin = false) {
    return post('/auth/login', { email, password, from_admin: fromAdmin });
  },
  async signup(email: string, password: string, name?: string) {
    return post('/auth/signup', { email, password, name });
  },
  async ldap(username: string, password: string, directory?: string) {
    return post('/auth/ldap', { username, password, directory });
  },
  ssoUrl(pid?: string) { return `${BASE}/auth/oidc/login${pid ? `?pid=${encodeURIComponent(pid)}` : ''}`; },

  async me(): Promise<User | null> {
    if (!this.isAuthed()) return null;
    const r = await fetch(`${BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${this.token()}` }
    });
    if (!r.ok) { this.logout(); return null; }
    cached = await r.json();
    return cached;
  },

  logout() { localStorage.removeItem(TOKEN); cached = null; }
};

async function post(path: string, body: any) {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || 'request failed');
  if (data.token) { auth.setToken(data.token); cached = data.user; }
  return data; // { token, user } or { pending: true }
}
