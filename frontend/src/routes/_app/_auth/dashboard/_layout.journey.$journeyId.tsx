import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, PlayCircle, CheckCircle2, XCircle, Route as RouteIcon } from "lucide-react";
import { Button } from "@/ui/button";
import { getJourney, getJourneyRun, runJourney } from "@/lib/api";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/journey/$journeyId")({
  component: JourneyDetailPage,
  beforeLoad: () => ({
    title: "Journey",
    headerTitle: "Multi-step journey",
    headerDescription: "Run the flow and review per-step results.",
  }),
});

function JourneyDetailPage() {
  const { journeyId } = Route.useParams();
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const { data: journey, isLoading } = useQuery({
    queryKey: ["journey", journeyId],
    queryFn: () => getJourney(journeyId),
  });

  const { data: run } = useQuery({
    queryKey: ["journey-run", journeyId, runId],
    queryFn: () => getJourneyRun(journeyId, runId!),
    enabled: !!runId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "queued" || s === "running" ? 2000 : false;
    },
  });

  const runMut = useMutation({
    mutationFn: () => runJourney(journeyId),
    onSuccess: (res) => {
      setRunId(res.run_id);
      setError("");
      queryClient.invalidateQueries({ queryKey: ["journey", journeyId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  if (isLoading) {
    return <div className="flex h-full items-center justify-center p-8 text-primary/60">Loading…</div>;
  }
  if (!journey) {
    return <div className="p-8 text-primary/60">Journey not found.</div>;
  }

  const statusColor =
    run?.status === "passed"
      ? "text-emerald-500"
      : run?.status === "failed"
        ? "text-red-500"
        : "text-primary/60";

  return (
    <div className="flex h-full w-full flex-col gap-6 bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl flex-col gap-6">
        <section className="rounded-lg border border-border bg-card p-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="text-xl font-medium text-primary">{journey.name}</h2>
              <p className="text-sm text-primary/50">
                {journey.steps?.length ?? 0} steps · status{" "}
                <span className={statusColor}>{run?.status ?? journey.status}</span>
              </p>
            </div>
            <Button disabled={runMut.isPending} onClick={() => runMut.mutate()}>
              {runMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlayCircle className="mr-2 h-4 w-4" />}
              Run journey
            </Button>
          </div>
          {error && <p className="mt-3 text-sm text-red-500">{error}</p>}
        </section>

        {/* Steps */}
        <section className="rounded-lg border border-border bg-card p-6">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-primary/80">
            <RouteIcon className="h-4 w-4" /> Steps
          </h3>
          <div className="flex flex-col gap-1">
            {journey.steps?.map((s, i) => (
              <div key={i} className="flex items-center gap-2 font-mono text-xs text-primary/70">
                <span className="w-6 text-right text-primary/40">{i}</span>
                <span className="rounded bg-secondary px-2 py-0.5 font-semibold text-primary/80">{s.action}</span>
                {s.url && <span className="truncate">{s.url}</span>}
                {s.selector && <span className="truncate">{s.selector}</span>}
                {s.text && <span className="truncate">"{s.text}"</span>}
              </div>
            ))}
          </div>
        </section>

        {/* Run results */}
        {run && (
          <section className="rounded-lg border border-border bg-card p-6">
            <h3 className="mb-3 text-sm font-medium text-primary/80">Run results</h3>
            {Object.entries(run.results || {}).map(([browser, r]) => {
              const res = r as { passed: boolean; error?: string; steps: Array<{ index: number; action: string; status: string; error: string; duration_ms: number }> };
              return (
                <div key={browser} className="mb-4">
                  <p className="mb-2 flex items-center gap-2 text-sm font-medium text-primary/80">
                    {res.passed ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <XCircle className="h-4 w-4 text-red-500" />}
                    {browser}
                  </p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-border text-primary/50">
                          <th className="py-2 pr-3 font-medium">#</th>
                          <th className="py-2 pr-3 font-medium">Action</th>
                          <th className="py-2 pr-3 font-medium">Status</th>
                          <th className="py-2 pr-3 font-medium">Duration</th>
                          <th className="py-2 font-medium">Error</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(res.steps || []).map((st) => (
                          <tr key={st.index} className="border-b border-border/50">
                            <td className="py-2 pr-3 text-primary/60">{st.index}</td>
                            <td className="py-2 pr-3 font-mono text-xs text-primary/80">{st.action}</td>
                            <td className="py-2 pr-3">
                              <span className={`rounded px-2 py-0.5 text-xs font-semibold ${
                                st.status === "passed" ? "bg-emerald-500/15 text-emerald-500" :
                                st.status === "error" ? "bg-red-500/15 text-red-500" :
                                st.status === "skipped" ? "bg-zinc-500/15 text-zinc-500" : "bg-yellow-500/15 text-yellow-500"
                              }`}>
                                {st.status}
                              </span>
                            </td>
                            <td className="py-2 pr-3 text-primary/60">{st.duration_ms}ms</td>
                            <td className="max-w-[300px] truncate py-2 font-mono text-xs text-red-400">{st.error}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })}
          </section>
        )}
      </div>
    </div>
  );
}
