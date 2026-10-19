import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, PlusCircle, Trash2, RefreshCw, Link2, Copy } from "lucide-react";
import { Button } from "@/ui/button";
import { Input } from "@/ui/input";
import { createTunnel, listTunnels, revokeTunnel, type TunnelRow } from "@/lib/api";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/tunnels")({
  component: TunnelsPage,
  beforeLoad: () => ({
    title: "Tunnels",
    headerTitle: "Self-hosted tunnels",
    headerDescription:
      "Expose a local/staging target to the crawler via an outbound agent.",
  }),
});

function TunnelsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [targetOrigin, setTargetOrigin] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [agentToken, setAgentToken] = useState<string | null>(null);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["tunnels"],
    queryFn: listTunnels,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["tunnels"] });

  const createMut = useMutation({
    mutationFn: createTunnel,
    onSuccess: (t) => {
      setAgentToken(t.agent_token);
      setNotice(`Tunnel "${t.name}" created.`);
      setError("");
      setName("");
      setTargetOrigin("");
      invalidate();
    },
    onError: (e: Error) => {
      setNotice("");
      setError(e.message);
    },
  });

  const revokeMut = useMutation({
    mutationFn: (t: TunnelRow) => revokeTunnel(t.id),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const tunnels = data?.tunnels ?? [];

  return (
    <div className="flex h-full w-full bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl flex-col gap-8">
        <section className="rounded-lg border border-border bg-card p-6">
          <h2 className="mb-1 flex items-center gap-2 text-lg font-medium text-primary">
            <PlusCircle className="h-5 w-5 text-primary/80" /> New tunnel
          </h2>
          <p className="mb-4 text-sm text-primary/60">
            Provisions a one-time agent token. Run the agent locally:
            <code className="mx-1 rounded bg-secondary px-1.5 py-0.5 text-xs">python -m src.tunnel.agent --relay wss://&lt;host&gt;/tunnel/agent --token &lt;agent_token&gt; --target http://localhost:3000</code>
          </p>
          <form
            className="flex flex-col gap-3 md:max-w-2xl"
            onSubmit={(e) => {
              e.preventDefault();
              setError("");
              setNotice("");
              if (!name.trim()) return;
              createMut.mutate({ name: name.trim(), target_origin: targetOrigin.trim() });
            }}
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Input placeholder="Name (e.g. local dev)" value={name} onChange={(e) => setName(e.target.value)} required />
              <Input placeholder="Target origin (e.g. http://localhost:3000)" value={targetOrigin} onChange={(e) => setTargetOrigin(e.target.value)} />
            </div>
            {error && <p className="text-sm text-red-500">{error}</p>}
            {notice && <p className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}
            {agentToken && (
              <div className="flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
                <code className="flex-1 break-all font-mono text-xs text-primary/90">{agentToken}</code>
                <Button variant="ghost" size="sm" onClick={() => navigator.clipboard.writeText(agentToken)}>
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
            )}
            <Button type="submit" disabled={createMut.isPending} className="w-fit">
              {createMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Create tunnel
            </Button>
          </form>
        </section>

        <section className="rounded-lg border border-border bg-card p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-medium text-primary">Your tunnels ({data?.total ?? 0})</h2>
            <Button variant="ghost" size="sm" onClick={() => refetch()}>
              <RefreshCw className="mr-1 h-4 w-4" /> Refresh
            </Button>
          </div>
          {isLoading ? (
            <p className="text-sm text-primary/60">Loading…</p>
          ) : tunnels.length === 0 ? (
            <p className="text-sm text-primary/60">No tunnels yet. Create one above.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {tunnels.map((t) => (
                <div key={t.id} className="flex items-center justify-between rounded-md border border-border/50 p-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium text-primary/90">{t.name}</p>
                    <p className="flex items-center gap-1 truncate text-xs text-primary/50">
                      <Link2 className="h-3 w-3" />
                      <code>{t.target_url}</code> · {t.status}
                    </p>
                  </div>
                  <Button
                    variant="ghost" size="sm" title="Revoke"
                    className="ml-2 text-red-500 hover:text-red-600"
                    onClick={() => { if (confirm(`Revoke tunnel "${t.name}"?`)) revokeMut.mutate(t); }}
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
