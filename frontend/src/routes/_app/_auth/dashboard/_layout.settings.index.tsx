import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Input } from "@/ui/input";
import { Button } from "@/ui/button";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getMe,
  updateMe,
  getMfaStatus,
  mfaSetup,
  mfaConfirm,
  mfaDisable,
} from "@/lib/api";
import { useForm } from "@tanstack/react-form";
import { Loader2, ShieldCheck } from "lucide-react";

import * as validators from "@/utils/validators";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/settings/")(
  {
    component: DashboardSettings,
    beforeLoad: () => ({
      title: "Settings",
      headerTitle: "Settings",
      headerDescription: "Manage your account settings.",
    }),
  },
);

export default function DashboardSettings() {
  const queryClient = useQueryClient();
  const { data: user, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: getMe,
  });

  const { mutateAsync: updateUsername } = useMutation({
    mutationFn: (data: { username: string }) => updateMe(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });

  const usernameForm = useForm({
    defaultValues: {
      username: user?.username || "",
    },
    onSubmit: async ({ value }) => {
      await updateUsername({ username: value.username || "" });
    },
  });

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-primary/60">
        Loading...
      </div>
    );
  }

  if (!user) {
    return null;
  }

  return (
    <div className="flex h-full w-full flex-col gap-6">
      {/* Username */}
      <form
        className="flex w-full flex-col items-start rounded-lg border border-border bg-card"
        onSubmit={(e) => {
          e.preventDefault();
          e.stopPropagation();
          usernameForm.handleSubmit();
        }}
      >
        <div className="flex w-full flex-col gap-4 rounded-lg p-6">
          <div className="flex flex-col gap-2">
            <h2 className="text-xl font-medium text-primary">Your Username</h2>
            <p className="text-sm font-normal text-primary/60">
              This is your username. It will be displayed on your profile.
            </p>
            <p className="text-xs text-primary/50">
              Role:{" "}
              <span className="font-medium text-primary/80">
                {user.role || "user"}
              </span>
              {user.email ? (
                <>
                  {" "}
                  · Email:{" "}
                  <span className="font-medium text-primary/80">
                    {user.email}
                  </span>
                </>
              ) : null}
              {user.role === "admin" ? (
                <span className="ml-2 text-amber-600 dark:text-amber-400">
                  (you can manage users under Users)
                </span>
              ) : (
                <span className="ml-2">
                  (you only see your own crawl jobs)
                </span>
              )}
            </p>
          </div>
          <usernameForm.Field
            name="username"
            validators={{
              onSubmit: ({ value }) => {
                const result = validators.username.safeParse(value);
                return result.success ? undefined : result.error.issues[0].message;
              },
            }}
            children={(field) => (
              <Input
                placeholder="Username"
                autoComplete="off"
                required
                value={field.state.value}
                onBlur={field.handleBlur}
                onChange={(e) => field.handleChange(e.target.value)}
                className={`w-80 bg-transparent ${
                  field.state.meta?.errors?.length > 0 &&
                  "border-destructive focus-visible:ring-destructive"
                }`}
              />
            )}
          />
          {usernameForm.state.fieldMeta.username?.errors && usernameForm.state.fieldMeta.username.errors.length > 0 && (
            <p className="text-sm text-destructive dark:text-destructive-foreground">
              {usernameForm.state.fieldMeta.username.errors.join(" ")}
            </p>
          )}
        </div>
        <div className="flex min-h-14 w-full items-center justify-between rounded-lg rounded-t-none border-t border-border bg-secondary px-6 dark:bg-card">
          <p className="text-sm font-normal text-primary/60">
            Please use 32 characters at maximum.
          </p>
          <Button type="submit" size="sm">
            Save
          </Button>
        </div>
      </form>

      <MfaCard />
    </div>
  );
}

function MfaCard() {
  const queryClient = useQueryClient();
  const [setupData, setSetupData] = useState<{
    secret: string;
    otpauth_uri: string;
    qr_url: string;
    message: string;
  } | null>(null);
  const [confirmCode, setConfirmCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const { data: status, isLoading } = useQuery({
    queryKey: ["mfa-status"],
    queryFn: getMfaStatus,
  });

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["mfa-status"] });

  const setupMut = useMutation({
    mutationFn: mfaSetup,
    onSuccess: (data) => {
      setSetupData(data);
      setConfirmCode("");
      setError("");
      setNotice("");
    },
    onError: (e: Error) => {
      setError(e.message || "Failed to start MFA setup");
    },
  });

  const confirmMut = useMutation({
    mutationFn: (code: string) => mfaConfirm(code),
    onSuccess: () => {
      setSetupData(null);
      setConfirmCode("");
      setError("");
      setNotice("MFA enabled");
      refresh();
    },
    onError: (e: Error) => {
      setError(e.message || "Invalid code");
    },
  });

  const disableMut = useMutation({
    mutationFn: ({ password, code }: { password: string; code: string }) =>
      mfaDisable(password, code),
    onSuccess: () => {
      setDisablePassword("");
      setDisableCode("");
      setError("");
      setNotice("MFA disabled");
      refresh();
    },
    onError: (e: Error) => {
      setError(e.message || "Failed to disable MFA");
    },
  });

  const mfaEnabled = status?.mfaEnabled ?? false;

  return (
    <section className="flex w-full flex-col items-start rounded-lg border border-border bg-card">
      <div className="flex w-full flex-col gap-4 rounded-lg p-6">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-primary/80" />
          <h2 className="text-xl font-medium text-primary">
            Two-factor authentication
          </h2>
        </div>

        {isLoading ? (
          <p className="text-sm text-primary/60">Loading…</p>
        ) : status && !status.mfaFeatureAvailable ? (
          <p className="text-sm text-primary/60">
            Two-factor authentication is disabled by the administrator.
          </p>
        ) : (
          <>
            <p className="text-sm text-primary/60">
              Status:{" "}
              <span
                className={`font-medium ${
                  mfaEnabled
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-primary/80"
                }`}
              >
                {mfaEnabled ? "Enabled" : "Disabled"}
              </span>
              {status?.authProvider ? (
                <>
                  {" "}
                  · Auth provider:{" "}
                  <span className="font-medium text-primary/80">
                    {status.authProvider}
                  </span>
                </>
              ) : null}
            </p>

            {error && <p className="text-sm text-red-500">{error}</p>}
            {notice && (
              <p className="text-sm text-emerald-600 dark:text-emerald-400">
                {notice}
              </p>
            )}

            {!mfaEnabled && !setupData ? (
              <div>
                <Button
                  type="button"
                  size="sm"
                  disabled={setupMut.isPending}
                  onClick={() => {
                    setError("");
                    setNotice("");
                    setupMut.mutate();
                  }}
                >
                  {setupMut.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : null}
                  Enable MFA
                </Button>
              </div>
            ) : null}

            {setupData ? (
              <div className="flex flex-col gap-4">
                <p className="text-sm text-primary/60">
                  Scan this QR code with your authenticator app, then enter the
                  6-digit code to confirm.
                </p>
                <img
                  src={setupData.qr_url}
                  alt="MFA QR code"
                  className="h-40 w-40 rounded-md border border-border bg-white p-2"
                />
                <div className="flex flex-col gap-1">
                  <p className="text-xs text-primary/50">
                    Or enter this secret manually:
                  </p>
                  <code className="w-fit rounded-md border border-border bg-secondary px-2 py-1 font-mono text-xs text-primary/80 select-all dark:bg-card">
                    {setupData.secret}
                  </code>
                </div>
                <form
                  className="flex items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    setError("");
                    confirmMut.mutate(confirmCode.trim());
                  }}
                >
                  <Input
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    placeholder="000000"
                    value={confirmCode}
                    onChange={(e) => setConfirmCode(e.target.value)}
                    className="w-40 bg-transparent"
                    required
                  />
                  <Button type="submit" size="sm" disabled={confirmMut.isPending}>
                    {confirmMut.isPending ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : null}
                    Confirm
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setSetupData(null);
                      setConfirmCode("");
                      setError("");
                    }}
                  >
                    Cancel
                  </Button>
                </form>
              </div>
            ) : null}

            {mfaEnabled ? (
              <form
                className="flex flex-col gap-3 sm:max-w-md"
                onSubmit={(e) => {
                  e.preventDefault();
                  setError("");
                  setNotice("");
                  disableMut.mutate({
                    password: disablePassword,
                    code: disableCode.trim(),
                  });
                }}
              >
                <p className="text-sm text-primary/60">
                  Enter your password and a current authenticator code to
                  disable MFA.
                </p>
                <Input
                  type="password"
                  placeholder="Password"
                  autoComplete="current-password"
                  value={disablePassword}
                  onChange={(e) => setDisablePassword(e.target.value)}
                  required
                />
                <Input
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  placeholder="000000"
                  value={disableCode}
                  onChange={(e) => setDisableCode(e.target.value)}
                  required
                />
                <Button
                  type="submit"
                  size="sm"
                  variant="destructive"
                  disabled={disableMut.isPending}
                  className="w-fit"
                >
                  {disableMut.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : null}
                  Disable MFA
                </Button>
              </form>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
