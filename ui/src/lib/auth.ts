/**
 * Token storage + lightweight JWT decode utilities.
 *
 * Tokens live in localStorage under STORAGE_KEY.
 * We never need to verify the signature client-side — the server does that.
 * We only decode the payload to read expiry / user info.
 */

const STORAGE_KEY = "convey_auth";
export const DEFAULT_AUTH_LIFETIME_SECONDS = 30 * 24 * 60 * 60;

export interface StoredAuth {
  token: string;
  user: AuthUser;
  expiresAt: number; // Unix epoch seconds
}

export interface AuthUser {
  user_id: string;
  email: string;
  name: string;
  role: "admin" | "reviewer" | "viewer";
  client_id: string;
  client_slug: string;
  client_name: string;
}

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

export function loadAuth(): StoredAuth | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: StoredAuth = JSON.parse(raw);
    if (isExpired(parsed)) {
      clearAuth();
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function saveAuth(token: string, user: AuthUser): void {
  const claims = decodeJwtPayload(token);
  const expClaim = claims?.exp;
  const expiresAt =
    typeof expClaim === "number"
      ? expClaim
      : Math.floor(Date.now() / 1000) + DEFAULT_AUTH_LIFETIME_SECONDS;
  const stored: StoredAuth = { token, user, expiresAt };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
}

export function clearAuth(): void {
  localStorage.removeItem(STORAGE_KEY);
}

export function isExpired(auth: StoredAuth): boolean {
  // Treat tokens as expired 30 seconds early to avoid race conditions.
  return auth.expiresAt - 30 < Math.floor(Date.now() / 1000);
}

export function getToken(): string | null {
  return loadAuth()?.token ?? null;
}

// ---------------------------------------------------------------------------
// JWT payload decoder (no signature verification — server handles that)
// ---------------------------------------------------------------------------

export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const padded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(padded.padEnd(padded.length + ((4 - (padded.length % 4)) % 4), "="));
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Client slug (baked in at build time via VITE_CLIENT_SLUG)
// ---------------------------------------------------------------------------

export function getClientSlug(): string {
  // Vite exposes env vars prefixed with VITE_ at build time.
  const slug = (import.meta.env.VITE_CLIENT_SLUG as string | undefined) ?? "";
  return slug.trim();
}
