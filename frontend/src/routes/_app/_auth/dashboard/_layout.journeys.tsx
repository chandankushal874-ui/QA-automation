import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, PlusCircle, Trash2, RefreshCw, ChevronRight, Route as RouteIcon } from "lucide-react";
import { Button } from "@/ui/button";
import { Input } from "@/ui/input";
import { createJourney, deleteJourney, listJourneys, type JourneyRow } from "@/lib/api";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/journeys")({
  component: JourneysPage,
  beforeLoad: () => ({
    title: "Journeys",
    headerTitle: "Multi-step user journeys",
    headerDescription:
      "Author and replay declarative flows (login → browse → act → assert) across browsers.",
  }),
});

const STEP_ACTIONS = ["goto", "fill", "click", "wait", "assert", "login", "screenshot"];

function JourneysPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [jobId, setJobId] = useState("");
  const [stepsJson, setStepsJson] = useState(
    JSON.stringify([{ action: "goto", url: "https://example.com" }], null, 2),
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["journeys"],
    queryFn: listJourneys,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["journeys"] });

  const createMut = useMutation({
    mutationFn: createJourney,
    onSuccess: (j) => {
      setNotice(`Journey "${j.name}" created.`);
      setError("");
      setName("");
      setJobId("");
      invalidate();
    },
    onError: (e: Error) => {
      setNotice("");
      setError(e.message);
    },
  });

  const deleteMut = useMutation({
    mutationFn: (j: JourneyRow) => deleteJourney(j.id),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const journeys = data?.journeys ?? [];

  return (
    <div className="flex h-full w-full bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl flex-col gap-8">
        <section className="rounded-lg border border-border bg-card p-6">
          <h2 className="mb-1 flex items-center gap-2 text-lg font-medium text-primary">
            <PlusCircle className="h-5 w-5 text-primary/80" /> New journey
          </h2>
          <p className="mb-4 text-sm text-primary/60">
            Attach to a completed crawl job (supplies the target URL). Define
            steps as a JSON array of typed actions: {STEP_ACTIONS.join(", ")}.
          </p>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              setError("");
              setNotice("");
              let steps;
              try {
                steps = JSON.parse(stepsJson);
              } catch {
                setError("Steps must be valid JSON");
                return;
              }
              if (!name.trim() || !jobId.trim()) {
                setError("Name and job ID are required");
                return;
              }
              createMut.mutate({ job_id: jobId.trim(), name: name.trim(), steps });
            }}
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Input placeholder="Name (e.g. checkout flow)" value={name} onChange={(e) => setName(e.target.value)} required />
              <Input placeholder="Completed crawl job ID" value={jobId} onChange={(e) => setJobId(e.target.value)} required />
            </div>
            <textarea
              className="h-40 w-full rounded-md border border-border bg-secondary/50 p-3 font-mono text-xs text-primary/90 focus:outline-none"
              value={stepsJson}
              onChange={(e) => setStepsJson(e.target.value)}
            />
            {error && <p className="text-sm text-red-500">{error}</p>}
            {notice && <p className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}
            <Button type="submit" disabled={createMut.isPending} className="w-fit">
              {createMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Create journey
            </Button>
          </form>
        </section>

        <section className="rounded-lg border border-border bg-card p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-medium text-primary">Your journeys ({data?.total ?? 0})</h2>
            <Button variant="ghost" size="sm" onClick={() => refetch()}>
              <RefreshCw className="mr-1 h-4 w-4" /> Refresh
            </Button>
          </div>
          {isLoading ? (
            <p className="text-sm text-primary/60">Loading…</p>
          ) : journeys.length === 0 ? (
            <p className="text-sm text-primary/60">
              No journeys yet. Create one above to author a multi-step flow.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {journeys.map((j) => (
                <div key={j.id} className="flex items-center justify-between rounded-md border border-border/50 p-3 transition-colors hover:border-primary/30">
                  <button
                    className="flex min-w-0 flex-1 items-center gap-3 text-left"
                    onClick={() => navigate({ to: "/dashboard/journey/$journeyId", params: { journeyId: j.id } })}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-primary/90">{j.name}</p>
                      <p className="flex items-center gap-1 text-xs text-primary/50">
                        <RouteIcon className="h-3 w-3" />
                        {j.steps?.length ?? 0} steps · {j.status}
                      </p>
                    </div>
                    <ChevronRight className="h-4 w-4 shrink-0 text-primary/40" />
                  </button>
                  <Button
                    variant="ghost" size="sm" title="Delete"
                    className="ml-2 text-red-500 hover:text-red-600"
                    onClick={() => { if (confirm(`Delete journey "${j.name}"?`)) deleteMut.mutate(j); }}
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
