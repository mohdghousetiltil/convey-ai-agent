/**
 * UserContext — loads the workspace user from the local backend.
 *
 * The app has no sign-in: the backend always answers with this install's user.
 * The UI waits here until that answer arrives, so everything below can rely on
 * a real user. The backend may still be starting, so failures keep retrying.
 */

import React, { createContext, useContext, useEffect, useState } from "react";
import { whoami } from "./api";
import type { LocalUser } from "./api";

const RETRY_DELAY_MS = 1000;
const SHOW_MESSAGE_AFTER_ATTEMPTS = 5;

const UserContext = createContext<LocalUser | null>(null);

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<LocalUser | null>(null);
  const [isDelayed, setIsDelayed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let attempts = 0;

    const load = async () => {
      try {
        const nextUser = await whoami();
        if (!cancelled) setUser(nextUser);
      } catch {
        if (cancelled) return;
        attempts += 1;
        if (attempts >= SHOW_MESSAGE_AFTER_ATTEMPTS) setIsDelayed(true);
        timer = window.setTimeout(load, RETRY_DELAY_MS);
      }
    };

    void load();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  if (!user) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background">
        <svg
          className="h-8 w-8 animate-spin text-muted-foreground"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        {isDelayed ? (
          <p className="text-sm text-muted-foreground">
            Waiting for the local backend. If this continues, restart the app.
          </p>
        ) : null}
      </div>
    );
  }

  return <UserContext.Provider value={user}>{children}</UserContext.Provider>;
}

export function useUser(): LocalUser {
  const user = useContext(UserContext);
  if (!user) throw new Error("useUser must be used within <UserProvider>.");
  return user;
}
