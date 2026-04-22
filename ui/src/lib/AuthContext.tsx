/**
 * AuthContext — provides the current user + login/logout to the whole app.
 *
 * On mount it reads localStorage. On 401 events it clears state and shows
 * the login screen. Login/logout functions update both the token store and
 * component state atomically.
 */

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { AuthUser, StoredAuth } from "./auth";
import { clearAuth, loadAuth, saveAuth } from "./auth";
import { AUTH_LOGOUT_EVENT, loadPersistedAuthState, login as apiLogin, loginWithOAuth, logout as apiLogout, register as apiRegister, resumePendingOAuthLogin } from "./api";
import type { LoginPayload } from "./api";

// ---------------------------------------------------------------------------
// Context shape
// ---------------------------------------------------------------------------

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  isLoading: boolean;
  /** Password-based login — clientSlug optional for single-tenant desktop */
  login: (email: string, password: string, clientSlug?: string) => Promise<void>;
  /** Self-registration with firm/company name and optional activation key */
  register: (name: string, companyName: string, email: string, password: string, activationKey: string) => Promise<void>;
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
  const oauthResumeInFlight = useRef(false);

  // On mount: restore from localStorage.
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const stored: StoredAuth | null = loadAuth();
      if (stored) {
        if (!cancelled) {
          setUser(stored.user);
          setToken(stored.token);
          setIsLoading(false);
        }
        return;
      }

      const persisted = await loadPersistedAuthState();
      if (persisted && !cancelled) {
        saveAuth(persisted.access_token, persisted.user);
        setUser(persisted.user);
        setToken(persisted.access_token);
        setIsLoading(false);
        return;
      }

      const resumed = await resumePendingOAuthLogin();
      if (!cancelled && resumed) {
        setUser(resumed.user);
        setToken(resumed.access_token);
      }

      if (!cancelled) {
        setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // Desktop OAuth recovery: while the app is sitting on the login screen,
  // keep checking whether an external-browser OAuth flow has completed.
  useEffect(() => {
    if (user) return;

    let cancelled = false;

    async function checkPendingOAuth() {
      if (cancelled || oauthResumeInFlight.current) return;
      oauthResumeInFlight.current = true;
      try {
        const resumed = await resumePendingOAuthLogin();
        if (!cancelled && resumed) {
          setUser(resumed.user);
          setToken(resumed.access_token);
          setIsLoading(false);
        }
      } finally {
        oauthResumeInFlight.current = false;
      }
    }

    void checkPendingOAuth();
    const timer = window.setInterval(() => {
      void checkPendingOAuth();
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [user]);

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
    async (email: string, password: string, clientSlug = ""): Promise<void> => {
      const payload: LoginPayload = await apiLogin(email, password, clientSlug);
      setUser(payload.user);
      setToken(payload.access_token);
    },
    [],
  );

  const register = useCallback(
    async (name: string, companyName: string, email: string, password: string, activationKey: string): Promise<void> => {
      const payload: LoginPayload = await apiRegister(name, companyName, email, password, activationKey);
      setUser(payload.user);
      setToken(payload.access_token);
    },
    [],
  );

  const loginOAuth = useCallback(async (provider: "google" | "microsoft"): Promise<void> => {
    const payload: LoginPayload | null = await loginWithOAuth(provider);
    if (!payload) return;
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
    <AuthContext.Provider value={{ user, token, isLoading, login, register, loginOAuth, logout }}>
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
