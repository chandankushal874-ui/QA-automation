import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, PlusCircle, PlayCircle, PauseCircle, Trash2, RefreshCw, ExternalLink } from "lucide-react";
import { Button } from "@/ui/button";
import { Input } from "@/ui/input";
import {
  createMonitor,
  deleteMonitor,
  listMonitors,
  runMonitorNow,
  updateMonitor,
  type MonitorRow,
} from "@/lib/api";
import { Route as ReportRoute } from "@/routes/_app/_auth/dashboard/_layout.report.$testJobId";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/monitors")({
  component: MonitorsPage,
  beforeLoad: () => ({
    title: "Monitors",
    headerTitle: "Scheduled monitors",
    headerDescription:
      "Recurring cross-browser crawls. Each run is tenant-isolated and respects your slot limits.",
  }),
});

function MonitorsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [intervalMin, setIntervalMin] = useState(60);
  const [notifyUrl, setNotifyUrl] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["monitors"],
    queryFn: listMonitors,
    refetchInterval: 10_000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["monitors"] });

  const createMut = useMutation({
    mutationFn: createMonitor,
    onSuccess: () => {
      setNotice(`Monitor "${name}" created.`);
      setError("");
      setName(""); setUrl(""); setNotifyUrl("");
      invalidate();
    },
    onError: (e: Error) => { setNotice(""); setError(e.message); },
  });

  const toggleMut = useMutation({
    mutationFn: (m: MonitorRow) => updateMonitor(m.id, { is_active: !m.is_active }),
    onSuccess: invalidate,
  });

  const runNowMut = useMutation({
    mutationFn: (m: MonitorRow) => runMonitorNow(m.id),
    onSuccess: (res) => {
      if (res.submitted && res.job_id) {
        setNotice(`Crawl submitted (${res.job_id.slice(0, 8)}) — opening report…`);
        navigate({ to: ReportRoute.fullPath, params: { testJobId: res.job_id } });
      } else {
        setNotice(res.error || "Slots full — run queued later or refused.");
      }
    },
    onError: (e: Error) => setError(e.message),
  });

  const deleteMut = useMutation({
    mutationFn: (m: MonitorRow) => deleteMonitor(m.id),
    onSuccess: invalidate,
  });

  const monitors = data?.monitors ?? [];

  return (
    <div className="flex h-full w-full bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl flex-col gap-8">
        {/* Create form */}
        <section className="rounded-lg border border-border bg-card p-6">
          <h2 className="mb-1 flex items-center gap-2 text-lg font-medium text-primary">
            <PlusCircle className="h-5 w-5 text-primary/80" /> New monitor
          </h2>
          <p className="mb-4 text-sm text-primary/60">
            Crawls the URL on a schedule, saves reports, optionally notifies a
            Slack-compatible webhook. Runs use your normal crawler slots.
          </p>
          <form
            className="flex flex-col gap-3 md:max-w-2xl"
            onSubmit={(e) => {
              e.preventDefault();
              setError(""); setNotice("");
              if (!name.trim() || !url.trim()) return;
              createMut.mutate({
                name: name.trim(),
                url: url.trim(),
                interval_minutes: intervalMin,
                notify_url: notifyUrl.trim() || undefined,
              });
            }}
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Input placeholder="Name (e.g. nightly prod)" value={name} onChange={(e) => setName(e.target.value)} required />
              <Input type="url" placeholder="https://example.com" value={url} onChange={(e) => setUrl(e.target.value)} required />
              <Input
                type="number" min={10} max={10080}
                placeholder="Every N minutes (min 10)"
                value={intervalMin}
                onChange={(e) => setIntervalMin(Number(e.target.value) || 60)}
                required
              />
              <Input
                type="url"
                placeholder="Slack webhook (optional)"
                value={notifyUrl}
                onChange={(e) => setNotifyUrl(e.target.value)}
              />
            </div>
            {error && <p className="text-sm text-red-500">{error}</p>}
            {notice && <p className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}
            <Button type="submit" disabled={createMut.isPending} className="w-fit">
              {createMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Create monitor
            </Button>
          </form>
        </section>

        {/* List */}
        <section className="rounded-lg border border-border bg-card p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-medium text-primary">
              Your monitors ({data?.total ?? 0})
            </h2>
            <Button variant="ghost" size="sm" onClick={() => refetch()}>
              <RefreshCw className="mr-1 h-4 w-4" /> Refresh
            </Button>
          </div>
          {isLoading ? (
            <p className="text-sm text-primary/60">Loading…</p>
          ) : monitors.length === 0 ? (
            <p className="text-sm text-primary/60">
              No monitors yet. Create one above to schedule recurring crawls.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-primary/60">
                    <th className="py-2 pr-3 font-medium">Name</th>
                    <th className="py-2 pr-3 font-medium">URL</th>
                    <th className="py-2 pr-3 font-medium">Every</th>
                    <th className="py-2 pr-3 font-medium">Status</th>
                    <th className="py-2 pr-3 font-medium">Next run</th>
                    <th className="py-2 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {monitors.map((m) => (
                    <tr key={m.id} className="border-b border-border/50">
                      <td className="py-2.5 pr-3 font-medium text-primary/90">{m.name}</td>
                      <td className="max-w-[220px] truncate py-2.5 pr-3 text-primary/70">
                        <a href={m.url} target="_blank" rel="noreferrer" className="underline decoration-primary/30">
                          {m.url}
                        </a>
                      </td>
                      <td className="py-2.5 pr-3 text-primary/70">{m.interval_minutes}m</td>
                      <td className="py-2.5 pr-3">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                            m.is_active
                              ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                              : "bg-zinc-500/15 text-zinc-500"
                          }`}
                        >
                          {m.is_active ? "active" : "paused"}
                        </span>
                      </td>
                      <td className="py-2.5 pr-3 text-xs text-primary/60">
                        {m.next_run_at ? new Date(m.next_run_at).toLocaleString() : "—"}
                      </td>
                      <td className="py-2.5">
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost" size="sm" title="Run now"
                            onClick={() => runNowMut.mutate(m)}
                            disabled={runNowMut.isPending && runNowMut.variables?.id === m.id}
                          >
                            {runNowMut.isPending && runNowMut.variables?.id === m.id ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <PlayCircle className="h-4 w-4" />
                            )}
                          </Button>
                          <Button
                            variant="ghost" size="sm"
                            title={m.is_active ? "Pause" : "Resume"}
                            onClick={() => toggleMut.mutate(m)}
                          >
                            {m.is_active ? (
                              <PauseCircle className="h-4 w-4" />
                            ) : (
                              <PlayCircle className="h-4 w-4" />
                            )}
                          </Button>
                          {m.last_job_id ? (
                            <Button
                              variant="ghost" size="sm" title="Open last run's report"
                              onClick={() =>
                                navigate({
                                  to: ReportRoute.fullPath,
                                  params: { testJobId: m.last_job_id! },
                                })
                              }
                            >
                              <ExternalLink className="h-4 w-4" />
                            </Button>
                          ) : null}
                          <Button
                            variant="ghost" size="sm" title="Delete"
                            className="text-red-500 hover:text-red-600"
                            onClick={() => {
                              if (confirm(`Delete monitor "${m.name}"?`)) deleteMut.mutate(m);
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}