import { createFileRoute, Link } from "@tanstack/react-router";
import { Plus, ExternalLink, Bug, Globe, Activity, CheckCircle, Clock } from "lucide-react";
import { cn } from "@/utils/misc.js";
import { buttonVariants } from "@/ui/button-util";
import siteConfig from "~/site.config";
import { useQuery } from "@tanstack/react-query";
import { getDashboardStats, listJobs, getMetrics, getCrawlerSlots } from "@/lib/api";
import { Route as NewTestRoute } from "@/routes/_app/_auth/dashboard/_layout.new-test";
import { Route as HistoryRoute } from "@/routes/_app/_auth/dashboard/_layout.history";
import { Route as ReportRoute } from "@/routes/_app/_auth/dashboard/_layout.report.$testJobId";

const API_BASE = import.meta.env.VITE_API_URL || "";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/")({
  component: Dashboard,
  beforeLoad: () => ({
    title: `${siteConfig.siteTitle} - Dashboard`,
    headerTitle: "Dashboard",
    headerDescription: "Monitor your cross-browser tests and discovered issues.",
  }),
});

export default function Dashboard() {
  const { data: stats } = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: getDashboardStats,
    refetchInterval: 10_000,
  });

  const { data: recentJobs } = useQuery({
    queryKey: ["recent-jobs"],
    queryFn: () => listJobs({ limit: 5 }),
    refetchInterval: 5_000,
  });

  const { data: telemetry } = useQuery({
    queryKey: ["backend-metrics"],
    queryFn: getMetrics,
    refetchInterval: 5_000,
  });

  const { data: slots } = useQuery({
    queryKey: ["crawler-slots"],
    queryFn: getCrawlerSlots,
    refetchInterval: 4_000,
  });

  const jobs = recentJobs?.jobs || [];

  return (
    <div className="flex h-full w-full bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl flex-col gap-8">
        {/* Per-user crawler slots */}
        <div className="flex flex-col gap-2 rounded-lg border border-sky-500/30 bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-medium text-primary">
              Your isolated crawlers:{" "}
              <span className="font-bold text-sky-600 dark:text-sky-400">
                {slots?.active ?? 0}
                {slots?.maxConcurrent != null ? ` / ${slots.maxConcurrent}` : " / ∞"}
              </span>{" "}
              active
            </p>
            <p className="text-xs text-primary/60">
              Up to 2 simultaneous crawls per user — each job is private to you.
              Other users cannot see or affect your crawls.
            </p>
          </div>
          <Link
            to={NewTestRoute.fullPath}
            className={cn(
              buttonVariants({ size: "sm" }),
              !slots?.canStartMore && "pointer-events-none opacity-50",
            )}
          >
            <Plus className="mr-1 h-4 w-4" />
            {slots?.canStartMore ? "Start crawl" : "Slots full"}
          </Link>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-6">
            <div className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-blue-500" />
              <span className="text-sm font-medium text-primary/60">Total Tests</span>
            </div>
            <span className="text-3xl font-bold text-primary">{stats?.totalJobs || 0}</span>
          </div>
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-6">
            <div className="flex items-center gap-2">
              <CheckCircle className="h-5 w-5 text-green-500" />
              <span className="text-sm font-medium text-primary/60">Completed</span>
            </div>
            <span className="text-3xl font-bold text-primary">{stats?.completedJobs || 0}</span>
          </div>
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-6">
            <div className="flex items-center gap-2">
              <Bug className="h-5 w-5 text-red-500" />
              <span className="text-sm font-medium text-primary/60">Issues Found</span>
            </div>
            <span className="text-3xl font-bold text-primary">{stats?.totalIssues || 0}</span>
          </div>
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-6">
            <div className="flex items-center gap-2">
              <Globe className="h-5 w-5 text-purple-500" />
              <span className="text-sm font-medium text-primary/60">Pages Crawled</span>
            </div>
            <span className="text-3xl font-bold text-primary">{stats?.totalPages || 0}</span>
          </div>
        </div>

        {/* Telemetry Observability Widget */}
        <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-6">
          <h2 className="text-xl font-medium text-primary">System Telemetry & Queue Health</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="flex flex-col gap-1 rounded-md border border-border/60 bg-secondary/50 p-4 dark:bg-black/30">
              <span className="text-xs font-semibold uppercase tracking-wider text-primary/40">Queue Depth (RQ Backlog)</span>
              <span className="text-2xl font-bold text-blue-500">{telemetry !== undefined && telemetry !== null ? telemetry.queue_depth : "Offline"}</span>
            </div>
            <div className="flex flex-col gap-1 rounded-md border border-border/60 bg-secondary/50 p-4 dark:bg-black/30">
              <span className="text-xs font-semibold uppercase tracking-wider text-primary/40">Active Worker Instances</span>
              <span className="text-2xl font-bold text-green-500">{telemetry !== undefined && telemetry !== null ? telemetry.active_workers : "Offline"}</span>
            </div>
            <div className="flex flex-col gap-1 rounded-md border border-border/60 bg-secondary/50 p-4 dark:bg-black/30">
              <span className="text-xs font-semibold uppercase tracking-wider text-primary/40">Active Running Crawls</span>
              <span className="text-2xl font-bold text-purple-500">{telemetry !== undefined && telemetry !== null ? telemetry.active_crawls : "Offline"}</span>
            </div>
          </div>
        </div>

        {/* Quick Actions */}
        <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-6">
          <h2 className="text-xl font-medium text-primary">Quick Actions</h2>
          <div className="flex flex-wrap gap-4">
            <Link
              to={NewTestRoute.fullPath}
              className={cn(
                buttonVariants({ variant: "default", size: "lg" }),
                "gap-2",
              )}
            >
              <Plus className="h-5 w-5" />
              New Cross-Browser Test
            </Link>
            <Link
              to={HistoryRoute.fullPath}
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "gap-2 bg-transparent",
              )}
            >
              <Clock className="h-5 w-5" />
              View Test History
            </Link>
            <a
              href="http://localhost:9181"
              target="_blank"
              rel="noreferrer"
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "gap-2 bg-transparent border-primary/20 hover:bg-primary/5",
              )}
            >
              <ExternalLink className="h-5 w-5" />
              Open RQ Dashboard
            </a>
          </div>
        </div>

        {/* Recent Tests */}
        <div className="flex flex-col rounded-lg border border-border bg-card">
          <div className="flex items-center justify-between p-6 pb-0">
            <h2 className="text-xl font-medium text-primary">Recent Tests</h2>
            <Link
              to={HistoryRoute.fullPath}
              className="text-sm text-primary/60 hover:text-primary"
            >
              View All
            </Link>
          </div>
          <div className="p-6">
            {jobs.length > 0 ? (
              <div className="flex flex-col gap-3">
                {jobs.map((job: any) => (
                  <div
                    key={job.id}
                    className="flex items-center justify-between rounded-md border border-border p-4 hover:border-primary/40 transition-colors bg-card"
                  >
                    <Link
                      to={ReportRoute.fullPath}
                      params={{ testJobId: job.id }}
                      className="flex flex-col gap-1 flex-1"
                    >
                      <span className="text-sm font-medium text-primary truncate max-w-md">
                        {job.url}
                      </span>
                      <span className="text-xs text-primary/60">
                        {new Date(job.created_at).toLocaleString()}
                      </span>
                    </Link>
                    <div className="flex items-center gap-3">
                      {(job.status === "cancelled" || job.status === "failed") && (
                        <button
                          onClick={async (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            try {
                              const res = await fetch(`${API_BASE}/jobs/${job.id}/resume`, { method: "POST" });
                              if (res.ok) {
                                alert("Job crawl resumed successfully!");
                                window.location.reload();
                              } else {
                                const details = await res.text();
                                alert(`Failed to resume: ${details}`);
                              }
                            } catch (err) {
                              alert("Backend offline. Could not resume job.");
                            }
                          }}
                          className={cn(buttonVariants({ variant: "outline", size: "sm" }), "text-xs h-7 px-2 border-primary/20 hover:bg-primary/5")}
                        >
                          Resume
                        </button>
                      )}
                      <span className={cn(
                        "flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
                        job.status === "completed" && "bg-green-500/10 text-green-600",
                        job.status === "running" && "bg-blue-500/10 text-blue-600",
                        job.status === "failed" && "bg-red-500/10 text-red-600",
                        job.status === "pending" && "bg-yellow-500/10 text-yellow-600",
                        job.status === "cancelled" && "bg-gray-500/10 text-gray-600",
                      )}>
                        {job.status}
                      </span>
                      <span className="text-xs text-primary/60">
                        {job.issues_found} issues
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="relative flex w-full flex-col items-center justify-center gap-6 overflow-hidden rounded-lg border border-border bg-secondary px-6 py-24 dark:bg-card">
                <div className="z-10 flex max-w-[460px] flex-col items-center gap-4">
                  <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-primary/20 bg-card hover:border-primary/40">
                    <Plus className="h-8 w-8 stroke-[1.5px] text-primary/60" />
                  </div>
                  <div className="flex flex-col items-center gap-2">
                    <p className="text-base font-medium text-primary">
                      No tests yet
                    </p>
                    <p className="text-center text-base font-normal text-primary/60">
                      Start your first cross-browser test to discover bugs across Chromium, Firefox, and WebKit.
                    </p>
                  </div>
                </div>
                <div className="z-10 flex items-center justify-center">
                  <Link
                    to={NewTestRoute.fullPath}
                    className={cn(
                      `${buttonVariants({ variant: "ghost", size: "sm" })} gap-2`,
                    )}
                  >
                    <span className="text-sm font-medium text-primary/60 group-hover:text-primary">
                      Start New Test
                    </span>
                    <ExternalLink className="h-4 w-4 stroke-[1.5px] text-primary/60 group-hover:text-primary" />
                  </Link>
                </div>
                <div className="base-grid absolute h-full w-full opacity-40" />
                <div className="absolute bottom-0 h-full w-full bg-gradient-to-t from-[hsl(var(--card))] to-transparent" />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
