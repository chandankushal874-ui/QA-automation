import { createFileRoute, useNavigate, Link } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Globe, Shield, Settings2, Layers } from "lucide-react";
import { Button } from "@/ui/button";
import { Switch } from "@/ui/switch";
import { createJob, getCrawlerSlots } from "@/lib/api";
import { Route as ReportRoute } from "@/routes/_app/_auth/dashboard/_layout.report.$testJobId";
import siteConfig from "~/site.config";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout/new-test")({
  component: NewTest,
  beforeLoad: () => ({
    title: `${siteConfig.siteTitle} - New Test`,
    headerTitle: "New Cross-Browser Test",
    headerDescription:
      "Paste a URL to scan. You can run up to 2 isolated crawls at once (per user).",
  }),
});

export default function NewTest() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [scope, setScope] = useState("same-domain");
  const [maxPages, setMaxPages] = useState(100);
  const [maxDepth, setMaxDepth] = useState(5);
  const [browsers, setBrowsers] = useState({
    chromium: true,
    firefox: false,
    webkit: false,
  });
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [credentials, setCredentials] = useState({
    username: "",
    password: "",
    loginUrl: "",
  });
  const [environment, setEnvironment] = useState("dev");
  const [showBrowser, setShowBrowser] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { data: slots, refetch: refetchSlots } = useQuery({
    queryKey: ["crawler-slots"],
    queryFn: getCrawlerSlots,
    refetchInterval: 4_000,
  });

  const isLocalUrl = (u: string) => {
    try {
      const hostname = new URL(u).hostname;
      return hostname === "localhost" || hostname === "127.0.0.1" || hostname.startsWith("192.168.") || hostname.startsWith("10.");
    } catch {
      return false;
    }
  };
  const showSecurityWarning = url && !isLocalUrl(url) && credentials.username === "admin";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url) return;
    if (slots && !slots.canStartMore) {
      alert(
        slots.note ||
          "You already have 2 active crawls. Wait for one to finish or stop a job.",
      );
      return;
    }

    setIsSubmitting(true);
    try {
      const data = await createJob({
        url,
        scope,
        browser_matrix: browsers,
        max_pages: maxPages,
        max_depth: maxDepth,
        repo_url: repoUrl || undefined,
        config: showBrowser ? { show_browser: true } : undefined,
        credentials: credentials.username
          ? {
              username: credentials.username,
              password: credentials.password,
              login_url: credentials.loginUrl || undefined,
            }
          : undefined,
      });
      await refetchSlots();
      navigate({
        to: ReportRoute.fullPath,
        params: { testJobId: data.id },
      });
    } catch (err: any) {
      console.error("Failed to create test:", err);
      alert(err.message || "Failed to create test job");
      await refetchSlots();
    } finally {
      setIsSubmitting(false);
    }
  };

  const maxSlots = slots?.maxConcurrent ?? 2;
  const activeSlots = slots?.active ?? 0;
  const canStart = slots?.canStartMore !== false;

  return (
    <div className="flex h-full w-full bg-secondary px-6 py-8 dark:bg-black">
      <div className="z-10 mx-auto flex h-full w-full max-w-screen-xl gap-12">
        <div className="flex w-full flex-col rounded-lg border border-border bg-card dark:bg-black">
          <form onSubmit={handleSubmit}>
            <div className="flex w-full flex-col gap-6 p-6">
              {/* Crawler slots (per-user isolation) */}
              <div className="flex flex-col gap-2 rounded-lg border border-border bg-secondary/40 p-4 dark:bg-secondary/20">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Layers className="h-5 w-5 text-sky-500" />
                    <span className="text-sm font-medium text-primary">
                      Your crawler slots
                    </span>
                  </div>
                  <span className="text-sm font-semibold text-primary">
                    {activeSlots} / {slots?.maxConcurrent == null ? "∞" : maxSlots}{" "}
                    active
                  </span>
                </div>
                <p className="text-xs text-primary/60">
                  You can run up to{" "}
                  <strong>{slots?.maxConcurrent == null ? "unlimited" : maxSlots}</strong>{" "}
                  crawls at the same time. Each crawl is fully isolated (own job,
                  frontier, report). Other users never see your jobs.
                  {slots?.role === "user" ? (
                    <> System design: 20 users × 2 slots ≈ 40 concurrent crawls.</>
                  ) : null}
                </p>
                <div className="mt-1 flex gap-2">
                  {Array.from({
                    length: Math.max(maxSlots || 2, 2),
                  }).map((_, i) => (
                    <div
                      key={i}
                      className={`h-2 flex-1 rounded-full ${
                        i < activeSlots
                          ? "bg-sky-500"
                          : "bg-primary/10"
                      }`}
                      title={i < activeSlots ? "In use" : "Free"}
                    />
                  ))}
                </div>
                {slots?.activeJobs?.length ? (
                  <ul className="mt-2 space-y-1 text-xs text-primary/70">
                    {slots.activeJobs.map((j) => (
                      <li key={j.id} className="flex items-center justify-between gap-2">
                        <span className="truncate font-mono">
                          {j.status}: {j.url}
                        </span>
                        <Link
                          to="/dashboard/report/$testJobId"
                          params={{ testJobId: j.id }}
                          className="shrink-0 text-sky-600 underline dark:text-sky-400"
                        >
                          open
                        </Link>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {!canStart ? (
                  <p className="text-xs font-medium text-amber-600 dark:text-amber-400">
                    Both slots are busy. Wait for a crawl to finish or stop one
                    from History, then start another.
                  </p>
                ) : null}
              </div>

              {/* URL Input */}
              <div className="flex flex-col gap-2">
                <label className="text-sm font-medium text-primary">
                  Target URL <span className="text-red-500">*</span>
                </label>
                <div className="flex items-center gap-2 rounded-md border border-border bg-secondary px-3 py-2">
                  <Globe className="h-5 w-5 text-primary/40" />
                  <input
                    type="url"
                    placeholder="https://example.com"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    required
                    className="w-full bg-transparent text-primary outline-none placeholder:text-primary/40"
                  />
                </div>
                <p className="text-xs text-primary/50">
                  Enter the URL of the web application you want to test.
                </p>
              </div>

              {/* Environment Profile Selector */}
              <div className="flex flex-col gap-2">
                <label className="text-sm font-medium text-primary">
                  Target Environment Profile
                </label>
                <select
                  value={environment}
                  onChange={(e) => setEnvironment(e.target.value)}
                  className="w-full rounded-md border border-border bg-secondary px-3 py-2 text-primary outline-none"
                >
                  <option value="dev">Development (Local)</option>
                  <option value="qa">QA Testing</option>
                  <option value="staging">Staging / UAT</option>
                  <option value="prod">Production</option>
                </select>
                <p className="text-xs text-primary/50">
                  Select the profile context for test verification and credentials matching.
                </p>
              </div>

              {/* Credentials */}
              <div className="flex flex-col gap-3">
                <label className="text-sm font-medium text-primary flex items-center gap-2">
                  <Shield className="h-4 w-4" />
                  Authentication Credentials (Optional)
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <input
                    type="text"
                    placeholder="Username"
                    value={credentials.username}
                    onChange={(e) =>
                      setCredentials((prev) => ({ ...prev, username: e.target.value }))
                    }
                    className="w-full rounded-md border border-border bg-secondary px-3 py-2 text-primary outline-none placeholder:text-primary/40"
                  />
                  <input
                    type="password"
                    placeholder="Password"
                    value={credentials.password}
                    onChange={(e) =>
                      setCredentials((prev) => ({ ...prev, password: e.target.value }))
                    }
                    className="w-full rounded-md border border-border bg-secondary px-3 py-2 text-primary outline-none placeholder:text-primary/40"
                  />
                </div>
                <input
                  type="url"
                  placeholder="Login URL (defaults to target URL)"
                  value={credentials.loginUrl}
                  onChange={(e) =>
                    setCredentials((prev) => ({ ...prev, loginUrl: e.target.value }))
                  }
                  className="w-full rounded-md border border-border bg-secondary px-3 py-2 text-primary outline-none placeholder:text-primary/40"
                />
                {showSecurityWarning && (
                  <div className="rounded-md bg-yellow-500/10 border border-yellow-500/30 p-3 text-xs text-yellow-600 font-medium leading-relaxed mt-1">
                    <strong>⚠️ Security Isolation Warning:</strong> You are attempting to use the default local development credential user (<code>admin</code>) on a remote/external target environment. This is blocked by the backend API security gate.
                  </div>
                )}
              </div>

              {/* Browser Matrix */}
              <div className="flex flex-col gap-3">
                <label className="text-sm font-medium text-primary">Browsers to Test</label>
                <div className="flex flex-wrap gap-4">
                  {(["chromium", "firefox", "webkit"] as const).map((browser) => (
                    <label
                      key={browser}
                      className="flex items-center gap-2 cursor-pointer"
                    >
                      <Switch
                        checked={browsers[browser]}
                        onCheckedChange={(checked) =>
                          setBrowsers((prev) => ({ ...prev, [browser]: checked }))
                        }
                      />
                      <span className="text-sm text-primary/80 capitalize">
                        {browser}
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Live Browser Window (headed mode) */}
              <div className="flex flex-col gap-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <Switch
                    checked={showBrowser}
                    onCheckedChange={setShowBrowser}
                  />
                  <span className="text-sm font-medium text-primary">
                    Show live browser window (headed mode)
                  </span>
                </label>
                <p className="text-xs text-primary/50">
                  Pops a visible browser window on the server for each engine you
                  selected, so you can watch the automation live. Each user's crawl
                  gets its own isolated window. Runs slower than headless mode.
                </p>
              </div>

              {/* Advanced Settings Toggle */}
              <button
                type="button"
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="flex items-center gap-2 text-sm text-primary/60 hover:text-primary"
              >
                <Settings2 className="h-4 w-4" />
                {showAdvanced ? "Hide" : "Show"} Advanced Settings
              </button>

              {showAdvanced && (
                <div className="flex flex-col gap-6 border-t border-border pt-6">
                  {/* Scope */}
                  <div className="flex flex-col gap-2">
                    <label className="text-sm font-medium text-primary">Crawl Scope</label>
                    <select
                      value={scope}
                      onChange={(e) => setScope(e.target.value)}
                      className="w-full rounded-md border border-border bg-secondary px-3 py-2 text-primary outline-none"
                    >
                      <option value="same-domain">Same Domain</option>
                      <option value="same-origin">Same Origin</option>
                      <option value="custom">Custom</option>
                    </select>
                  </div>

                  {/* Max Pages & Depth */}
                  <div className="grid grid-cols-2 gap-4">
                    <div className="flex flex-col gap-2">
                      <label className="text-sm font-medium text-primary">Max Pages</label>
                      <input
                        type="number"
                        min={1}
                        max={10000}
                        value={maxPages}
                        onChange={(e) => setMaxPages(Number(e.target.value))}
                        className="w-full rounded-md border border-border bg-secondary px-3 py-2 text-primary outline-none"
                      />
                    </div>
                    <div className="flex flex-col gap-2">
                      <label className="text-sm font-medium text-primary">Max Depth</label>
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={maxDepth}
                        onChange={(e) => setMaxDepth(Number(e.target.value))}
                        className="w-full rounded-md border border-border bg-secondary px-3 py-2 text-primary outline-none"
                      />
                    </div>
                  </div>

                  {/* Repo URL */}
                  <div className="flex flex-col gap-2">
                    <label className="text-sm font-medium text-primary">
                      Repository URL (for AI fix suggestions)
                    </label>
                    <input
                      type="url"
                      placeholder="https://github.com/user/repo"
                      value={repoUrl}
                      onChange={(e) => setRepoUrl(e.target.value)}
                      className="w-full rounded-md border border-border bg-secondary px-3 py-2 text-primary outline-none placeholder:text-primary/40"
                    />
                  </div>

                </div>
              )}
            </div>

            <div className="flex w-full px-6">
              <div className="w-full border-b border-border" />
            </div>

            <div className="flex items-center justify-end gap-3 p-6">
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setUrl("");
                  setScope("same-domain");
                  setMaxPages(100);
                  setMaxDepth(5);
                  setBrowsers({ chromium: true, firefox: true, webkit: true });
                  setRepoUrl("");
                  setCredentials({ username: "", password: "", loginUrl: "" });
                }}
              >
                Reset
              </Button>
              <Button type="submit" disabled={!url || isSubmitting || !canStart}>
                {isSubmitting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Starting Test...
                  </>
                ) : (
                  "Start Cross-Browser Test"
                )}
              </Button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
