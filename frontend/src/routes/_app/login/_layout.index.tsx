import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Input } from "@/ui/input";
import { Button } from "@/ui/button";
import { Route as DashboardRoute } from "@/routes/_app/_auth/dashboard/_layout.index";
import { useAuth } from "@/hooks/useAuth";
import { getOidcStatus } from "@/lib/api";

const API_BASE = import.meta.env.VITE_API_URL || "";

export const Route = createFileRoute("/_app/login/_layout/")({
  component: Login,
});

type Mode = "login" | "register" | "mfa";

function Login() {
  const {
    isAuthenticated,
    isLoading: authLoading,
    login,
    register,
    verifyMfa,
    completeOidcLogin,
  } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mfaToken, setMfaToken] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [oidcLoginUrl, setOidcLoginUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Handle a Google OIDC redirect back to /login#token=<jwt>.
    completeOidcLogin().then((ok) => {
      if (!cancelled && ok) {
        navigate({ to: DashboardRoute.fullPath });
      }
    });
    getOidcStatus()
      .then((s) => {
        if (!cancelled) setOidcLoginUrl(s.loginUrl);
      })
      .catch(() => {
        // OIDC status is optional; silently ignore failures.
      });
    return () => {
      cancelled = true;
    };
  }, [completeOidcLogin, navigate]);

  useEffect(() => {
    if (!authLoading && isAuthenticated) {
      navigate({ to: DashboardRoute.fullPath });
    }
  }, [authLoading, isAuthenticated, navigate]);

  if (!authLoading && isAuthenticated) {
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (mode === "mfa") {
      if (!mfaCode.trim()) {
        setError("Enter the 6-digit code");
        return;
      }
      setIsSubmitting(true);
      try {
        await verifyMfa(mfaToken, mfaCode.trim());
        navigate({ to: DashboardRoute.fullPath });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Invalid code";
        setError(msg);
      } finally {
        setIsSubmitting(false);
      }
      return;
    }

    const u = username.trim();
    if (!u) {
      setError("Username is required");
      return;
    }
    if (!password) {
      setError("Password is required");
      return;
    }
    setIsSubmitting(true);
    try {
      if (mode === "register") {
        await register(u, password);
        navigate({ to: DashboardRoute.fullPath });
      } else {
        const result = await login(password, u);
        if (result.mfaRequired && result.mfaToken) {
          setMfaToken(result.mfaToken);
          setMfaCode("");
          setMode("mfa");
        } else {
          navigate({ to: DashboardRoute.fullPath });
        }
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Invalid credentials";
      setError(msg);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-md flex-col items-center justify-center gap-6 px-4">
      <div className="flex flex-col gap-2 text-center">
        <h3 className="text-2xl font-medium text-primary">CrossBrowserTest</h3>
        <p className="text-base text-primary/60">
          {mode === "mfa"
            ? "Two-factor authentication"
            : mode === "login"
              ? "Sign in with your username"
              : "Create a unique account"}
        </p>
        {mode !== "mfa" ? (
          <p className="text-xs text-primary/40">
            Each username is unique. Your crawls, history, and reports are fully
            isolated from other users.
          </p>
        ) : null}
      </div>

      <form onSubmit={handleSubmit} className="flex w-full flex-col gap-4">
        {mode === "mfa" ? (
          <div className="flex flex-col gap-1.5">
            <label htmlFor="mfa-code" className="text-sm font-medium text-primary/80">
              Authentication code
            </label>
            <Input
              id="mfa-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              placeholder="000000"
              value={mfaCode}
              onChange={(e) => setMfaCode(e.target.value)}
              disabled={isSubmitting}
              autoFocus
            />
            <p className="text-xs text-primary/50">
              Enter the code from your authenticator app
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="username" className="text-sm font-medium text-primary/80">
                Username
              </label>
              <Input
                id="username"
                autoComplete="username"
                placeholder="your-username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={isSubmitting}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="password" className="text-sm font-medium text-primary/80">
                Password
              </label>
              <Input
                id="password"
                type="password"
                autoComplete={mode === "register" ? "new-password" : "current-password"}
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isSubmitting}
              />
            </div>
          </>
        )}

        {error ? (
          <p className="text-sm text-red-500" role="alert">
            {error}
          </p>
        ) : null}

        <Button type="submit" disabled={isSubmitting} className="w-full">
          {isSubmitting ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Please wait…
            </>
          ) : mode === "mfa" ? (
            "Verify"
          ) : mode === "login" ? (
            "Sign in"
          ) : (
            "Create account"
          )}
        </Button>

        {mode === "login" && oidcLoginUrl ? (
          <>
            <div className="flex items-center gap-3">
              <span className="h-px flex-1 bg-border" />
              <span className="text-xs text-primary/40">or</span>
              <span className="h-px flex-1 bg-border" />
            </div>
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => {
                window.location.href = `${API_BASE}${oidcLoginUrl}`;
              }}
            >
              Sign in with Google
            </Button>
          </>
        ) : null}
      </form>

      {mode === "mfa" ? (
        <p className="text-sm text-primary/60">
          <button
            type="button"
            className="font-medium text-sky-600 underline-offset-2 hover:underline dark:text-sky-400"
            onClick={() => {
              setMode("login");
              setMfaToken("");
              setMfaCode("");
              setError("");
            }}
          >
            Back
          </button>{" "}
          to sign in with your password
        </p>
      ) : (
        <p className="text-sm text-primary/60">
          {mode === "login" ? (
            <>
              No account?{" "}
              <button
                type="button"
                className="font-medium text-sky-600 underline-offset-2 hover:underline dark:text-sky-400"
                onClick={() => {
                  setMode("register");
                  setError("");
                }}
              >
                Register
              </button>
            </>
          ) : (
            <>
              Already registered?{" "}
              <button
                type="button"
                className="font-medium text-sky-600 underline-offset-2 hover:underline dark:text-sky-400"
                onClick={() => {
                  setMode("login");
                  setError("");
                }}
              >
                Sign in
              </button>
            </>
          )}
        </p>
      )}
    </div>
  );
}
