const API_BASE = import.meta.env.VITE_API_URL || "";

function getToken(): string | null {
  try {
    return localStorage.getItem("cbtp_auth_token");
  } catch {
    return null;
  }
}

async function authFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return fetch(`${API_BASE}${path}`, { ...options, headers });
}

// ── Auth ──

export async function downloadAuthenticatedFile(path: string, filename: string) {
  const res = await authFetch(path);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function loginApi(password: string, username: string) {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Login failed" }));
    throw new Error(
      typeof err.detail === "string" ? err.detail : "Login failed",
    );
  }
  return res.json();
}

export async function registerApi(username: string, password: string) {
  const res = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Register failed" }));
    throw new Error(
      typeof err.detail === "string" ? err.detail : "Register failed",
    );
  }
  return res.json();
}

export async function getMe() {
  const res = await authFetch("/api/auth/me");
  if (!res.ok) throw new Error("Not authenticated");
  return res.json();
}

export async function updateMe(data: { username?: string }) {
  const res = await authFetch("/api/auth/me", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update profile");
  return res.json();
}

// ── OIDC ──

export async function getOidcStatus() {
  const res = await fetch(`${API_BASE}/api/auth/oidc/status`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to load OIDC status" }));
    throw new Error(err.detail || "Failed to load OIDC status");
  }
  return res.json() as Promise<{
    oidcEnabled: boolean;
    oidcConfigured: boolean;
    issuer: string | null;
    loginUrl: string | null;
  }>;
}

// ── MFA ──

export async function mfaSetup() {
  const res = await authFetch("/api/auth/mfa/setup", { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to start MFA setup" }));
    throw new Error(err.detail || "Failed to start MFA setup");
  }
  return res.json() as Promise<{
    secret: string;
    otpauth_uri: string;
    qr_url: string;
    message: string;
  }>;
}

export async function mfaConfirm(code: string) {
  const res = await authFetch("/api/auth/mfa/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to confirm MFA" }));
    throw new Error(err.detail || "Failed to confirm MFA");
  }
  return res.json() as Promise<{ mfaEnabled: boolean }>;
}

export async function mfaDisable(password: string, code: string) {
  const res = await authFetch("/api/auth/mfa/disable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password, code }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to disable MFA" }));
    throw new Error(err.detail || "Failed to disable MFA");
  }
  return res.json() as Promise<{ mfaEnabled: boolean }>;
}

export async function mfaVerify(mfaToken: string, code: string) {
  const res = await fetch(`${API_BASE}/api/auth/mfa/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mfa_token: mfaToken, code }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "MFA verification failed" }));
    throw new Error(err.detail || "MFA verification failed");
  }
  return res.json();
}

export async function getMfaStatus() {
  const res = await authFetch("/api/auth/mfa/status");
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to load MFA status" }));
    throw new Error(err.detail || "Failed to load MFA status");
  }
  return res.json() as Promise<{
    mfaEnabled: boolean;
    mfaFeatureAvailable: boolean;
    authProvider: string;
  }>;
}

// ── Admin: audit log ──

export async function listAuditEvents(params?: {
  limit?: number;
  offset?: number;
  action?: string;
  actor_username?: string;
}) {
  const searchParams = new URLSearchParams();
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));
  if (params?.action) searchParams.set("action", params.action);
  if (params?.actor_username) searchParams.set("actor_username", params.actor_username);
  const qs = searchParams.toString();
  const res = await authFetch(`/api/auth/audit${qs ? `?${qs}` : ""}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to load audit events" }));
    throw new Error(err.detail || "Failed to load audit events");
  }
  return res.json() as Promise<{
    total: number;
    limit: number;
    offset: number;
    events: Array<{
      id: string;
      created_at: string;
      actor_id: string | null;
      actor_username: string | null;
      action: string;
      resource_type: string | null;
      resource_id: string | null;
      ip_address: string | null;
      success: boolean;
      detail: unknown;
    }>;
  }>;
}

// ── Jobs ──

export async function createJob(data: {
  url: string;
  scope?: string;
  browser_matrix?: { chromium: boolean; firefox: boolean; webkit: boolean };
  max_pages?: number;
  max_depth?: number;
  repo_url?: string;
  config?: Record<string, unknown>;
  device?: string;
  credentials?: {
    username?: string;
    password?: string;
    login_url?: string;
  };
}) {
  const res = await authFetch("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to create job" }));
    // FastAPI may return detail as string or validation list
    const detail = err.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg).join(", ")
          : err.detail || "Failed to create job";
    throw new Error(msg);
  }
  return res.json();
}

export async function getJob(jobId: string) {
  const res = await authFetch(`/jobs/${jobId}`);
  if (!res.ok) throw new Error("Job not found");
  return res.json();
}

export async function getJobReport(jobId: string) {
  const res = await authFetch(`/jobs/${jobId}/report`);
  if (!res.ok) throw new Error("Report not found");
  return res.json();
}

export async function stopJob(jobId: string) {
  const res = await authFetch(`/jobs/${jobId}/stop`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to stop job" }));
    throw new Error(typeof err.detail === "string" ? err.detail : "Failed to stop job");
  }
  return res.json();
}

export async function resumeJob(jobId: string) {
  const res = await authFetch(`/jobs/${jobId}/resume`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to resume job" }));
    throw new Error(typeof err.detail === "string" ? err.detail : "Failed to resume job");
  }
  return res.json();
}

export async function listJobs(params?: {
  status?: string;
  limit?: number;
  offset?: number;
}) {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));
  const qs = searchParams.toString();
  const res = await authFetch(`/jobs${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error("Failed to fetch jobs");
  return res.json();
}

// ── Dashboard ──

export async function getDashboardStats() {
  const res = await authFetch("/api/dashboard/stats");
  if (!res.ok) throw new Error("Failed to fetch stats");
  return res.json();
}

// ── Metrics ──

export async function getMetrics() {
  const res = await authFetch("/metrics");
  if (!res.ok) throw new Error("Failed to fetch metrics");
  return res.json();
}

// ── Admin: platform users ──

export async function listPlatformUsers() {
  const res = await authFetch("/api/auth/users");
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to list users" }));
    throw new Error(err.detail || "Failed to list users");
  }
  return res.json() as Promise<{
    users: Array<{
      id: string;
      email: string;
      username: string;
      avatarUrl: string | null;
      role: string;
    }>;
    total: number;
  }>;
}

export async function getCrawlerSlots() {
  const res = await authFetch("/api/me/crawler-slots");
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to load crawler slots" }));
    throw new Error(err.detail || "Failed to load crawler slots");
  }
  return res.json() as Promise<{
    userId: string;
    username: string;
    role: string;
    maxConcurrent: number | null;
    active: number;
    available: number;
    canStartMore: boolean;
    isolation: string;
    note: string;
    activeJobs: Array<{
      id: string;
      url: string;
      status: string;
      created_at: string | null;
    }>;
  }>;
}

export async function createPlatformUser(data: {
  username: string;
  password: string;
  email?: string;
  role?: string;
}) {
  const res = await authFetch("/api/auth/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to create user" }));
    throw new Error(err.detail || "Failed to create user");
  }
  return res.json();
}

// ── API tokens (CI/CD) ──

export async function listApiTokens() {
  const res = await authFetch('/api/me/tokens');
  if (!res.ok) throw new Error('Failed to list tokens');
  return res.json() as Promise<{
    tokens: Array<{ id: string; name: string; created_at: string; last_used_at: string | null; is_active: boolean }>;
    total: number;
  }>;
}

export async function createApiToken(name: string) {
  const res = await authFetch('/api/me/tokens', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to create token' }));
    throw new Error(err.detail || 'Failed to create token');
  }
  return res.json() as Promise<{ token: string; record: { id: string; name: string } }>;
}

export async function revokeApiToken(tokenId: string) {
  const res = await authFetch(`/api/me/tokens/${tokenId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to revoke token');
  return res.json();
}

// ── Monitors (scheduled crawls) ──

export interface MonitorRow {
  id: string;
  name: string;
  url: string;
  interval_minutes: number;
  max_pages: number;
  max_depth: number;
  notify_url: string | null;
  is_active: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_job_id: string | null;
}

export async function listMonitors() {
  const res = await authFetch('/api/me/monitors');
  if (!res.ok) throw new Error('Failed to list monitors');
  return res.json() as Promise<{ monitors: MonitorRow[]; total: number }>;
}

export async function createMonitor(data: {
  name: string;
  url: string;
  interval_minutes: number;
  max_pages?: number;
  max_depth?: number;
  notify_url?: string;
}) {
  const res = await authFetch('/api/me/monitors', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to create monitor' }));
    throw new Error(err.detail || 'Failed to create monitor');
  }
  return res.json() as Promise<MonitorRow>;
}

export async function updateMonitor(id: string, patch: Record<string, unknown>) {
  const res = await authFetch(`/api/me/monitors/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error('Failed to update monitor');
  return res.json() as Promise<MonitorRow>;
}

export async function deleteMonitor(id: string) {
  const res = await authFetch(`/api/me/monitors/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete monitor');
  return res.json();
}

export async function runMonitorNow(id: string) {
  const res = await authFetch(`/api/me/monitors/${id}/run-now`, { method: 'POST' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Run failed' }));
    throw new Error(err.detail || 'Run failed');
  }
  return res.json() as Promise<{ submitted: boolean; job_id: string | null; error: string | null }>;
}

// ── Flaky registry ──

export async function listFlakyRegistry(status?: string) {
  const qs = status ? `?status=${encodeURIComponent(status)}` : '';
  const res = await authFetch(`/api/me/flaky-registry${qs}`);
  if (!res.ok) throw new Error('Failed to load flaky registry');
  return res.json() as Promise<{
    registry: Array<{
      id: string; fingerprint: string; title: string; status: string;
      flip_rate: number; samples: number; last_seen_at: string | null;
    }>;
    total: number;
  }>;
}

export async function confirmFlaky(fingerprint: string) {
  const res = await authFetch(`/api/me/flaky-registry/${encodeURIComponent(fingerprint)}/confirm`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to confirm');
  return res.json();
}

export async function resetFlaky(fingerprint: string) {
  const res = await authFetch(`/api/me/flaky-registry/${encodeURIComponent(fingerprint)}/reset`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to reset');
  return res.json();
}

// ── Projects & visual-baseline diffing ──

export interface ProjectRow {
  id: string;
  name: string;
  slug: string;
  base_url: string;
  default_branch: string;
  viewports: Array<{ width: number; height: number }>;
  ignore_regions: Array<Record<string, unknown>>;
  threshold: number;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface BaselineRow {
  id: string;
  branch: string;
  url: string;
  browser: string;
  image_hash: string;
  status: string;
  created_at: string | null;
}

export interface CompareResult {
  url: string;
  browser: string;
  verdict: string;
  mismatch_percentage?: number;
  changed_pixels?: number;
  total_pixels?: number;
  width?: number;
  height?: number;
  threshold?: number;
  ignore_regions_applied?: Array<{ x: number; y: number; width: number; height: number }>;
  error?: string | null;
  baseline_key?: string;
  current_key?: string;
  diff_key?: string;
}

export async function listProjects() {
  const res = await authFetch('/api/projects');
  if (!res.ok) throw new Error('Failed to list projects');
  return res.json() as Promise<{ projects: ProjectRow[]; total: number }>;
}

export async function createProject(data: {
  name: string;
  base_url: string;
  slug?: string;
  default_branch?: string;
  viewports?: Array<{ width: number; height: number }>;
  ignore_regions?: Array<Record<string, unknown>>;
  threshold?: number;
}) {
  const res = await authFetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to create project' }));
    throw new Error(typeof err.detail === 'string' ? err.detail : 'Failed to create project');
  }
  return res.json() as Promise<ProjectRow>;
}

export async function getProject(projectId: string) {
  const res = await authFetch(`/api/projects/${projectId}`);
  if (!res.ok) throw new Error('Project not found');
  return res.json() as Promise<ProjectRow>;
}

export async function updateProject(projectId: string, patch: Record<string, unknown>) {
  const res = await authFetch(`/api/projects/${projectId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to update project' }));
    throw new Error(typeof err.detail === 'string' ? err.detail : 'Failed to update project');
  }
  return res.json() as Promise<ProjectRow>;
}

export async function deleteProject(projectId: string) {
  const res = await authFetch(`/api/projects/${projectId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete project');
  return res.json();
}

export async function captureBaseline(projectId: string, jobId: string, branch?: string) {
  const res = await authFetch(`/api/projects/${projectId}/baseline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, branch }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to capture baseline' }));
    throw new Error(typeof err.detail === 'string' ? err.detail : 'Failed to capture baseline');
  }
  return res.json() as Promise<{ branch: string; baselines_created: number; baselines: Array<Record<string, unknown>> }>;
}

export async function listBaselines(projectId: string, branch?: string) {
  const qs = branch ? `?branch=${encodeURIComponent(branch)}` : '';
  const res = await authFetch(`/api/projects/${projectId}/baselines${qs}`);
  if (!res.ok) throw new Error('Failed to list baselines');
  return res.json() as Promise<{ baselines: BaselineRow[]; total: number }>;
}

export async function compareProject(projectId: string, jobId: string, branch?: string) {
  const res = await authFetch(`/api/projects/${projectId}/compare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, branch }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to compare' }));
    throw new Error(typeof err.detail === 'string' ? err.detail : 'Failed to compare');
  }
  return res.json() as Promise<{
    compare_id: string;
    branch: string;
    regressions: number;
    matches: number;
    new_baselines: number;
    total: number;
    results: CompareResult[];
  }>;
}

export async function getCompare(projectId: string, compareId: string) {
  const res = await authFetch(`/api/projects/${projectId}/compare/${compareId}`);
  if (!res.ok) throw new Error('Compare not found');
  return res.json() as Promise<{
    compare_id: string;
    branch: string;
    job_id: string | null;
    regressions: number;
    matches: number;
    new_baselines: number;
    status: string;
    created_at: string | null;
    results: CompareResult[];
  }>;
}

export async function approveCompare(projectId: string, compareId: string) {
  const res = await authFetch(`/api/projects/${projectId}/compare/${compareId}/approve`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to approve compare');
  return res.json() as Promise<{ status: string; promoted: number }>;
}

// Fetch an S3 artifact through the tenant-checked proxy, returning a blob URL.
// (The proxy requires an Authorization header, so we can't use a plain <img src>.)
export async function fetchArtifactBlobUrl(projectId: string, key: string): Promise<string> {
  const res = await authFetch(`/api/projects/${projectId}/artifact/${encodeURIComponent(key)}`);
  if (!res.ok) throw new Error('Artifact not found');
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// ── Journeys (multi-step user flows) ──

export interface JourneyStep {
  action: string;
  selector?: string;
  value?: string;
  url?: string;
  text?: string;
  expect?: string;
  ms?: number;
  username?: string;
  password?: string;
  label?: string;
}

export interface JourneyRow {
  id: string;
  job_id: string | null;
  name: string;
  description: string;
  status: string;
  steps: JourneyStep[];
  results: Record<string, unknown>;
  created_at: string | null;
}

export async function listJourneys() {
  const res = await authFetch('/api/journeys');
  if (!res.ok) throw new Error('Failed to list journeys');
  return res.json() as Promise<{ journeys: JourneyRow[]; total: number }>;
}

export async function createJourney(data: {
  job_id: string;
  name: string;
  description?: string;
  steps: JourneyStep[];
  browser_matrix?: { chromium: boolean; firefox: boolean; webkit: boolean };
  stop_on_failure?: boolean;
}) {
  const res = await authFetch('/api/journeys', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to create journey' }));
    throw new Error(typeof err.detail === 'string' ? err.detail : 'Failed to create journey');
  }
  return res.json() as Promise<JourneyRow>;
}

export async function getJourney(journeyId: string) {
  const res = await authFetch(`/api/journeys/${journeyId}`);
  if (!res.ok) throw new Error('Journey not found');
  return res.json() as Promise<JourneyRow>;
}

export async function deleteJourney(journeyId: string) {
  const res = await authFetch(`/api/journeys/${journeyId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete journey');
  return res.json();
}

export async function runJourney(journeyId: string) {
  const res = await authFetch(`/api/journeys/${journeyId}/run`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to run journey');
  return res.json() as Promise<{ run_id: string; journey_id: string; status: string }>;
}

export async function getJourneyRun(journeyId: string, runId: string) {
  const res = await authFetch(`/api/journeys/${journeyId}/run/${runId}`);
  if (!res.ok) throw new Error('Run not found');
  return res.json() as Promise<{
    run_id: string;
    status: string;
    results: Record<string, { passed: boolean; error?: string; steps: Array<{ index: number; action: string; status: string; error: string; duration_ms: number }> }>;
    error: string;
  }>;
}

// ── Devices ──

export interface DeviceRow {
  name: string;
  viewport: { width: number; height: number };
  device_scale_factor: number;
  is_mobile: boolean;
  has_touch: boolean;
  default_browser: string;
}

export async function listDevices() {
  const res = await fetch(`${API_BASE}/api/devices`);
  if (!res.ok) throw new Error('Failed to load devices');
  return res.json() as Promise<{ devices: DeviceRow[]; total: number }>;
}

// ── Tunnels ──

export interface TunnelRow {
  id: string;
  name: string;
  tunnel_token: string;
  target_origin: string;
  status: string;
  target_url: string;
  expires_at: string | null;
  created_at: string | null;
}

export async function listTunnels() {
  const res = await authFetch('/api/tunnels');
  if (!res.ok) throw new Error('Failed to list tunnels');
  return res.json() as Promise<{ tunnels: TunnelRow[]; total: number }>;
}

export async function createTunnel(data: { name: string; target_origin?: string }) {
  const res = await authFetch('/api/tunnels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to create tunnel' }));
    throw new Error(typeof err.detail === 'string' ? err.detail : 'Failed to create tunnel');
  }
  return res.json() as Promise<TunnelRow & { agent_token: string }>;
}

export async function revokeTunnel(tunnelId: string) {
  const res = await authFetch(`/api/tunnels/${tunnelId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to revoke tunnel');
  return res.json();
}

// ── Analytics ──

export interface TrendsSeriesPoint {
  date: string;
  crawls: number;
  completed: number;
  failed: number;
  issues: number;
  blocking: number;
  functional: number;
  visual: number;
  advisory: number;
  pages_visited: number;
}

export async function getAnalyticsTrends(days = 30) {
  const res = await authFetch(`/api/analytics/trends?days=${days}`);
  if (!res.ok) throw new Error('Failed to load trends');
  return res.json() as Promise<{
    days: number;
    series: TrendsSeriesPoint[];
    totals: { crawls: number; issues: number; pages_visited: number; pass_rate: number | null };
  }>;
}

export async function getAnalyticsIssues(days = 30) {
  const res = await authFetch(`/api/analytics/issues?days=${days}`);
  if (!res.ok) throw new Error('Failed to load issue breakdowns');
  return res.json() as Promise<{
    days: number;
    by_severity: Record<string, number>;
    by_category: Record<string, number>;
    top_recurring: Array<{ fingerprint: string; title: string; occurrences: number; severity: string; last_seen: string | null }>;
  }>;
}

export async function getAnalyticsTargets(days = 30) {
  const res = await authFetch(`/api/analytics/targets?days=${days}`);
  if (!res.ok) throw new Error('Failed to load target rollups');
  return res.json() as Promise<{
    days: number;
    total: number;
    targets: Array<{
      url: string;
      crawls: number;
      completed: number;
      issues: number;
      distinct_fingerprints: number;
      avg_issues_per_crawl: number;
      last_crawl_status: string | null;
      last_crawled_at: string | null;
    }>;
  }>;
}

export async function getAnalyticsFlaky(days = 30) {
  const res = await authFetch(`/api/analytics/flaky?days=${days}`);
  if (!res.ok) throw new Error('Failed to load flaky stats');
  return res.json() as Promise<{
    days: number;
    totals: { registry_size: number; quarantined: number; suspected: number; new_in_window: number };
    series: Array<{ date: string; new: number }>;
    top_flaky: Array<{ fingerprint: string; title: string; status: string; flip_rate: number; samples: number }>;
  }>;
}
