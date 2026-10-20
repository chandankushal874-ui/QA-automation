import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart3, RefreshCw, AlertTriangle, ShieldAlert, Timer } from "lucide-react";
import { Button } from "@/ui/button";
import {
  getAnalyticsTrends,
  getAnalyticsIssues,
  getAnalyticsTargets,
  getAnalyticsFlaky,
} from "@/lib/api";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/analytics")({
  component: AnalyticsPage,
  beforeLoad: () => ({
    title: "Analytics",
    headerTitle: "Cross-job analytics",
    headerDescription: "Quality trends across crawls: issues over time, severity mix, targets, flakiness.",
  }),
});

const SEV_COLORS: Record<string, string> = {
  blocking: "bg-red-500",
  functional: "bg-orange-500",
  visual: "bg-yellow-500",
  advisory: "bg-sky-500",
};

function AnalyticsPage() {
  const [days, setDays] = useState(30);

  const { data: trends, isLoading: tLoading, refetch } = useQuery({
    queryKey: ["analytics-trends", days],
    queryFn: () => getAnalyticsTrends(days),
  });
  const { data: issues } = useQuery({
    queryKey: ["analytics-issues", days],
    queryFn: () => getAnalyticsIssues(days),
  });
  const { data: targets } = useQuery({
    queryKey: ["analytics-targets", days],
    queryFn: () => getAnalyticsTargets(days),
  });
  const { data: flaky } = useQuery({
    queryKey: ["analytics-flaky", days],
    queryFn: () => getAnalyticsFlaky(days),
  });

  const series = trends?.series ?? [];
  const maxIssues = Math.max(1, ...series.map((s) => s.issues));
  const maxCrawls = Math.max(1, ...series.map((s) => s.crawls));
  const sevTotal = issues
    ? Object.values(issues.by_severity).reduce((a, b) => a + b, 0)
    : 0;

  return (
    <div className="flex h-full w-full bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl flex-col gap-6 overflow-y-auto">
        {/* Controls */}
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-medium text-primary">
            <BarChart3 className="h-5 w-5 text-primary/80" /> Last{" "}
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="rounded-md border border-border bg-secondary/50 px-2 py-1 text-sm text-primary/90 focus:outline-none"
            >
              {[7, 14, 30, 90].map((d) => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
          </h2>
          <Button variant="ghost" size="sm" onClick={() => refetch()}>
            <RefreshCw className="mr-1 h-4 w-4" /> Refresh
          </Button>
        </div>

        {/* Totals strip */}
        {trends && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Crawls" value={trends.totals.crawls} />
            <Stat label="Issues" value={trends.totals.issues} />
            <Stat label="Pages visited" value={trends.totals.pages_visited} />
            <Stat
              label="Pass rate"
              value={trends.totals.pass_rate == null ? "—" : `${Math.round(trends.totals.pass_rate * 100)}%`}
            />
          </div>
        )}

        {/* Issues-over-time chart (CSS bars) */}
        <section className="rounded-lg border border-border bg-card p-6">
          <h3 className="mb-4 text-sm font-medium text-primary/80">Issues & crawls per day</h3>
          {tLoading ? (
            <p className="text-sm text-primary/60">Loading…</p>
          ) : series.length === 0 ? (
            <p className="text-sm text-primary/60">No data in this window.</p>
          ) : (
            <div className="flex h-40 items-end gap-1">
              {series.map((s) => (
                <div key={s.date} className="group relative flex h-full flex-1 flex-col justify-end gap-[2px]">
                  {/* crawls bar (muted) */}
                  <div
                    className="w-full rounded-t bg-primary/15"
                    style={{ height: `${(s.crawls / maxCrawls) * 30}%` }}
                  />
                  {/* issues bar (severity stacked) */}
                  <div className="flex w-full flex-col-reverse">
                    {(["blocking", "functional", "visual", "advisory"] as const).map((sev) =>
                      s[sev] > 0 ? (
                        <div
                          key={sev}
                          className={`w-full ${SEV_COLORS[sev]}`}
                          style={{ height: `${Math.max(2, (s[sev] / maxIssues) * 60)}px` }}
                        />
                      ) : null,
                    )}
                  </div>
                  <div className="pointer-events-none absolute bottom-full left-1/2 z-10 hidden -translate-x-1/2 whitespace-nowrap rounded bg-primary px-2 py-1 text-[10px] text-primary-foreground group-hover:block">
                    {s.date}: {s.crawls} crawls, {s.issues} issues
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Severity mix */}
          <section className="rounded-lg border border-border bg-card p-6">
            <h3 className="mb-3 text-sm font-medium text-primary/80">Severity mix</h3>
            {issues && sevTotal > 0 ? (
              <div className="flex flex-col gap-3">
                <div className="flex h-4 w-full overflow-hidden rounded">
                  {(["blocking", "functional", "visual", "advisory"] as const).map((sev) =>
                    issues.by_severity[sev] ? (
                      <div
                        key={sev}
                        className={SEV_COLORS[sev]}
                        style={{ width: `${(issues.by_severity[sev] / sevTotal) * 100}%` }}
                      />
                    ) : null,
                  )}
                </div>
                <div className="flex flex-wrap gap-3 text-xs">
                  {(["blocking", "functional", "visual", "advisory"] as const).map((sev) => (
                    <span key={sev} className="flex items-center gap-1.5 text-primary/70">
                      <span className={`h-2.5 w-2.5 rounded-sm ${SEV_COLORS[sev]}`} />
                      {sev}: {issues.by_severity[sev] ?? 0}
                    </span>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-sm text-primary/60">No issues in this window. 🎉</p>
            )}

            {/* Categories */}
            {issues && Object.keys(issues.by_category).length > 0 && (
              <>
                <h4 className="mb-2 mt-5 text-xs font-medium uppercase tracking-wide text-primary/50">
                  By category
                </h4>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(issues.by_category)
                    .sort((a, b) => b[1] - a[1])
                    .map(([cat, n]) => (
                      <span key={cat} className="rounded bg-secondary px-2 py-1 text-xs text-primary/70">
                        {cat}: {n}
                      </span>
                    ))}
                </div>
              </>
            )}
          </section>

          {/* Flaky */}
          <section className="rounded-lg border border-border bg-card p-6">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-primary/80">
              <Timer className="h-4 w-4 text-violet-500" /> Flakiness
            </h3>
            {flaky && flaky.totals.registry_size > 0 ? (
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap gap-3 text-sm text-primary/70">
                  <span>Registry: <b>{flaky.totals.registry_size}</b></span>
                  <span className="text-violet-500">Quarantined: <b>{flaky.totals.quarantined}</b></span>
                  <span>Suspected: <b>{flaky.totals.suspected}</b></span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-border text-primary/50">
                        <th className="py-1.5 pr-2 font-medium">Fingerprint</th>
                        <th className="py-1.5 pr-2 font-medium">Flip rate</th>
                        <th className="py-1.5 font-medium">Samples</th>
                      </tr>
                    </thead>
                    <tbody>
                      {flaky.top_flaky.slice(0, 5).map((f) => (
                        <tr key={f.fingerprint} className="border-b border-border/50">
                          <td className="max-w-[180px] truncate py-1.5 pr-2 text-primary/80" title={f.title}>{f.title || f.fingerprint}</td>
                          <td className="py-1.5 pr-2 font-mono text-violet-500">{Math.round(f.flip_rate * 100)}%</td>
                          <td className="py-1.5 text-primary/60">{f.samples}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <p className="text-sm text-primary/60">No flaky fingerprints recorded.</p>
            )}
          </section>
        </div>

        {/* Top recurring issues */}
        {issues && issues.top_recurring.length > 0 && (
          <section className="rounded-lg border border-border bg-card p-6">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-primary/80">
              <ShieldAlert className="h-4 w-4 text-orange-500" /> Top recurring issues
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-primary/50">
                    <th className="py-2 pr-3 font-medium">Issue</th>
                    <th className="py-2 pr-3 font-medium">Severity</th>
                    <th className="py-2 pr-3 font-medium">Times seen</th>
                    <th className="py-2 font-medium">Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {issues.top_recurring.map((t) => (
                    <tr key={t.fingerprint} className="border-b border-border/50">
                      <td className="max-w-[320px] truncate py-2 pr-3 text-primary/80" title={t.title}>{t.title}</td>
                      <td className="py-2 pr-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                          t.severity === "blocking" ? "bg-red-500/15 text-red-500" :
                          t.severity === "functional" ? "bg-orange-500/15 text-orange-500" :
                          t.severity === "visual" ? "bg-yellow-500/15 text-yellow-600" :
                          "bg-sky-500/15 text-sky-500"
                        }`}>{t.severity}</span>
                      </td>
                      <td className="py-2 pr-3 font-medium text-primary/80">{t.occurrences}</td>
                      <td className="py-2 text-xs text-primary/50">{t.last_seen?.slice(0, 10) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Targets */}
        <section className="rounded-lg border border-border bg-card p-6">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-primary/80">
            <AlertTriangle className="h-4 w-4 text-sky-500" /> Targets ({targets?.total ?? 0})
          </h3>
          {targets && targets.targets.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-primary/50">
                    <th className="py-2 pr-3 font-medium">Target</th>
                    <th className="py-2 pr-3 font-medium">Crawls</th>
                    <th className="py-2 pr-3 font-medium">Issues</th>
                    <th className="py-2 pr-3 font-medium">Avg/crawl</th>
                    <th className="py-2 font-medium">Last status</th>
                  </tr>
                </thead>
                <tbody>
                  {targets.targets.map((t) => (
                    <tr key={t.url} className="border-b border-border/50">
                      <td className="max-w-[280px] truncate py-2 pr-3 text-primary/80">{t.url}</td>
                      <td className="py-2 pr-3 text-primary/70">{t.crawls}</td>
                      <td className="py-2 pr-3 font-medium text-primary/80">{t.issues}</td>
                      <td className="py-2 pr-3 text-primary/70">{t.avg_issues_per_crawl}</td>
                      <td className="py-2">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                          t.last_crawl_status === "completed"
                            ? "bg-emerald-500/15 text-emerald-500"
                            : t.last_crawl_status === "failed"
                              ? "bg-red-500/15 text-red-500"
                              : "bg-primary/10 text-primary/60"
                        }`}>{t.last_crawl_status ?? "—"}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-primary/60">No crawls in this window.</p>
          )}
        </section>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <p className="text-xs uppercase tracking-wide text-primary/50">{label}</p>
      <p className="text-2xl font-bold text-primary">{value}</p>
    </div>
  );
}