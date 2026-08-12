export type User = {
  id: string;
  username?: string;
  email?: string;
  avatarUrl?: string;
  role?: "admin" | "user" | string;
};

export type TestJob = {
  id: string;
  url: string;
  status: string;
  created_at: string;
  pages_discovered: number;
  pages_visited: number;
  issues_found: number;
  endpoints_discovered: number;
  console_errors_found: number;
  browser_matrix: { chromium: boolean; firefox: boolean; webkit: boolean };
};

export type TestIssue = {
  fingerprint: string;
  title: string;
  description: string;
  severity: string;
  category: string;
  browsers_affected: { chromium: boolean; firefox: boolean; webkit: boolean };
  browser_count: number;
  occurrence_count: number;
  first_seen_url: string;
  error_message: string;
  stack_trace: string;
  source_file?: string;
  source_line?: number;
  fix_status: string;
  fix_diff?: string;
  fix_confidence?: number;
  screenshot_path?: string;
};

export type TestEndpoint = {
  url: string;
  method: string;
  status: string;
  status_code?: number;
  depth: number;
  discovered_via?: string;
  endpoint_type?: string;
  requires_auth?: boolean;
  console_errors?: Record<string, unknown>;
};
