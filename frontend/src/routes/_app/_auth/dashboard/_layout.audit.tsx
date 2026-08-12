import { createFileRoute, redirect } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Loader2, ScrollText } from "lucide-react";
import { Button } from "@/ui/button";
import { Input } from "@/ui/input";
import { getMe, listAuditEvents } from "@/lib/api";

const PAGE_SIZE = 50;

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/audit")({
  component: AuditAdminPage,
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
      title: "Audit",
      headerTitle: "Audit log",
      headerDescription:
        "Admin only — authentication and authorization events across the platform.",
    };
  },
});

function AuditAdminPage() {
  const [offset, setOffset] = useState(0);
  const [actionInput, setActionInput] = useState("");
  const [actionFilter, setActionFilter] = useState("");

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["audit-events", offset, actionFilter],
    queryFn: () =>
      listAuditEvents({
        limit: PAGE_SIZE,
        offset,
        action: actionFilter || undefined,
      }),
    placeholderData: keepPreviousData,
  });

  const total = data?.total ?? 0;
  const events = data?.events ?? [];
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <div className="mx-auto flex w-full max-w-screen-xl flex-col gap-8 px-6 py-8">
      <section className="rounded-lg border border-border bg-card p-6">
        <div className="mb-4 flex items-center gap-2">
          <ScrollText className="h-5 w-5 text-primary/80" />
          <h2 className="text-lg font-medium text-primary">
            Audit events ({total})
          </h2>
          {isFetching && !isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-primary/40" />
          ) : null}
        </div>

        <form
          className="mb-4 flex items-center gap-2 sm:max-w-md"
          onSubmit={(e) => {
            e.preventDefault();
            setOffset(0);
            setActionFilter(actionInput.trim());
          }}
        >
          <Input
            placeholder="Filter by action (e.g. login, mfa.enable)"
            value={actionInput}
            onChange={(e) => setActionInput(e.target.value)}
            autoComplete="off"
          />
          <Button type="submit" size="sm">
            Filter
          </Button>
          {actionFilter ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setActionInput("");
                setActionFilter("");
                setOffset(0);
              }}
            >
              Clear
            </Button>
          ) : null}
        </form>

        {isLoading ? (
          <p className="text-sm text-primary/60">Loading…</p>
        ) : events.length === 0 ? (
          <p className="text-sm text-primary/60">No audit events found.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border text-primary/60">
                  <th className="py-2 pr-4 font-medium">Time</th>
                  <th className="py-2 pr-4 font-medium">Actor</th>
                  <th className="py-2 pr-4 font-medium">Action</th>
                  <th className="py-2 pr-4 font-medium">Resource</th>
                  <th className="py-2 pr-4 font-medium">IP</th>
                  <th className="py-2 pr-4 font-medium">Result</th>
                  <th className="py-2 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {events.map((ev) => (
                  <tr key={ev.id} className="border-b border-border/50 align-top">
                    <td className="py-2.5 pr-4 whitespace-nowrap text-primary/70">
                      {ev.created_at
                        ? new Date(ev.created_at).toLocaleString()
                        : "—"}
                    </td>
                    <td className="py-2.5 pr-4 font-medium text-primary/90">
                      {ev.actor_username || "—"}
                    </td>
                    <td className="py-2.5 pr-4 text-primary/80">{ev.action}</td>
                    <td className="py-2.5 pr-4 text-primary/70">
                      {ev.resource_type
                        ? `${ev.resource_type}${
                            ev.resource_id ? `/${ev.resource_id.slice(0, 8)}…` : ""
                          }`
                        : "—"}
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-xs text-primary/50">
                      {ev.ip_address || "—"}
                    </td>
                    <td className="py-2.5 pr-4">
                      <span
                        className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                          ev.success
                            ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                            : "bg-red-500/15 text-red-600 dark:text-red-400"
                        }`}
                      >
                        {ev.success ? "success" : "failure"}
                      </span>
                    </td>
                    <td className="max-w-64 py-2.5">
                      {ev.detail ? (
                        <code className="block truncate font-mono text-xs text-primary/50">
                          {typeof ev.detail === "string"
                            ? ev.detail
                            : JSON.stringify(ev.detail)}
                        </code>
                      ) : (
                        <span className="text-primary/40">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-4 flex items-center justify-between">
          <p className="text-sm text-primary/60">
            {total > 0
              ? `Showing ${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}`
              : ""}
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!hasPrev}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Prev
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!hasNext}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
