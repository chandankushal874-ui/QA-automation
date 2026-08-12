import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { listJobs, stopJob } from "@/lib/api";
import { cn } from "@/utils/misc";
import { ExternalLink, Clock, CheckCircle, XCircle, Loader2, AlertTriangle, Square } from "lucide-react";
import { Route as ReportRoute } from "@/routes/_app/_auth/dashboard/_layout.report.$testJobId";
import siteConfig from "~/site.config";
import { useState, useCallback } from "react";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/history")({
  component: TestHistory,
  beforeLoad: () => ({
    title: `${siteConfig.siteTitle} - Test History`,
    headerTitle: "Test History",
    headerDescription: "View all your past cross-browser test runs and their results.",
  }),
});

const statusIcons: Record<string, React.ReactNode> = {
  completed: <CheckCircle className="h-4 w-4 text-green-500" />,
  running: <Loader2 className="h-4 w-4 animate-spin text-blue-500" />,
  failed: <XCircle className="h-4 w-4 text-red-500" />,
  pending: <Clock className="h-4 w-4 text-yellow-500" />,
  cancelled: <AlertTriangle className="h-4 w-4 text-gray-500" />,
};

export default function TestHistory() {
  const queryClient = useQueryClient();
  const [stoppingJobs, setStoppingJobs] = useState<Set<string>>(new Set());

  const { data, isLoading } = useQuery({
    queryKey: ["all-jobs"],
    queryFn: () => listJobs({ limit: 50 }),
    refetchInterval: 5_000,
  });

  const jobs = data?.jobs || [];

  const handleStop = useCallback(async (jobId: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm('Stop this test? The crawl will be cancelled immediately.')) return;
    setStoppingJobs(prev => new Set(prev).add(jobId));
    try {
      await stopJob(jobId);
      queryClient.invalidateQueries({ queryKey: ["all-jobs"] });
    } catch (err: any) {
      alert(err.message || 'Failed to stop job');
    } finally {
      setStoppingJobs(prev => {
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
    }
  }, [queryClient]);

  return (
    <div className="flex h-full w-full bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl gap-12">
        <div className="flex w-full flex-col rounded-lg border border-border bg-card dark:bg-black">
          <div className="flex w-full flex-col rounded-lg p-6">
            <div className="flex flex-col gap-2">
              <h2 className="text-xl font-medium text-primary">All Tests</h2>
              <p className="text-sm font-normal text-primary/60">
                {jobs?.length || 0} test{jobs?.length !== 1 ? "s" : ""} run
              </p>
            </div>
          </div>
          <div className="flex w-full px-6">
            <div className="w-full border-b border-border" />
          </div>

          <div className="p-6">
            {isLoading ? (
              <div className="flex items-center justify-center py-24">
                <Loader2 className="h-8 w-8 animate-spin text-primary/40" />
              </div>
            ) : jobs && jobs.length > 0 ? (
              <div className="flex flex-col gap-3">
                {jobs.map((job: any) => (
                  <Link
                    key={job.id}
                    to={ReportRoute.fullPath}
                    params={{ testJobId: job.id }}
                    className="flex items-center justify-between rounded-md border border-border p-4 hover:border-primary/40 transition-colors group"
                  >
                    <div className="flex items-center gap-4">
                      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-secondary">
                        {statusIcons[job.status] || <Clock className="h-4 w-4 text-primary/40" />}
                      </div>
                      <div className="flex flex-col gap-0.5">
                        <span className="text-sm font-medium text-primary truncate max-w-lg group-hover:text-primary/80">
                          {job.url}
                        </span>
                        <div className="flex items-center gap-3 text-xs text-primary/50">
                          <span>
                            {new Date(job.created_at).toLocaleString()}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-3 text-xs text-primary/60">
                        <span>{job.pages_visited} pages</span>
                        <span className={cn(
                          job.issues_found > 0 ? "text-red-500 font-medium" : "text-green-500"
                        )}>
                          {job.issues_found} issues
                        </span>
                      </div>
                      {(job.status === 'running' || job.status === 'pending') && (
                        <button
                          onClick={(e) => handleStop(job.id, e)}
                          disabled={stoppingJobs.has(job.id)}
                          className="flex items-center gap-1 rounded-md bg-red-500/10 px-2.5 py-1 text-xs font-medium text-red-500 border border-red-500/20 hover:bg-red-500/20 transition-colors disabled:opacity-50"
                          title="Stop this test"
                        >
                          {stoppingJobs.has(job.id) ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Square className="h-3 w-3" />
                          )}
                          Stop
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
                      <ExternalLink className="h-4 w-4 text-primary/30 group-hover:text-primary/60" />
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <div className="relative flex w-full flex-col items-center justify-center gap-6 overflow-hidden rounded-lg border border-border bg-secondary px-6 py-24 dark:bg-card">
                <div className="z-10 flex flex-col items-center gap-4">
                  <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-primary/20 bg-card">
                    <Clock className="h-8 w-8 stroke-[1.5px] text-primary/60" />
                  </div>
                  <p className="text-base font-medium text-primary">No tests yet</p>
                  <p className="text-center text-base font-normal text-primary/60">
                    Run your first cross-browser test to see results here.
                  </p>
                </div>
                <div className="base-grid absolute h-full w-full opacity-40" />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

