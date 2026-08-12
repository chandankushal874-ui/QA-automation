import { createFileRoute, redirect } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, UserPlus, Shield, User } from "lucide-react";
import { Button } from "@/ui/button";
import { Input } from "@/ui/input";
import { createPlatformUser, getMe, listPlatformUsers } from "@/lib/api";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/users")({
  component: UsersAdminPage,
  beforeLoad: async () => {
    try {
      const me = await getMe();
      if (me?.role !== "admin") {
        throw redirect({ to: "/dashboard" });
      }
    } catch (e) {
      if (e && typeof e === "object" && "to" in e) throw e;
      throw redirect({ to: "/login" });
    }
    return {
      title: "Users",
      headerTitle: "User management",
      headerDescription:
        "Admin only — create platform users. Each user only sees their own crawl jobs.",
    };
  },
});

function UsersAdminPage() {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["platform-users"],
    queryFn: listPlatformUsers,
  });

  const createMut = useMutation({
    mutationFn: createPlatformUser,
    onSuccess: () => {
      setSuccess(`User "${username}" created.`);
      setError("");
      setUsername("");
      setPassword("");
      setEmail("");
      setRole("user");
      queryClient.invalidateQueries({ queryKey: ["platform-users"] });
    },
    onError: (e: Error) => {
      setSuccess("");
      setError(e.message || "Failed to create user");
    },
  });

  return (
    <div className="mx-auto flex w-full max-w-screen-xl flex-col gap-8 px-6 py-8">
      {/* Create user */}
      <section className="rounded-lg border border-border bg-card p-6">
        <div className="mb-4 flex items-center gap-2">
          <UserPlus className="h-5 w-5 text-primary/80" />
          <h2 className="text-lg font-medium text-primary">Create user</h2>
        </div>
        <p className="mb-4 text-sm text-primary/60">
          New users get their own isolated job history. They cannot see other
          users&apos; crawls. Admins can see all jobs.
        </p>
        <form
          className="flex flex-col gap-3 sm:max-w-md"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            setSuccess("");
            createMut.mutate({
              username: username.trim(),
              password,
              email: email.trim() || undefined,
              role,
            });
          }}
        >
          <Input
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={2}
            autoComplete="off"
          />
          <Input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={4}
            autoComplete="new-password"
          />
          <Input
            type="email"
            placeholder="Email (optional)"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="off"
          />
          <div className="flex gap-3 text-sm text-primary/80">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="role"
                checked={role === "user"}
                onChange={() => setRole("user")}
              />
              Regular user
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="role"
                checked={role === "admin"}
                onChange={() => setRole("admin")}
              />
              Admin
            </label>
          </div>
          {error && <p className="text-sm text-red-500">{error}</p>}
          {success && <p className="text-sm text-emerald-600">{success}</p>}
          <Button type="submit" disabled={createMut.isPending} className="w-fit">
            {createMut.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : null}
            Create user
          </Button>
        </form>
      </section>

      {/* User list */}
      <section className="rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-medium text-primary">
          Platform users ({data?.total ?? 0})
        </h2>
        {isLoading ? (
          <p className="text-sm text-primary/60">Loading…</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border text-primary/60">
                  <th className="py-2 pr-4 font-medium">Username</th>
                  <th className="py-2 pr-4 font-medium">Email</th>
                  <th className="py-2 pr-4 font-medium">Role</th>
                  <th className="py-2 font-medium">ID</th>
                </tr>
              </thead>
              <tbody>
                {(data?.users ?? []).map((u) => (
                  <tr key={u.id} className="border-b border-border/50">
                    <td className="py-2.5 pr-4 font-medium text-primary/90">
                      {u.username}
                    </td>
                    <td className="py-2.5 pr-4 text-primary/70">{u.email}</td>
                    <td className="py-2.5 pr-4">
                      <span
                        className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                          u.role === "admin"
                            ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                            : "bg-sky-500/15 text-sky-600 dark:text-sky-400"
                        }`}
                      >
                        {u.role === "admin" ? (
                          <Shield className="h-3 w-3" />
                        ) : (
                          <User className="h-3 w-3" />
                        )}
                        {u.role}
                      </span>
                    </td>
                    <td className="py-2.5 font-mono text-xs text-primary/40">
                      {u.id.slice(0, 8)}…
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
