"use client";

import { useState, useCallback, useEffect } from "react";

const USER_KEY = "outreach-user";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: string;
}

// Empty string = same-origin (relative URLs). Set to a full URL only
// when the frontend is served from a different host than the backend.
const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

function loadUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/**
 * Auth via httpOnly cookie (set by backend).
 *
 * Google OAuth: redirect to /auth/google/login → Google → callback sets cookie.
 * Email fallback (dev): POST /auth/login or /auth/signup sets cookie.
 *
 * The cookie is httpOnly — JS can't read it. We store user info (not token)
 * in localStorage for display. Auth state is verified via GET /auth/me.
 */
export function useAuth() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // On mount, verify the session cookie via /auth/me before showing any
  // user content. Previously we set user from localStorage cache immediately,
  // which caused a flash-and-clear when the JWT was invalid (e.g. after a
  // server restart that rotated JWT_SECRET). Now we wait for the server to
  // confirm — cache is only used as a fallback when the backend is unreachable.
  useEffect(() => {
    fetch(`${BACKEND_URL}/auth/me`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) {
          const u: AuthUser = { id: data.id, email: data.email, name: data.name, role: data.role };
          setUser(u);
          localStorage.setItem(USER_KEY, JSON.stringify(u));
        } else {
          setUser(null);
          localStorage.removeItem(USER_KEY);
        }
      })
      .catch(() => {
        // Backend unreachable — fall back to cached user so the app
        // is usable offline (read-only). If the cache is stale, the
        // next successful /auth/me call will correct it.
        const cached = loadUser();
        if (cached) setUser(cached);
      })
      .finally(() => setLoading(false));
  }, []);

  const loginWithGoogle = useCallback(() => {
    window.location.href = `${BACKEND_URL}/auth/google/login`;
  }, []);

  const loginWithEmail = useCallback(async (email: string, password: string) => {
    setError("");
    try {
      const res = await fetch(`${BACKEND_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        credentials: "include",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Login failed");
        return false;
      }
      const data = await res.json();
      const u = data.user as AuthUser;
      setUser(u);
      localStorage.setItem(USER_KEY, JSON.stringify(u));
      return true;
    } catch {
      setError("Network error");
      return false;
    }
  }, []);

  const signupWithEmail = useCallback(async (email: string, name: string, password: string) => {
    setError("");
    try {
      const res = await fetch(`${BACKEND_URL}/auth/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, name, password }),
        credentials: "include",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Signup failed");
        return false;
      }
      const data = await res.json();
      const u = data.user as AuthUser;
      setUser(u);
      localStorage.setItem(USER_KEY, JSON.stringify(u));
      return true;
    } catch {
      setError("Network error");
      return false;
    }
  }, []);

  const logout = useCallback(async () => {
    await fetch(`${BACKEND_URL}/auth/logout`, {
      method: "POST",
      credentials: "include",
    }).catch(() => {});
    setUser(null);
    localStorage.removeItem(USER_KEY);
  }, []);

  return {
    user,
    loading,
    error,
    isAuthenticated: !!user,
    loginWithGoogle,
    loginWithEmail,
    signupWithEmail,
    logout,
  };
}
