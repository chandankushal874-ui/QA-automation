import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  RefreshCw,
  GitBranch,
  Camera,
  Search,
  CheckCircle2,
  AlertTriangle,
  Layers,
} from "lucide-react";
import { Button } from "@/ui/button";
import { Input } from "@/ui/input";
import {
  approveCompare,
  captureBaseline,
  compareProject,
  fetchArtifactBlobUrl,
  getCompare,
  getProject,
  listBaselines,
  updateProject,
  type CompareResult,
} from "@/lib/api";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/project/$projectId")({
  component: ProjectDetailPage,
  beforeLoad: () => ({
    title: "Project",
    headerTitle: "Visual regression",
    headerDescription: "Capture baselines and review diffs against them.",
  }),
});

function ProjectDetailPage() {
  const { projectId } = Route.useParams();
  const queryClient = useQueryClient();
  const [baselineJob, setBaselineJob] = useState("");
  const [compareJob, setCompareJob] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [compareId, setCompareId] = useState<string | null>(null);
  const [regionX, setRegionX] = useState("");
  const [regionY, setRegionY] = useState("");
  const [regionW, setRegionW] = useState("");
  const [regionH, setRegionH] = useState("");

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => getProject(projectId),
  });

  const { data: baselines } = useQuery({
    queryKey: ["baselines", projectId],
    queryFn: () => listBaselines(projectId),
  });

  const { data: compare } = useQuery({
    queryKey: ["compare", projectId, compareId],
    queryFn: () => getCompare(projectId, compareId!),
    enabled: !!compareId,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    queryClient.invalidateQueries({ queryKey: ["baselines", projectId] });
  };

  const baselineMut = useMutation({
    mutationFn: () => captureBaseline(projectId, baselineJob.trim()),
    onSuccess: (res) => {
      setNotice(`Baseline captured (${res.baselines_created} screenshots).`);
      setError("");
      setBaselineJob("");
      invalidate();
    },
    onError: (e: Error) => {
      setNotice("");
      setError(e.message);
    },
  });

  const compareMut = useMutation({
    mutationFn: () => compareProject(projectId, compareJob.trim()),
    onSuccess: (res) => {
      setCompareId(res.compare_id);
      setError("");
      setNotice("");
      setCompareJob("");
    },
    onError: (e: Error) => {
      setNotice("");
      setError(e.message);
    },
  });

  const approveMut = useMutation({
    mutationFn: () => approveCompare(projectId, compareId!),
    onSuccess: (res) => {
      setNotice(`Approved — ${res.promoted} baseline(s) updated.`);
      setCompareId(null);
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  const regionMut = useMutation({
    mutationFn: () =>
      updateProject(projectId, {
        ignore_regions: [
          ...(project?.ignore_regions ?? []),
          { x: Number(regionX), y: Number(regionY), width: Number(regionW), height: Number(regionH) },
        ],
      }),
    onSuccess: () => {
      setNotice("Ignore region added.");
      setRegionX(""); setRegionY(""); setRegionW(""); setRegionH("");
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  if (isLoading) {
    return <div className="flex h-full items-center justify-center p-8 text-primary/60">Loading…</div>;
  }
  if (!project) {
    return <div className="p-8 text-primary/60">Project not found.</div>;
  }

  return (
    <div className="flex h-full w-full flex-col gap-6 bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl flex-col gap-6">
        {/* Header */}
        <section className="rounded-lg border border-border bg-card p-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="text-xl font-medium text-primary">{project.name}</h2>
              <p className="flex items-center gap-1 text-sm text-primary/50">
                <GitBranch className="h-3 w-3" />
                {project.base_url} · default branch <span className="font-mono">{project.default_branch}</span>
              </p>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <span className="rounded-full bg-blue-500/15 px-2 py-0.5 text-xs font-semibold text-blue-500">
                threshold {project.threshold}%
              </span>
              <span className="rounded-full bg-violet-500/15 px-2 py-0.5 text-xs font-semibold text-violet-500">
                {project.ignore_regions.length} ignore region(s)
              </span>
            </div>
          </div>
        </section>

        {/* Capture / compare */}
        <section className="rounded-lg border border-border bg-card p-6">
          <h3 className="mb-3 text-sm font-medium text-primary/80">Run a diff</h3>
          <div className="flex flex-col gap-3 md:flex-row md:items-end">
            <div className="flex-1">
              <label className="mb-1 flex items-center gap-1 text-xs text-primary/50">
                <Camera className="h-3 w-3" /> Baseline from job ID
              </label>
              <Input
                placeholder="Paste a completed crawl job ID"
                value={baselineJob}
                onChange={(e) => setBaselineJob(e.target.value)}
              />
            </div>
            <Button
              variant="secondary"
              disabled={baselineMut.isPending || !baselineJob.trim()}
              onClick={() => baselineMut.mutate()}
            >
              {baselineMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Capture baseline
            </Button>
            <div className="flex-1">
              <label className="mb-1 flex items-center gap-1 text-xs text-primary/50">
                <Search className="h-3 w-3" /> Compare against job ID
              </label>
              <Input
                placeholder="Paste a completed crawl job ID"
                value={compareJob}
                onChange={(e) => setCompareJob(e.target.value)}
              />
            </div>
            <Button
              disabled={compareMut.isPending || !compareJob.trim()}
              onClick={() => compareMut.mutate()}
            >
              {compareMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Compare
            </Button>
          </div>
          {error && <p className="mt-3 text-sm text-red-500">{error}</p>}
          {notice && <p className="mt-3 text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}
        </section>

        {/* Baselines */}
        <section className="rounded-lg border border-border bg-card p-6">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-medium text-primary/80">
              Active baselines ({baselines?.baselines.filter((b) => b.status === "active").length ?? 0})
            </h3>
            <Button variant="ghost" size="sm" onClick={invalidate}>
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
          {baselines && baselines.baselines.length > 0 ? (
            <div className="max-h-56 overflow-y-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-primary/50">
                    <th className="py-2 pr-3 font-medium">URL</th>
                    <th className="py-2 pr-3 font-medium">Browser</th>
                    <th className="py-2 pr-3 font-medium">Branch</th>
                    <th className="py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {baselines.baselines.map((b) => (
                    <tr key={b.id} className="border-b border-border/50">
                      <td className="max-w-[300px] truncate py-2 pr-3 text-primary/70">{b.url}</td>
                      <td className="py-2 pr-3 text-primary/70">{b.browser}</td>
                      <td className="py-2 pr-3 font-mono text-xs text-primary/60">{b.branch}</td>
                      <td className="py-2 text-primary/70">{b.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-primary/60">
              No baselines yet. Run a crawl, then capture it as the baseline.
            </p>
          )}
        </section>

        {/* Compare result */}
        {compare && (
          <CompareView
            results={compare.results}
            projectId={projectId}
            onApprove={approveMut.mutate}
            approving={approveMut.isPending}
          />
        )}

        {/* Ignore regions */}
        <section className="rounded-lg border border-border bg-card p-6">
          <h3 className="mb-1 flex items-center gap-2 text-sm font-medium text-primary/80">
            <Layers className="h-4 w-4" /> Ignore regions
          </h3>
          <p className="mb-3 text-sm text-primary/60">
            Mask dynamic areas (ads, timestamps, carousels) so they never count
            as regressions.
          </p>
          {project.ignore_regions.length > 0 && (
            <ul className="mb-3 flex flex-wrap gap-2">
              {project.ignore_regions.map((r: any, i: number) => (
                <li key={i} className="rounded bg-secondary px-2 py-1 font-mono text-xs text-primary/70">
                  x:{r.x} y:{r.y} w:{r.width} h:{r.height}
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-end gap-2">
            <Input className="w-20" placeholder="x" value={regionX} onChange={(e) => setRegionX(e.target.value)} />
            <Input className="w-20" placeholder="y" value={regionY} onChange={(e) => setRegionY(e.target.value)} />
            <Input className="w-20" placeholder="w" value={regionW} onChange={(e) => setRegionW(e.target.value)} />
            <Input className="w-20" placeholder="h" value={regionH} onChange={(e) => setRegionH(e.target.value)} />
            <Button
              variant="secondary"
              disabled={regionMut.isPending || !regionX || !regionY || !regionW || !regionH}
              onClick={() => regionMut.mutate()}
            >
              {regionMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Add region
            </Button>
          </div>
        </section>
      </div>
    </div>
  );
}

function CompareView({
  results,
  projectId,
  onApprove,
  approving,
}: {
  results: CompareResult[];
  projectId: string;
  onApprove: () => void;
  approving: boolean;
}) {
  const regressions = results.filter((r) => r.verdict === "regression");
  const matches = results.filter((r) => r.verdict === "match").length;

  return (
    <section className="rounded-lg border border-border bg-card p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-medium text-primary/80">
          <AlertTriangle className={`h-4 w-4 ${regressions.length ? "text-red-500" : "text-emerald-500"}`} />
          Compare result — {regressions.length} regression(s), {matches} match(es)
        </h3>
        <div className="flex items-center gap-2">
          {regressions.length > 0 && (
            <Button variant="secondary" disabled={approving} onClick={onApprove}>
              {approving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
              Approve & promote to baseline
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-4">
        {regressions.map((r, i) => (
          <div key={i} className="rounded-md border border-red-500/30 bg-red-500/5 p-4">
            <div className="mb-2 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-red-500" />
              <span className="text-sm font-medium text-primary/90">{r.url}</span>
              <span className="font-mono text-xs text-primary/50">[{r.browser}]</span>
              <span className="ml-auto rounded bg-red-500/15 px-2 py-0.5 text-xs font-semibold text-red-500">
                {r.mismatch_percentage?.toFixed(4)}% changed
              </span>
            </div>
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
              <ArtifactImage label="Baseline" projectId={projectId} objKey={r.baseline_key} />
              <ArtifactImage label="Current" projectId={projectId} objKey={r.current_key} />
              <ArtifactImage label="Diff (red = changed)" projectId={projectId} objKey={r.diff_key} />
            </div>
          </div>
        ))}

        {regressions.length === 0 && (
          <div className="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-4 text-sm text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-4 w-4" />
            No visual regressions — all screenshots match the baseline.
          </div>
        )}

        {/* Non-regression verdicts summary */}
        {results.filter((r) => r.verdict !== "regression" && r.verdict !== "match").length > 0 && (
          <details className="text-sm text-primary/60">
            <summary className="cursor-pointer">Other verdicts</summary>
            <ul className="mt-2 flex flex-col gap-1 pl-4">
              {results
                .filter((r) => r.verdict !== "regression" && r.verdict !== "match")
                .map((r, i) => (
                  <li key={i} className="font-mono text-xs">
                    {r.verdict} — {r.url} [{r.browser}]
                  </li>
                ))}
            </ul>
          </details>
        )}
      </div>
    </section>
  );
}

function ArtifactImage({
  label,
  projectId,
  objKey,
}: {
  label: string;
  projectId: string;
  objKey?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!objKey) return;
    fetchArtifactBlobUrl(projectId, objKey)
      .then((u) => {
        if (!cancelled) setUrl(u);
      })
      .catch(() => {
        if (!cancelled) setErr(true);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, objKey]);

  return (
    <div>
      <div className="mb-1 text-xs text-primary/40">{label}</div>
      {err ? (
        <div className="flex h-32 items-center justify-center rounded border border-dashed border-border/60 text-xs text-primary/40">
          image unavailable
        </div>
      ) : url ? (
        <img src={url} alt={label} className="max-h-72 w-full rounded border border-border object-contain" />
      ) : (
        <div className="flex h-32 items-center justify-center rounded border border-dashed border-border/60">
          <Loader2 className="h-4 w-4 animate-spin text-primary/40" />
        </div>
      )}
    </div>
  );
}
