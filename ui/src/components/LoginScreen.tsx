/**
 * LoginScreen — email/password + Microsoft/Google OAuth + self-registration.
 *
 * No firm slug required — the desktop app resolves the client automatically.
 * Views: "login" | "register"
 */

import React, { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Building2, Eye, EyeOff, KeyRound, LogIn, UserPlus } from "lucide-react";
import { Button } from "./ui/button";
import { useAuth } from "../lib/AuthContext";
import { getOAuthProviders } from "../lib/api";
import type { OAuthProviders } from "../lib/api";

type View = "login" | "register";

export function LoginScreen() {
  const { login, register, loginOAuth } = useAuth();
  const [view, setView] = useState<View>("login");

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
          <p className="text-slate-500 text-sm">
            {view === "login" ? "Sign in to your account" : "Create your account"}
          </p>
        </div>

        <AnimatePresence mode="wait">
          {view === "login" ? (
            <LoginForm
              key="login"
              login={login}
              loginOAuth={loginOAuth}
              onCreateAccount={() => setView("register")}
            />
          ) : (
            <RegisterForm
              key="register"
              register={register}
              loginOAuth={loginOAuth}
              onBack={() => setView("login")}
            />
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Login form
// ---------------------------------------------------------------------------

function LoginForm({
  login,
  loginOAuth,
  onCreateAccount,
}: {
  login: (email: string, password: string) => Promise<void>;
  loginOAuth: (provider: "google" | "microsoft") => Promise<void>;
  onCreateAccount: () => void;
}) {
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
    getOAuthProviders()
      .then(setProviders)
      .catch(() => {});
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) { setError("Enter your email."); return; }
    if (!password) { setError("Enter your password."); return; }
    setError("");
    setLoading(true);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  async function handleOAuth(provider: "google" | "microsoft") {
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
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -20 }}
      transition={{ duration: 0.22 }}
    >
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        {/* OAuth buttons — primary auth for Microsoft */}
        {showOAuth && (
          <div className="p-6 border-b border-slate-100 space-y-3">
            {msConfigured && (
              <button
                type="button"
                disabled={!!oauthLoading || loading}
                onClick={() => handleOAuth("microsoft")}
                className="w-full flex items-center justify-center gap-3 px-4 py-3 rounded-xl border border-slate-200 bg-white text-slate-800 text-sm font-semibold hover:bg-slate-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {oauthLoading === "microsoft" ? <Spinner /> : <MicrosoftIcon />}
                Continue with Microsoft
              </button>
            )}
            {googleConfigured && (
              <button
                type="button"
                disabled={!!oauthLoading || loading}
                onClick={() => handleOAuth("google")}
                className="w-full flex items-center justify-center gap-3 px-4 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-800 text-sm font-medium hover:bg-slate-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {oauthLoading === "google" ? <Spinner /> : <GoogleIcon />}
                Continue with Google
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
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

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
            {loading ? <Spinner light /> : <LogIn size={16} />}
            {loading ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </div>

      <p className="text-center text-xs text-slate-400 mt-5">
        New to Convey Agent?{" "}
        <button
          onClick={onCreateAccount}
          className="text-slate-600 font-semibold hover:text-slate-900 transition-colors underline underline-offset-2"
        >
          Create an account
        </button>
      </p>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Register form
// ---------------------------------------------------------------------------

function RegisterForm({
  register,
  loginOAuth,
  onBack,
}: {
  register: (name: string, companyName: string, email: string, password: string, activationKey: string) => Promise<void>;
  loginOAuth: (provider: "google" | "microsoft") => Promise<void>;
  onBack: () => void;
}) {
  const [name, setName] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [activationKey, setActivationKey] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState<"microsoft" | "google" | null>(null);
  const [error, setError] = useState("");
  const [providers, setProviders] = useState<OAuthProviders | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameRef.current?.focus();
    getOAuthProviders().then(setProviders).catch(() => {});
  }, []);

  const msConfigured = providers?.microsoft?.configured ?? false;
  const googleConfigured = providers?.google?.configured ?? false;

  async function handleOAuth(provider: "google" | "microsoft") {
    setError("");
    setOauthLoading(provider);
    try {
      await loginOAuth(provider);
    } catch (err) {
      setError(err instanceof Error ? err.message : `${provider} sign-up failed.`);
    } finally {
      setOauthLoading(null);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError("Enter your full name."); return; }
    if (!companyName.trim()) { setError("Enter your firm or company name."); return; }
    if (!email.trim()) { setError("Enter your email."); return; }
    if (password.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (password !== confirm) { setError("Passwords do not match."); return; }
    setError("");
    setLoading(true);
    try {
      await register(name.trim(), companyName.trim(), email.trim(), password, activationKey.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ duration: 0.22 }}
    >
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        {/* Microsoft / Google OAuth — fastest path for corporate accounts */}
        {(msConfigured || googleConfigured) && (
          <div className="p-6 border-b border-slate-100 space-y-3">
            {msConfigured && (
              <button
                type="button"
                disabled={!!oauthLoading || loading}
                onClick={() => handleOAuth("microsoft")}
                className="w-full flex items-center justify-center gap-3 px-4 py-3 rounded-xl border border-[#2f6bc4] bg-[#2f6bc4] text-white text-sm font-semibold hover:bg-[#1a5bb5] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {oauthLoading === "microsoft" ? <Spinner light /> : <MicrosoftIcon />}
                Create account with Microsoft
              </button>
            )}
            {googleConfigured && (
              <button
                type="button"
                disabled={!!oauthLoading || loading}
                onClick={() => handleOAuth("google")}
                className="w-full flex items-center justify-center gap-3 px-4 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-800 text-sm font-medium hover:bg-slate-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {oauthLoading === "google" ? <Spinner /> : <GoogleIcon />}
                Create account with Google
              </button>
            )}
            {msConfigured && (
              <p className="text-[0.68rem] text-slate-400 text-center px-2">
                Your Microsoft organisation is automatically detected — no activation key needed.
              </p>
            )}
            <div className="flex items-center gap-3 pt-1">
              <hr className="flex-1 border-slate-100" />
              <span className="text-xs text-slate-400 shrink-0">or create with email</span>
              <hr className="flex-1 border-slate-100" />
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div className="space-y-1">
            <label className="block text-xs font-medium text-slate-600 uppercase tracking-wide">Full name</label>
            <input
              ref={nameRef}
              type="text"
              autoComplete="name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Jane Smith"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-transparent transition"
            />
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-medium text-slate-600 uppercase tracking-wide">Firm / Company name</label>
            <div className="relative">
              <input
                type="text"
                autoComplete="organization"
                required
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder="Acme Conveyancing"
                className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 pl-10 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-transparent transition"
              />
              <Building2 className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            </div>
            <p className="text-[11px] text-slate-400">
              We use this as your firm name and create the workspace/client slug from it if needed.
            </p>
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-medium text-slate-600 uppercase tracking-wide">Email</label>
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@yourfirm.com"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-transparent transition"
            />
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-medium text-slate-600 uppercase tracking-wide">Password</label>
            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Min. 8 characters"
                className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 pr-10 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-transparent transition"
              />
              <button
                type="button"
                tabIndex={-1}
                onClick={() => setShowPassword((v) => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-medium text-slate-600 uppercase tracking-wide">Confirm password</label>
            <input
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="Repeat password"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-transparent transition"
            />
          </div>

          <div className="space-y-1">
            <label className="block text-xs font-medium text-slate-600 uppercase tracking-wide">Activation key (optional)</label>
            <div className="relative">
              <input
                type="text"
                autoComplete="off"
                value={activationKey}
                onChange={(e) => setActivationKey(e.target.value)}
                placeholder="Enter only if your firm gave you one"
                className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 pl-10 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-transparent transition"
              />
              <KeyRound className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            </div>
          </div>

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
            {loading ? <Spinner light /> : <UserPlus size={16} />}
            {loading ? "Creating account…" : "Create account"}
          </Button>
        </form>
      </div>

      <p className="text-center text-xs text-slate-400 mt-5">
        Already have an account?{" "}
        <button
          onClick={onBack}
          className="text-slate-600 font-semibold hover:text-slate-900 transition-colors underline underline-offset-2"
        >
          Sign in
        </button>
      </p>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Micro-components
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
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
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
    <svg width="20" height="20" viewBox="0 0 23 23" className="shrink-0">
      <path fill="#f3f3f3" d="M0 0h23v23H0z" />
      <path fill="#f35325" d="M1 1h10v10H1z" />
      <path fill="#81bc06" d="M12 1h10v10H12z" />
      <path fill="#05a6f0" d="M1 12h10v10H1z" />
      <path fill="#ffba08" d="M12 12h10v10H12z" />
    </svg>
  );
}
