/**
 * LoginScreen — email/password + Google/Microsoft OAuth.
 *
 * Matches the existing app visual language:
 *  - Playfair Display serif italic heading
 *  - Geist sans body
 *  - Slate colour palette + motion animations
 */

import React, { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { Eye, EyeOff, LogIn } from "lucide-react";
import { Button } from "./ui/button";
import { useAuth } from "../lib/AuthContext";
import { getClientSlug } from "../lib/auth";
import { getOAuthProviders } from "../lib/api";
import type { OAuthProviders } from "../lib/api";

interface LoginScreenProps {
  /** Optional override; if absent, falls back to VITE_CLIENT_SLUG env var. */
  clientSlug?: string;
}

export function LoginScreen({ clientSlug: slugProp }: LoginScreenProps) {
  const { login, loginOAuth } = useAuth();
  const envSlug = getClientSlug();
  const resolvedSlug = slugProp || envSlug;

  const [clientSlug, setClientSlug] = useState(resolvedSlug);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState<"google" | "microsoft" | null>(null);
  const [error, setError] = useState("");
  const [providers, setProviders] = useState<OAuthProviders | null>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    emailRef.current?.focus();
    // Fetch which OAuth providers are configured on this install.
    getOAuthProviders()
      .then(setProviders)
      .catch(() => {/* silently ignore — may be offline */});
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!clientSlug.trim()) { setError("Enter the firm slug."); return; }
    if (!email.trim()) { setError("Enter your email."); return; }
    if (!password) { setError("Enter your password."); return; }
    setError("");
    setLoading(true);
    try {
      await login(clientSlug.trim(), email.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  async function handleOAuth(provider: "google" | "microsoft") {
    if (!clientSlug.trim()) { setError("Enter the firm slug first."); return; }
    setError("");
    setOauthLoading(provider);
    try {
      await loginOAuth(provider);
    } catch (err) {
      setError(err instanceof Error ? err.message : `${provider} login failed.`);
    } finally {
      setOauthLoading(null);
    }
  }

  const googleConfigured = providers?.google?.configured ?? false;
  const msConfigured = providers?.microsoft?.configured ?? false;
  const showOAuth = googleConfigured || msConfigured;

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-6 font-sans">
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: "easeOut" }}
        className="w-full max-w-md"
      >
        {/* Header */}
        <div className="text-center mb-10 space-y-1">
          <h1 className="text-4xl font-serif italic tracking-tight text-slate-900">
            Convey Agent
          </h1>
          <p className="text-slate-500 text-sm">Sign in to your account</p>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          {/* OAuth buttons */}
          {showOAuth && (
            <div className="p-6 border-b border-slate-100 space-y-3">
              {googleConfigured && (
                <button
                  type="button"
                  disabled={!!oauthLoading || loading}
                  onClick={() => handleOAuth("google")}
                  className="w-full flex items-center justify-center gap-3 px-4 py-2.5 rounded-lg border border-slate-200 bg-white text-slate-800 text-sm font-medium hover:bg-slate-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {oauthLoading === "google" ? (
                    <Spinner />
                  ) : (
                    <GoogleIcon />
                  )}
                  Continue with Google
                </button>
              )}
              {msConfigured && (
                <button
                  type="button"
                  disabled={!!oauthLoading || loading}
                  onClick={() => handleOAuth("microsoft")}
                  className="w-full flex items-center justify-center gap-3 px-4 py-2.5 rounded-lg border border-slate-200 bg-white text-slate-800 text-sm font-medium hover:bg-slate-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {oauthLoading === "microsoft" ? (
                    <Spinner />
                  ) : (
                    <MicrosoftIcon />
                  )}
                  Continue with Microsoft
                </button>
              )}
              <div className="flex items-center gap-3 pt-1">
                <hr className="flex-1 border-slate-100" />
                <span className="text-xs text-slate-400 shrink-0">or sign in with email</span>
                <hr className="flex-1 border-slate-100" />
              </div>
            </div>
          )}

          {/* Email / password form */}
          <form onSubmit={handleSubmit} className="p-6 space-y-4">
            {/* Firm slug — only shown when not pre-configured via env */}
            {!envSlug && (
              <div className="space-y-1">
                <label className="block text-xs font-medium text-slate-600 uppercase tracking-wide">
                  Firm slug
                </label>
                <input
                  type="text"
                  autoComplete="organization"
                  value={clientSlug}
                  onChange={(e) => setClientSlug(e.target.value)}
                  placeholder="e.g. acme-conveyancing"
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-transparent transition"
                />
              </div>
            )}

            <div className="space-y-1">
              <label className="block text-xs font-medium text-slate-600 uppercase tracking-wide">
                Email
              </label>
              <input
                ref={emailRef}
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-transparent transition"
              />
            </div>

            <div className="space-y-1">
              <label className="block text-xs font-medium text-slate-600 uppercase tracking-wide">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 pr-10 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-transparent transition"
                />
                <button
                  type="button"
                  tabIndex={-1}
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            {/* Error message */}
            {error && (
              <motion.p
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3.5 py-2.5"
              >
                {error}
              </motion.p>
            )}

            <Button
              type="submit"
              disabled={loading || !!oauthLoading}
              className="w-full flex items-center justify-center gap-2 mt-2"
              size="md"
            >
              {loading ? (
                <Spinner light />
              ) : (
                <LogIn size={16} />
              )}
              {loading ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </div>

        <p className="text-center text-xs text-slate-400 mt-6">
          Contact your administrator if you don't have an account.
        </p>
      </motion.div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline micro-components
// ---------------------------------------------------------------------------

function Spinner({ light = false }: { light?: boolean }) {
  return (
    <svg
      className={`animate-spin h-4 w-4 ${light ? "text-white" : "text-slate-500"}`}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" className="shrink-0">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

function MicrosoftIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 23 23" className="shrink-0">
      <path fill="#f3f3f3" d="M0 0h23v23H0z" />
      <path fill="#f35325" d="M1 1h10v10H1z" />
      <path fill="#81bc06" d="M12 1h10v10H12z" />
      <path fill="#05a6f0" d="M1 12h10v10H1z" />
      <path fill="#ffba08" d="M12 12h10v10H12z" />
    </svg>
  );
}
