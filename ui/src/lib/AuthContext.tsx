/**
 * AuthContext — provides the current user + login/logout to the whole app.
 *
 * On mount it reads localStorage. On 401 events it clears state and shows
 * the login screen. Login/logout functions update both the token store and
 * component state atomically.
 */

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { AuthUser, StoredAuth } from "./auth";
import { clearAuth, loadAuth } from "./auth";
import { AUTH_LOGOUT_EVENT, login as apiLogin, loginWithOAuth, logout as apiLogout } from "./api";
import type { LoginPayload } from "./api";

// ---------------------------------------------------------------------------
// Context shape
// ---------------------------------------------------------------------------

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  isLoading: boolean;
  /** Password-based login */
  login: (clientSlug: string, email: string, password: string) => Promise<void>;
  /** OAuth popup-based login (provider = 'google' | 'microsoft') */
  loginOAuth: (provider: "google" | "microsoft") => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // On mount: restore from localStorage.
  useEffect(() => {
    const stored: StoredAuth | null = loadAuth();
    if (stored) {
      setUser(stored.user);
      setToken(stored.token);
    }
    setIsLoading(false);
  }, []);

  // Listen for global 401 events (fired by apiRequest when the server rejects the token).
  useEffect(() => {
    function handleLogout() {
      setUser(null);
      setToken(null);
    }
    window.addEventListener(AUTH_LOGOUT_EVENT, handleLogout);
    return () => window.removeEventListener(AUTH_LOGOUT_EVENT, handleLogout);
  }, []);

  const login = useCallback(
    async (clientSlug: string, email: string, password: string): Promise<void> => {
      const payload: LoginPayload = await apiLogin(clientSlug, email, password);
      setUser(payload.user);
      setToken(payload.access_token);
    },
    [],
  );

  const loginOAuth = useCallback(async (provider: "google" | "microsoft"): Promise<void> => {
    const payload: LoginPayload = await loginWithOAuth(provider);
    setUser(payload.user);
    setToken(payload.access_token);
  }, []);

  const logout = useCallback(async (): Promise<void> => {
    try {
      await apiLogout();
    } finally {
      clearAuth();
      setUser(null);
      setToken(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, isLoading, login, loginOAuth, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>.");
  return ctx;
}
