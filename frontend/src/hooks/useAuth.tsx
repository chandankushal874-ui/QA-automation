import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";

const API_BASE = import.meta.env.VITE_API_URL || "";

interface User {
  id: string;
  email: string;
  username: string;
  avatarUrl: string | null;
  role?: string;
}

interface LoginResult {
  mfaRequired: boolean;
  mfaToken?: string;
}

interface AuthContextValue {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: User | null;
  token: string | null;
  isAdmin: boolean;
  login: (password: string, username: string) => Promise<LoginResult>;
  verifyMfa: (mfaToken: string, code: string) => Promise<void>;
  completeOidcLogin: () => Promise<boolean>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => void;
  updateUser: (updates: Partial<User>) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = "cbtp_auth_token";
const USER_KEY = "cbtp_auth_user";

function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function getStoredUser(): User | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(getStoredToken);
  const [user, setUser] = useState<User | null>(getStoredUser);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const stored = getStoredToken();
    if (!stored) {
      setIsLoading(false);
      return;
    }
    fetch(`${API_BASE}/api/auth/me`, {
      headers: { Authorization: `Bearer ${stored}` },
    })
      .then((res) => {
        if (!res.ok) throw new Error("invalid");
        return res.json();
      })
      .then((u: User) => {
        setUser(u);
        setToken(stored);
        localStorage.setItem(USER_KEY, JSON.stringify(u));
      })
      .catch(() => {
        setToken(null);
        setUser(null);
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
      })
      .finally(() => setIsLoading(false));
  }, []);

  const applySession = useCallback((data: { token: string; user: User }) => {
    setToken(data.token);
    setUser(data.user);
    localStorage.setItem(TOKEN_KEY, data.token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user));
  }, []);

  const login = useCallback(
    async (password: string, username: string): Promise<LoginResult> => {
      const res = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username.trim(),
          password,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Login failed" }));
        throw new Error(
          typeof err.detail === "string" ? err.detail : "Login failed",
        );
      }
      const data = await res.json();
      if (data?.mfa_required) {
        return { mfaRequired: true, mfaToken: data.mfa_token };
      }
      applySession(data);
      return { mfaRequired: false };
    },
    [applySession],
  );

  const verifyMfa = useCallback(
    async (mfaToken: string, code: string) => {
      const res = await fetch(`${API_BASE}/api/auth/mfa/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mfa_token: mfaToken, code }),
      });
      if (!res.ok) {
        const err = await res
          .json()
          .catch(() => ({ detail: "MFA verification failed" }));
        throw new Error(
          typeof err.detail === "string" ? err.detail : "MFA verification failed",
        );
      }
      applySession(await res.json());
    },
    [applySession],
  );

  const completeOidcLogin = useCallback(async (): Promise<boolean> => {
    if (typeof window === "undefined") return false;
    const match = window.location.hash.match(/#token=([^&]+)/);
    const token = match?.[1];
    if (!token) return false;
    const res = await fetch(`${API_BASE}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return false;
    const u: User = await res.json();
    applySession({ token, user: u });
    // Clean the token fragment from the URL.
    window.history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
    return true;
  }, [applySession]);

  const register = useCallback(
    async (username: string, password: string) => {
      const res = await fetch(`${API_BASE}/api/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username.trim(),
          password,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Register failed" }));
        throw new Error(
          typeof err.detail === "string" ? err.detail : "Register failed",
        );
      }
      applySession(await res.json());
    },
    [applySession],
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }, []);

  const updateUser = useCallback((updates: Partial<User>) => {
    setUser((prev) => {
      if (!prev) return prev;
      const next = { ...prev, ...updates };
      localStorage.setItem(USER_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated: !!token && !!user,
        isLoading,
        user,
        token,
        isAdmin: user?.role === "admin",
        login,
        verifyMfa,
        completeOidcLogin,
        register,
        logout,
        updateUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
