"use client";

import { useState } from "react";

interface LoginFormProps {
  onGoogleLogin: () => void;
  onEmailLogin: (email: string, password: string) => Promise<boolean>;
  onEmailSignup: (email: string, name: string, password: string) => Promise<boolean>;
  error: string;
}

export function LoginForm({ onGoogleLogin, onEmailLogin, onEmailSignup, error }: LoginFormProps) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [isSignup, setIsSignup] = useState(false);
  const [showEmailForm, setShowEmailForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    if (isSignup) {
      await onEmailSignup(email, name, password);
    } else {
      await onEmailLogin(email, password);
    }
    setSubmitting(false);
  };

  return (
    <div className="flex items-center justify-center h-screen bg-bg">
      <div className="w-full max-w-sm p-8 bg-surface rounded-lg border border-border/20">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 bg-primary rounded-sm flex items-center justify-center">
            <span className="text-white text-lg font-bold">O</span>
          </div>
          <div>
            <h1 className="font-[family-name:var(--font-headline)] font-black text-lg tracking-tight">
              Outreach
            </h1>
            <p className="text-sm text-text-muted">Outreach Pipeline</p>
          </div>
        </div>

        {/* Google OAuth button */}
        <button
          onClick={onGoogleLogin}
          className="w-full flex items-center justify-center gap-3 py-2.5 px-4 text-sm font-medium border border-border/30 rounded-md bg-white hover:bg-gray-50 transition-colors mb-4"
        >
          <svg width="18" height="18" viewBox="0 0 48 48">
            <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
            <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
            <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
            <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
          </svg>
          Sign in with Google
        </button>

        {/* Divider */}
        {!showEmailForm && (
          <button
            onClick={() => setShowEmailForm(true)}
            className="w-full text-sm text-text-faint hover:text-text-muted transition-colors py-2"
          >
            or sign in with email
          </button>
        )}

        {/* Email fallback form */}
        {showEmailForm && (
          <>
            <div className="flex items-center gap-3 my-4">
              <div className="flex-1 border-t border-border/20" />
              <span className="text-sm text-text-faint uppercase">or</span>
              <div className="flex-1 border-t border-border/20" />
            </div>

            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className="w-full px-3 py-2 text-sm border border-border/30 rounded-md bg-bg focus:outline-none focus:ring-2 focus:ring-primary/30"
                  placeholder="Email"
                />
              </div>

              {isSignup && (
                <div>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                    className="w-full px-3 py-2 text-sm border border-border/30 rounded-md bg-bg focus:outline-none focus:ring-2 focus:ring-primary/30"
                    placeholder="Name"
                  />
                </div>
              )}

              <div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  className="w-full px-3 py-2 text-sm border border-border/30 rounded-md bg-bg focus:outline-none focus:ring-2 focus:ring-primary/30"
                  placeholder="Password"
                />
              </div>

              {error && <p className="text-sm text-red-500">{error}</p>}

              <button
                type="submit"
                disabled={submitting}
                className="w-full py-2 px-4 text-sm font-medium text-white bg-primary rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {submitting ? "..." : isSignup ? "Create Account" : "Sign In"}
              </button>

              <button
                type="button"
                onClick={() => setIsSignup(!isSignup)}
                className="w-full text-sm text-text-muted hover:text-primary transition-colors"
              >
                {isSignup ? "Already have an account? Sign in" : "New user? Create account"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
