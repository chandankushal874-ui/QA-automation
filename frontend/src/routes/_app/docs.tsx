import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Logo } from "@/ui/logo";
import { cn } from "@/utils/misc";
import { buttonVariants } from "@/ui/button-util";
import { Route as LoginRoute } from "@/routes/_app/login/_layout.index";
import { Route as DashboardRoute } from "@/routes/_app/_auth/dashboard/_layout.index";
import siteConfig from "~/site.config";
import {
  BookOpen,
  Download,
  PlayCircle,
  LayoutDashboard,
  PlusCircle,
  History,
  FileText,
  Users as UsersIcon,
  ScrollText,
  ShieldCheck,
  Lock,
  Globe,
  Bug,
  Code2,
  KeyRound,
  Server,
  ChevronRight,
  Terminal,
  CheckCircle2,
  AlertTriangle,
  Fingerprint,
} from "lucide-react";

export const Route = createFileRoute("/_app/docs")({
  component: DocsPage,
  beforeLoad: () => ({
    title: `${siteConfig.siteTitle} - User Guide & Docs`,
  }),
});

/* ------------------------------------------------------------------ */
/*  Content model: every doc section is a small data object so adding */
/*  content later is just a matter of appending to SECTIONS below.    */
/* ------------------------------------------------------------------ */

interface DocSection {
  id: string;
  label: string;
  icon: React.ReactNode;
  render: () => React.ReactNode;
}

/* Small layout helpers used across sections ----------------------- */
function H2({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-2 scroll-mt-24 text-2xl font-bold tracking-tight text-primary">
      {children}
    </h2>
  );
}
function Lead({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-3 text-[15px] leading-relaxed text-primary/70">{children}</p>
  );
}
function H3({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mt-8 text-lg font-semibold text-primary">{children}</h3>
  );
}
function P({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-3 text-[15px] leading-relaxed text-primary/70">
      {children}
    </p>
  );
}
function Ul({ children }: { children: React.ReactNode }) {
  return (
    <ul className="mt-3 space-y-2 text-[15px] text-primary/70">{children}</ul>
  );
}
function Li({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-primary/30" />
      <span>{children}</span>
    </li>
  );
}
function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[13px] text-primary/90">
      {children}
    </code>
  );
}
function Pre({ children }: { children: React.ReactNode }) {
  return (
    <pre className="mt-4 overflow-x-auto rounded-lg border border-border bg-secondary/60 p-4 text-[13px] leading-relaxed text-primary/90 dark:bg-black/40">
      <code className="font-mono">{children}</code>
    </pre>
  );
}
function Callout({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "warning" | "success";
  title: string;
  children: React.ReactNode;
}) {
  const palette =
    tone === "warning"
      ? "border-yellow-500/30 bg-yellow-500/10 text-yellow-700 dark:text-yellow-300"
      : tone === "success"
        ? "border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-300"
        : "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300";
  return (
    <div className={cn("mt-4 rounded-lg border p-4", palette)}>
      <p className="flex items-center gap-2 text-sm font-semibold">
        {tone === "warning" ? (
          <AlertTriangle className="h-4 w-4" />
        ) : tone === "success" ? (
          <CheckCircle2 className="h-4 w-4" />
        ) : (
          <ShieldCheck className="h-4 w-4" />
        )}
        {title}
      </p>
      <div className="mt-2 text-sm leading-relaxed">{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Sections                                                          */
/* ------------------------------------------------------------------ */

const SECTIONS: DocSection[] = [
  /* 1. Overview ------------------------------------------------------- */
  {
    id: "overview",
    label: "Overview",
    icon: <BookOpen className="h-4 w-4" />,
    render: () => (
      <>
        <H2>What is {siteConfig.siteTitle}?</H2>
        <Lead>
          {siteConfig.siteDescription} Paste a URL, pick your browsers, and the
          platform crawls your web app, captures every console error and network
          failure, and produces an AI-triaged report with downloadable HTML.
        </Lead>

        <H3>How it works, in four steps</H3>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            {
              icon: <Globe className="h-5 w-5" />,
              t: "1 · Crawl",
              d: "A real browser (Chromium / Firefox / WebKit) navigates your site BFS-style, scrolls every page, and follows same-domain links.",
            },
            {
              icon: <Bug className="h-5 w-5" />,
              t: "2 · Detect",
              d: "JS errors, CORS issues, CSP violations, network failures, and visual regressions are recorded per page.",
            },
            {
              icon: <Code2 className="h-5 w-5" />,
              t: "3 · Discover",
              d: "API endpoints are mined from network traffic, GraphQL, and HTML/JS — building a complete map of your app.",
            },
            {
              icon: <FileText className="h-5 w-5" />,
              t: "4 · Report",
              d: "Issues are bucketed by type & severity, scored into a health grade, and exported as a standalone HTML report.",
            },
          ].map((s) => (
            <div
              key={s.t}
              className="flex flex-col gap-2 rounded-lg border border-border bg-card p-5"
            >
              <div className="flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-primary/80">
                {s.icon}
              </div>
              <p className="text-sm font-semibold text-primary">{s.t}</p>
              <p className="text-xs leading-relaxed text-primary/60">{s.d}</p>
            </div>
          ))}
        </div>

        <H3>Live URLs (after install)</H3>
        <Ul>
          <Li><Code>http://localhost:8000</Code> — REST API</Li>
          <Li><Code>http://localhost:8000/health</Code> — health check</Li>
          <Li><Code>http://localhost:8000/docs</Code> — interactive OpenAPI / Swagger</Li>
          <Li><Code>http://localhost:3000</Code> — this dashboard UI (if Node is installed)</Li>
          <Li><Code>http://localhost:3000/login</Code> — sign-in page</Li>
        </Ul>

        <Callout tone="info" title="Per-user isolation">
          Each registered username owns a private, fully isolated set of crawl
          jobs, history, and reports. User A can never see User B&apos;s data.
          Only admins see a platform-wide view.
        </Callout>
      </>
    ),
  },

  /* 2. Install (Windows) --------------------------------------------- */
  {
    id: "install-windows",
    label: "Install — Windows",
    icon: <Download className="h-4 w-4" />,
    render: () => (
      <>
        <H2>Installation — Windows</H2>
        <Lead>
          The recommended path is the one-command host installer (<Code>host.ps1</Code>).
          It creates a Python virtualenv, installs Playwright&apos;s three browsers
          (Chromium + Firefox + WebKit), and starts the API + UI. No Docker required.
        </Lead>

        <H3>Prerequisites</H3>
        <Ul>
          <Li>Windows 10 / 11</Li>
          <Li>Python 3.12+ and Node.js 20+ — auto-installed by <Code>host.ps1</Code> if missing</Li>
          <Li>~4 GB free disk space</Li>
          <Li>Internet connection (first run only)</Li>
        </Ul>

        <H3>Step-by-step</H3>
        <Pre>{`# 1. Open PowerShell (Win+X → Windows PowerShell / Terminal)
# 2. Go to the package folder
cd "C:\\Users\\Dell\\Downloads\\updated QA"

# 3. Extract the source archive
tar -xzf cbtp-source.tar.gz
cd cross-browser-testing-platform

# 4. Run the one-command installer
powershell -ExecutionPolicy Bypass -File host.ps1`}</Pre>

        <P>
          First run may take 5–15 minutes while browsers download. When ready,
          the terminal prints the <Code>LIVE</Code> block with URLs.
        </P>

        <H3>Run again later (deps already installed)</H3>
        <Pre>{`cd "C:\\Users\\Dell\\Downloads\\updated QA\\cross-browser-testing-platform"
powershell -ExecutionPolicy Bypass -File host.ps1`}</Pre>

        <H3>Optional environment variables</H3>
        <Pre>{`$env:CBTP_PORT   = "8000"   # API port
$env:CBTP_UI_PORT = "3000"   # UI port
$env:CBTP_SKIP_UI = "1"      # API only (skip React UI)

powershell -ExecutionPolicy Bypass -File host.ps1`}</Pre>
      </>
    ),
  },

  /* 3. Install (Mac/Linux) ------------------------------------------- */
  {
    id: "install-mac-linux",
    label: "Install — Mac / Linux",
    icon: <Terminal className="h-4 w-4" />,
    render: () => (
      <>
        <H2>Installation — macOS / Linux</H2>
        <Lead>
          Mirrors the Windows flow but uses the <Code>host.sh</Code> shell
          installer. On Linux, Playwright may need extra system libraries
          (the installer attempts <Code>playwright install --with-deps</Code>;
          <Code>sudo</Code> may be required once).
        </Lead>

        <H3>Prerequisites</H3>
        <Ul>
          <Li>macOS 12+ or modern Linux (Ubuntu 22.04+ recommended)</Li>
          <Li>Python 3.12+ and Node.js 20+ — auto-installed via Homebrew / deadsnakes / NodeSource</Li>
          <Li>~4 GB free disk space</Li>
          <Li>Internet connection (first run only)</Li>
        </Ul>

        <H3>Step-by-step</H3>
        <Pre>{`# 1. Open Terminal
# 2. Go to the package folder
cd "/path/to/updated QA"

# 3. Extract
tar -xzf cbtp-source.tar.gz
cd cross-browser-testing-platform

# 4. Run the one-command installer
chmod +x host.sh
./host.sh`}</Pre>

        <H3>Optional environment variables</H3>
        <Pre>{`export CBTP_PORT=8000
export CBTP_UI_PORT=3000
export CBTP_SKIP_UI=1   # API only

./host.sh`}</Pre>
      </>
    ),
  },

  /* 4. Troubleshooting ----------------------------------------------- */
  {
    id: "troubleshooting",
    label: "Troubleshooting",
    icon: <AlertTriangle className="h-4 w-4" />,
    render: () => (
      <>
        <H2>Troubleshooting</H2>
        <Lead>Common issues and their one-line fixes.</Lead>

        <H3>Install &amp; startup</H3>
        <Ul>
          <Li>
            <strong>Execution policy error (Windows):</strong>{" "}
            run with{" "}
            <Code>powershell -ExecutionPolicy Bypass -File host.ps1</Code>.
          </Li>
          <Li>
            <strong>Python 3.12+ not found:</strong> install manually from{" "}
            <Code>python.org</Code> (tick <em>Add python.exe to PATH</em>) or{" "}
            <Code>brew install python@3.12</Code> on macOS, then re-run.
          </Li>
          <Li>
            <strong>Port already in use:</strong>{" "}
            <Code>$env:CBTP_PORT = &quot;8080&quot;</Code> (Windows) or{" "}
            <Code>CBTP_PORT=8080 ./host.sh</Code> (Unix), then restart.
          </Li>
          <Li>
            <strong>Permission denied on host.sh:</strong>{" "}
            <Code>chmod +x host.sh</Code>.
          </Li>
          <Li>
            <strong>Playwright missing system libs (Linux):</strong>{" "}
            <Code>python -m playwright install --with-deps chromium firefox webkit</Code>{" "}
            (<Code>sudo</Code> may be required for apt packages).
          </Li>
          <Li>
            <strong>macOS Gatekeeper blocks browsers:</strong> allow under{" "}
            System Settings → Privacy &amp; Security.
          </Li>
          <Li>
            <strong>Firewall prompt:</strong> allow Python / Node on private
            networks for localhost access.
          </Li>
          <Li>
            <strong>No UI on :3000:</strong> install Node.js 20+ OR use the
            standalone API at <Code>http://localhost:8000</Code>.
          </Li>
        </Ul>

        <H3>Verifying health</H3>
        <Pre>{`# Windows
Invoke-WebRequest http://localhost:8000/health

# macOS / Linux
curl -sf http://localhost:8000/health`}</Pre>

        <Callout tone="success" title="Success criteria">
          <Code>GET /health</Code> returns HTTP 200 with a JSON body of
          <Code>{` { status: "healthy" }`}</Code>, and the terminal
          shows the <Code>LIVE</Code> block.
        </Callout>
      </>
    ),
  },

  /* 5. Using the platform -------------------------------------------- */
  {
    id: "using",
    label: "Using the Platform",
    icon: <LayoutDashboard className="h-4 w-4" />,
    render: () => (
      <>
        <H2>Using the Platform</H2>
        <Lead>
          Once signed in, every page lives under <Code>/dashboard</Code>. The
          top navigation exposes the pages you have access to (admin-only ones
          are hidden for regular users).
        </Lead>

        <H3>
          <LayoutDashboard className="mr-2 inline h-5 w-5 -mt-1 text-primary/60" />
          Dashboard
        </H3>
        <P>
          Your landing page inside the app. It shows your isolated crawler
          slots (<Code>X / 2 active</Code>), summary stats (Total / Completed /
          Issues / Endpoints), and your five most recent jobs. Stats auto-refresh.
        </P>

        <H3>
          <PlusCircle className="mr-2 inline h-5 w-5 -mt-1 text-primary/60" />
          New Test
        </H3>
        <P>This is where you launch a crawl. Configure:</P>
        <Ul>
          <Li><strong>Target URL</strong> — the entry point to scan (required).</Li>
          <Li><strong>Target Environment Profile</strong> — Dev, QA, Staging, or Production (used for credentials matching).</Li>
          <Li><strong>Authentication Credentials</strong> — optional username / password / login URL for protected apps.</Li>
          <Li><strong>Browsers to Test</strong> — toggle Chromium, Firefox, and/or WebKit independently.</Li>
          <Li>
            <strong>Advanced Settings</strong> — crawl scope (same-domain / same-origin / custom),
            <Code>max_pages</Code>, <Code>max_depth</Code>, and an optional
            <strong> Repository URL</strong> (used by the AI fix-suggestion engine).
          </Li>
        </Ul>
        <Callout tone="warning" title="Security isolation gate">
          Using the default local-dev credential user <Code>admin</Code> against
          a remote / external target is blocked by the backend. Provide a
          dedicated test credential instead.
        </Callout>
        <Callout tone="info" title="Concurrency">
          You can run up to <strong>2 simultaneous crawls per user</strong>.
          Each crawl is fully isolated (own job, frontier, and report). System
          design: ~20 users × 2 slots ≈ 40 concurrent crawls.
        </Callout>

        <H3>
          <History className="mr-2 inline h-5 w-5 -mt-1 text-primary/60" />
          History
        </H3>
        <P>
          A chronological list of your past test runs with status badges
          (<Code>pending</Code>, <Code>running</Code>, <Code>completed</Code>,
          <Code>failed</Code>, <Code>cancelled</Code>), pages visited, and issues
          found. Click any row to open its report. The list auto-refreshes every
          5 seconds while jobs are running.
        </P>

        <H3>
          <FileText className="mr-2 inline h-5 w-5 -mt-1 text-primary/60" />
          Test Report
        </H3>
        <P>Each report has three tabs plus a download bar:</P>
        <Ul>
          <Li>
            <strong>Error Type Breakdown</strong> — counts per category
            (JS error, network failure, HTTP error, CORS, CSP, etc.) with
            severity labels (Blocking / Functional / Advisory).
          </Li>
          <Li>
            <strong>Issues</strong> — the full list of detected issues with
            page URLs and stack traces.
          </Li>
          <Li>
            <strong>Endpoints</strong> — every API endpoint discovered on your
            app (REST, GraphQL, etc.) — expandable for detail.
          </Li>
          <Li>
            <strong>Download HTML Report</strong> — exports the standalone,
            self-contained report you can share offline.
          </Li>
        </Ul>

        <H3>
          <UsersIcon className="mr-2 inline h-5 w-5 -mt-1 text-primary/60" />
          Users <span className="ml-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-600 dark:text-amber-400">Admin</span>
        </H3>
        <P>
          Admin-only page to create platform users (regular or admin role).
          New users immediately get isolated job history. Admins can see all
          jobs across the platform.
        </P>

        <H3>
          <ScrollText className="mr-2 inline h-5 w-5 -mt-1 text-primary/60" />
          Audit <span className="ml-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-600 dark:text-amber-400">Admin</span>
        </H3>
        <P>
          Admin-only, append-only log of every authentication and authorization
          event (logins, registrations, MFA actions, job actions). Filterable
          by action with pagination.
        </P>
      </>
    ),
  },

  /* 6. Authentication & Security ------------------------------------- */
  {
    id: "auth-security",
    label: "Auth &amp; Security",
    icon: <ShieldCheck className="h-4 w-4" />,
    render: () => (
      <>
        <H2>Authentication &amp; Security</H2>
        <Lead>
          CBTP ships with a full enterprise auth layer — all optional, with
          sensible defaults out of the box.
        </Lead>

        <H3>
          <KeyRound className="mr-2 inline h-5 w-5 -mt-1 text-primary/60" />
          Accounts
        </H3>
        <P>
          Register any unique username + password. Bootstrap admin (first run
          only) credentials are printed once by the host script and saved in{" "}
          <Code>.env</Code>. Tokens are bearer JWTs stored in{" "}
          <Code>localStorage</Code> as <Code>cbtp_auth_token</Code>.
        </P>

        <H3>
          <Lock className="mr-2 inline h-5 w-5 -mt-1 text-primary/60" />
          TOTP Multi-Factor Authentication
        </H3>
        <P>Per-user optional 2FA. To enable:</P>
        <Ul>
          <Li>Sign in → <strong>Settings</strong> → <strong>Two-factor authentication</strong> → Enable MFA.</Li>
          <Li>Scan the QR code in Google Authenticator / Authy.</Li>
          <Li>Enter the 6-digit code to confirm. Future logins ask for it after your password.</Li>
        </Ul>
        <P>
          Disable platform-wide with <Code>MFA_ENABLED=false</Code> in <Code>.env</Code>.
        </P>

        <H3>
          <Fingerprint className="mr-2 inline h-5 w-5 -mt-1 text-primary/60" />
          Sign in with Google (OIDC)
        </H3>
        <P>Real IdP integration — Google <Code>id_token</Code>s are JWKS-verified.</P>
        <Pre>{`# 1. Google Cloud Console → APIs & Services → Credentials
#    → Create OAuth 2.0 Client ID (type: Web application)
# 2. Add authorized redirect URI:
#      http://localhost:8000/api/auth/oidc/callback
# 3. Set in .env (generated by host script on first run):
OIDC_ENABLED=true
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=<your client id>
OIDC_CLIENT_SECRET=<your client secret>`}</Pre>
        <P>
          Restart the host script — a &quot;Sign in with Google&quot; button
          appears on <Code>/login</Code>. First-time Google users get an account
          automatically; existing accounts are linked by email.
        </P>
        <Callout tone="warning" title="Do not invent credentials">
          Leave OIDC disabled (<Code>OIDC_ENABLED</Code> unset / false) if you
          do not have real Google credentials. The status endpoint is{" "}
          <Code>GET /api/auth/oidc/status</Code>.
        </Callout>
      </>
    ),
  },

  /* 7. API reference ------------------------------------------------- */
  {
    id: "api",
    label: "API Reference",
    icon: <Server className="h-4 w-4" />,
    render: () => (
      <>
        <H2>API Reference</H2>
        <Lead>
          Base URL: <Code>http://localhost:8000</Code>. All protected routes
          require <Code>Authorization: Bearer &lt;token&gt;</Code>. The full
          OpenAPI spec is browsable at <Code>/docs</Code>.
        </Lead>

        <H3>Auth</H3>
        <Pre>{`POST   /api/auth/register              {"username","password"}
POST   /api/auth/login                 {"username","password"}
GET    /api/auth/me                    (Bearer)
GET    /api/auth/audit                 (Bearer, admin)`}</Pre>

        <H3>MFA</H3>
        <Pre>{`POST   /api/auth/mfa/setup
POST   /api/auth/mfa/confirm
POST   /api/auth/mfa/verify
POST   /api/auth/mfa/disable`}</Pre>

        <H3>OIDC</H3>
        <Pre>{`GET    /api/auth/oidc/status           { oidcEnabled, oidcConfigured, issuer, loginUrl }
GET    /api/auth/oidc/login
GET    /api/auth/oidc/callback`}</Pre>

        <H3>Jobs</H3>
        <Pre>{`GET    /api/jobs                       (your jobs only)
POST   /api/jobs                       {url, browser_matrix, max_pages, max_depth, ...}
GET    /api/jobs/{id}
GET    /api/jobs/{id}/report           {issues, endpoints, error_type_summary, ...}
GET    /api/jobs/{id}/report.html
DELETE /api/jobs/{id}
GET    /api/jobs/crawler-slots         {active, maxConcurrent, canStartMore, activeJobs}`}</Pre>

        <H3>Admin</H3>
        <Pre>{`GET    /api/admin/users                (Bearer, admin)
POST   /api/admin/users                {username, password, role, email?}
GET    /metrics`}</Pre>

        <Callout tone="success" title="Quick token test">
          <Pre>{`curl -X POST http://localhost:8000/api/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"username":"alice","password":"secret12"}'

# → {"access_token":"...","token_type":"bearer"}`}</Pre>
        </Callout>
      </>
    ),
  },

  /* 8. FAQ ----------------------------------------------------------- */
  {
    id: "faq",
    label: "FAQ",
    icon: <PlayCircle className="h-4 w-4" />,
    render: () => (
      <>
        <H2>Frequently Asked Questions</H2>
        <div className="mt-6 space-y-6">
          {[
            {
              q: "How much disk space does CBTP need?",
              a: (
                <>
                  The source archive is ~2 MB. After first install the full
                  stack occupies ~2.5–4 GB (Python venv + Chromium / Firefox /
                  WebKit). No Docker images are used in this path.
                </>
              ),
            },
            {
              q: "Can I run the API without the React UI?",
              a: (
                <>
                  Yes. Set <Code>CBTP_SKIP_UI=1</Code> before running the host
                  script. The API at <Code>:8000</Code> still works and Swagger
                  lives at <Code>/docs</Code>.
                </>
              ),
            },
            {
              q: "Where are crawls stored?",
              a: (
                <>
                  In the platform SQLite database (<Code>cbtp.db</Code> at the
                  project root). Jobs, issues, pages, endpoints, and probes are
                  all persisted there, keyed by the owning user.
                </>
              ),
            },
            {
              q: "Is my data isolated from other users?",
              a: (
                <>
                  Yes. Each job is tagged with <Code>owner_id</Code> and every
                  API query is scoped to the requesting user. Admins are the
                  only exception — they can list all jobs and audit events.
                </>
              ),
            },
            {
              q: "Which browsers can I test against?",
              a: (
                <>
                  Chromium, Firefox, and WebKit. The host script installs all
                  three. In the <em>New Test</em> form you toggle which engines
                  the crawl runs on.
                </>
              ),
            },
            {
              q: "Does it work behind authentication?",
              a: (
                <>
                  Yes. Provide credentials in the <em>New Test</em> form
                  (username, password, optional login URL). The crawler handles
                  login flows and SPA state transitions.
                </>
              ),
            },
          ].map((f, i) => (
            <details
              key={i}
              className="group rounded-lg border border-border bg-card p-5 open:bg-card"
            >
              <summary className="flex cursor-pointer items-center justify-between gap-3 text-sm font-medium text-primary">
                <span>{f.q}</span>
                <ChevronRight className="h-4 w-4 shrink-0 text-primary/40 transition-transform group-open:rotate-90" />
              </summary>
              <div className="mt-3 text-[15px] leading-relaxed text-primary/70">
                {f.a}
              </div>
            </details>
          ))}
        </div>
      </>
    ),
  },
];

/* ------------------------------------------------------------------ */
/*  Page                                                              */
/* ------------------------------------------------------------------ */

function DocsPage() {
  const [active, setActive] = useState<string>("overview");
  const current = SECTIONS.find((s) => s.id === active) ?? SECTIONS[0];

  return (
    <div className="flex min-h-screen flex-col bg-secondary text-primary dark:bg-black">
      {/* Top bar */}
      <nav className="sticky top-0 z-50 flex w-full items-center justify-between border-b border-border bg-card/80 px-6 py-3 backdrop-blur">
        <Link to="/" className="flex items-center gap-2">
          <Logo width={28} height={28} />
          <span className="text-base font-semibold">CrossBrowserTest</span>
          <span className="ml-1 hidden text-xs text-primary/40 sm:inline">
            · Docs
          </span>
        </Link>
        <div className="flex items-center gap-2">
          <Link
            to="/"
            className={cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "text-primary/70",
            )}
          >
            Home
          </Link>
          <Link
            to={LoginRoute.fullPath}
            className={cn(buttonVariants({ variant: "default", size: "sm" }))}
          >
            Sign In
          </Link>
          <Link
            to={DashboardRoute.fullPath}
            className={cn(
              buttonVariants({ variant: "outline", size: "sm" }),
              "gap-1.5",
            )}
          >
            Dashboard
          </Link>
        </div>
      </nav>

      {/* Body: sticky TOC + scrolling content */}
      <div className="mx-auto flex w-full max-w-screen-xl gap-10 px-6 py-10">
        {/* TOC */}
        <aside className="sticky top-20 hidden h-[calc(100vh-6rem)] w-64 shrink-0 overflow-y-auto lg:block">
          <p className="px-3 text-xs font-semibold uppercase tracking-wider text-primary/40">
            User Guide
          </p>
          <nav className="mt-2 flex flex-col gap-0.5">
            {SECTIONS.map((s) => {
              const isActive = s.id === active;
              return (
                <a
                  key={s.id}
                  href={`#${s.id}`}
                  onClick={() => setActive(s.id)}
                  className={cn(
                    "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                    isActive
                      ? "bg-primary/10 font-medium text-primary"
                      : "text-primary/60 hover:bg-primary/5 hover:text-primary",
                  )}
                >
                  <span
                    className={cn(
                      isActive ? "text-primary" : "text-primary/40",
                    )}
                  >
                    {s.icon}
                  </span>
                  <span dangerouslySetInnerHTML={{ __html: s.label }} />
                </a>
              );
            })}
          </nav>

          <div className="mt-6 rounded-lg border border-border bg-card p-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-primary/40">
              Need more?
            </p>
            <p className="mt-2 text-xs leading-relaxed text-primary/60">
              Open the interactive API docs at{" "}
              <Code>http://localhost:8000/docs</Code> or check the install
              package in <Code>updated&nbsp;QA</Code>.
            </p>
          </div>
        </aside>

        {/* Content */}
        <main className="min-w-0 flex-1 pb-24">
          <article className="rounded-lg border border-border bg-card p-6 sm:p-10">
            {current.render()}
          </article>

          {/* Section pager */}
          <div className="mt-6 flex items-center justify-between gap-3">
            {(() => {
              const idx = SECTIONS.findIndex((s) => s.id === active);
              const prev = SECTIONS[idx - 1];
              const next = SECTIONS[idx + 1];
              return (
                <>
                  {prev ? (
                    <a
                      href={`#${prev.id}`}
                      onClick={() => setActive(prev.id)}
                      className="flex flex-1 items-center gap-2 rounded-lg border border-border bg-card p-4 text-sm text-primary/70 hover:text-primary"
                    >
                      <ChevronRight className="h-4 w-4 rotate-180" />
                      <span className="truncate">
                        <span className="block text-[11px] uppercase tracking-wider text-primary/40">
                          Previous
                        </span>
                        <span dangerouslySetInnerHTML={{ __html: prev.label }} />
                      </span>
                    </a>
                  ) : (
                    <span className="flex-1" />
                  )}
                  {next ? (
                    <a
                      href={`#${next.id}`}
                      onClick={() => setActive(next.id)}
                      className="flex flex-1 items-center justify-end gap-2 rounded-lg border border-border bg-card p-4 text-right text-sm text-primary/70 hover:text-primary"
                    >
                      <span className="truncate">
                        <span className="block text-[11px] uppercase tracking-wider text-primary/40">
                          Next
                        </span>
                        <span dangerouslySetInnerHTML={{ __html: next.label }} />
                      </span>
                      <ChevronRight className="h-4 w-4" />
                    </a>
                  ) : (
                    <span className="flex-1" />
                  )}
                </>
              );
            })()}
          </div>
        </main>
      </div>

      {/* Footer */}
      <footer className="border-t border-border px-6 py-8 text-center text-xs text-primary/40">
        {siteConfig.siteTitle} — Autonomous Cross-Browser Testing Platform
      </footer>
    </div>
  );
}
