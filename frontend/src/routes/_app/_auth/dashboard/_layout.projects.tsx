import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, PlusCircle, Trash2, RefreshCw, ChevronRight, GitBranch } from "lucide-react";
import { Button } from "@/ui/button";
import { Input } from "@/ui/input";
import { createProject, deleteProject, listProjects, type ProjectRow } from "@/lib/api";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/projects")({
  component: ProjectsPage,
  beforeLoad: () => ({
    title: "Projects",
    headerTitle: "Visual regression projects",
    headerDescription:
      "Group sites into projects, capture baselines, and diff every new crawl against them.",
  }),
});

function ProjectsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["projects"] });

  const createMut = useMutation({
    mutationFn: createProject,
    onSuccess: (p) => {
      setNotice(`Project "${p.name}" created.`);
      setError("");
      setName("");
      setBaseUrl("");
      invalidate();
    },
    onError: (e: Error) => {
      setNotice("");
      setError(e.message);
    },
  });

  const deleteMut = useMutation({
    mutationFn: (p: ProjectRow) => deleteProject(p.id),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const projects = data?.projects ?? [];

  return (
    <div className="flex h-full w-full bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl flex-col gap-8">
        {/* Create form */}
        <section className="rounded-lg border border-border bg-card p-6">
          <h2 className="mb-1 flex items-center gap-2 text-lg font-medium text-primary">
            <PlusCircle className="h-5 w-5 text-primary/80" /> New project
          </h2>
          <p className="mb-4 text-sm text-primary/60">
            A project owns baselines for one site. After you run a crawl of your
            default branch, capture it as the baseline — later crawls diff
            against it.
          </p>
          <form
            className="flex flex-col gap-3 md:max-w-2xl"
            onSubmit={(e) => {
              e.preventDefault();
              setError("");
              setNotice("");
              if (!name.trim() || !baseUrl.trim()) return;
              createMut.mutate({ name: name.trim(), base_url: baseUrl.trim() });
            }}
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Input placeholder="Name (e.g. Storefront)" value={name} onChange={(e) => setName(e.target.value)} required />
              <Input type="url" placeholder="https://example.com" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} required />
            </div>
            {error && <p className="text-sm text-red-500">{error}</p>}
            {notice && <p className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}
            <Button type="submit" disabled={createMut.isPending} className="w-fit">
              {createMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Create project
            </Button>
          </form>
        </section>

        {/* List */}
        <section className="rounded-lg border border-border bg-card p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-medium text-primary">
              Your projects ({data?.total ?? 0})
            </h2>
            <Button variant="ghost" size="sm" onClick={() => refetch()}>
              <RefreshCw className="mr-1 h-4 w-4" /> Refresh
            </Button>
          </div>
          {isLoading ? (
            <p className="text-sm text-primary/60">Loading…</p>
          ) : projects.length === 0 ? (
            <p className="text-sm text-primary/60">
              No projects yet. Create one above to start capturing visual baselines.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {projects.map((p) => (
                <div
                  key={p.id}
                  className="flex items-center justify-between rounded-md border border-border/50 p-3 transition-colors hover:border-primary/30"
                >
                  <button
                    className="flex min-w-0 flex-1 items-center gap-3 text-left"
                    onClick={() => navigate({ to: "/dashboard/project/$projectId", params: { projectId: p.id } })}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-primary/90">{p.name}</p>
                      <p className="flex items-center gap-1 truncate text-xs text-primary/50">
                        <GitBranch className="h-3 w-3" />
                        {p.base_url} · branch {p.default_branch}
                      </p>
                    </div>
                    <ChevronRight className="h-4 w-4 shrink-0 text-primary/40" />
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    title="Delete"
                    className="ml-2 text-red-500 hover:text-red-600"
                    onClick={() => {
                      if (confirm(`Delete project "${p.name}"? This also removes its baselines.`)) {
                        deleteMut.mutate(p);
                      }
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
