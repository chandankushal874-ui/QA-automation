import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { getJob, getJobReport, downloadAuthenticatedFile, stopJob } from '@/lib/api'
import { useEffect, useState, Fragment, useCallback } from 'react'
import { AlertTriangle, CheckCircle, ExternalLink, Globe, Monitor, Terminal, PlayCircle, Download, Table2, Image as ImageIcon, ChevronDown, ChevronRight, Square, Loader2 } from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

export const Route = createFileRoute('/_app/_auth/dashboard/_layout/report/$testJobId')({
  component: ReportPage,
})

function ReportPage() {
  const { testJobId } = Route.useParams()

  const { data: job, isLoading: jobLoading } = useQuery({
    queryKey: ['job', testJobId],
    queryFn: () => getJob(testJobId),
    refetchInterval: 2_000,
  })

  const { data: report } = useQuery({
    queryKey: ['job-report', testJobId],
    queryFn: () => getJobReport(testJobId),
    enabled: !!job && (job.status === 'completed' || job.status === 'failed'),
  })

  const issues = report?.issues || []
  const endpoints = report?.endpoints || []

  const [errorTypeSummary, setErrorTypeSummary] = useState<Record<string, number>>({})
  const [reportHtmlUrl, setReportHtmlUrl] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'issues' | 'endpoints' | 'errortypes'>('errortypes')
  const [expandedEndpoints, setExpandedEndpoints] = useState<Set<string>>(new Set())
  const [isStopping, setIsStopping] = useState(false)

  const handleStop = useCallback(async () => {
    if (!confirm('Are you sure you want to stop this test? The crawl will be cancelled immediately.')) return
    setIsStopping(true)
    try {
      await stopJob(testJobId)
    } catch (err: any) {
      alert(err.message || 'Failed to stop job')
    } finally {
      setIsStopping(false)
    }
  }, [testJobId])

  // When report data arrives, extract error type summary and report URL
  useEffect(() => {
    if (!report) return
    if (report.error_type_summary) {
      setErrorTypeSummary(report.error_type_summary as Record<string, number>)
    }
    if (report.report_html_url) {
      setReportHtmlUrl(`${API_BASE}${report.report_html_url}`)
    }
  }, [report])


  if (jobLoading) {
    return <div className="flex h-full items-center justify-center p-8 text-primary/60">Loading report...</div>
  }
  if (!job) {
    return <div className="flex h-full items-center justify-center p-8 text-red-500">Report not found</div>
  }

  // issues/endpoints may still be loading (undefined) — that's fine, we handle it below

  return (
    <div className="flex h-full w-full flex-col overflow-y-auto bg-secondary px-6 py-8 dark:bg-black">
      <div className="mx-auto flex w-full max-w-screen-xl flex-col gap-6">
        
        {/* Header Section */}
        <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-6">
          <div className="flex items-start justify-between">
            <div className="flex flex-col gap-1">
              <h1 className="text-2xl font-bold text-primary">Test Report</h1>
              <a href={job.url} target="_blank" rel="noreferrer" className="flex items-center gap-2 text-primary/60 hover:text-blue-500 transition-colors">
                <Globe className="h-4 w-4" />
                <span>{job.url}</span>
                <ExternalLink className="h-3 w-3" />
              </a>
            </div>
            
            <div className={`px-4 py-1.5 rounded-full text-sm font-medium uppercase tracking-wider
              ${job.status === 'completed' ? 'bg-green-500/10 text-green-500 border border-green-500/20' : 
                job.status === 'failed' ? 'bg-red-500/10 text-red-500 border border-red-500/20' : 
                job.status === 'running' ? 'bg-blue-500/10 text-blue-500 border border-blue-500/20 animate-pulse' : 
                job.status === 'cancelled' ? 'bg-gray-500/10 text-gray-500 border border-gray-500/20' :
                'bg-gray-500/10 text-gray-500 border border-gray-500/20'}`}>
              {job.status}
            </div>
          </div>
          
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold uppercase tracking-wider text-primary/40">Pages Discovered</span>
              <span className="text-xl font-bold text-primary">{job.pages_discovered}</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold uppercase tracking-wider text-primary/40">Pages Visited</span>
              <span className="text-xl font-bold text-primary">{job.pages_visited}</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold uppercase tracking-wider text-primary/40">Issues Found</span>
              <span className="text-xl font-bold text-red-500">{job.issues_found}</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold uppercase tracking-wider text-primary/40">Endpoints</span>
              <span className="text-xl font-bold text-blue-500">{job.endpoints_discovered}</span>
            </div>
          </div>
        </div>

        {/* Live Running State */}
        {(job.status === 'pending' || job.status === 'running') && (
          <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-border bg-card p-12 text-center">
             <div className="relative flex h-16 w-16 items-center justify-center">
                <div className="absolute h-full w-full animate-ping rounded-full bg-blue-500/20"></div>
                <PlayCircle className="h-10 w-10 text-blue-500 animate-pulse" />
             </div>
             <div className="flex flex-col gap-2">
                <h3 className="text-xl font-medium text-primary">Test is currently running...</h3>
                <p className="text-primary/60 max-w-md mx-auto">The backend algorithm is actively crawling the site, analyzing the DOM, and detecting issues. This report will automatically update when the test finishes.</p>
             </div>
             <button
               onClick={handleStop}
               disabled={isStopping}
               className="flex items-center gap-2 rounded-md bg-red-500/10 px-4 py-2 text-sm font-medium text-red-500 border border-red-500/20 hover:bg-red-500/20 transition-colors disabled:opacity-50"
             >
               {isStopping ? (
                 <Loader2 className="h-4 w-4 animate-spin" />
               ) : (
                 <Square className="h-4 w-4" />
               )}
               {isStopping ? 'Stopping...' : 'Stop Test'}
             </button>
          </div>
        )}

        {/* Cancelled State */}
        {job.status === 'cancelled' && (
          <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-border bg-card p-12 text-center">
             <div className="flex h-16 w-16 items-center justify-center rounded-full bg-gray-500/10">
                <AlertTriangle className="h-10 w-10 text-gray-500" />
             </div>
             <div className="flex flex-col gap-2">
                <h3 className="text-xl font-medium text-primary">Test was cancelled</h3>
                <p className="text-primary/60 max-w-md mx-auto">This test was stopped before completion. You can resume it or start a new test.</p>
             </div>
          </div>
        )}

        {/* Final Report — shown when job is completed or failed */}
        {(job.status === 'completed' || job.status === 'failed') && (
          <div className="flex flex-col gap-4">

            {/* Download Bar */}
            <div className="flex items-center justify-between rounded-lg border border-border bg-card p-4">
              <div className="flex items-center gap-2">
                <CheckCircle className="h-5 w-5 text-green-500" />
                <span className="text-sm font-medium text-primary">
                  Test {job.status === 'completed' ? 'completed' : 'failed'} — Full report ready
                </span>
              </div>
              {reportHtmlUrl && (
                <button
                  onClick={async () => {
                    try {
                      await downloadAuthenticatedFile(
                        `/jobs/${testJobId}/report/download`,
                        `report_${testJobId}.html`,
                      )
                    } catch (err: any) {
                      alert(err.message || 'Failed to download report')
                    }
                  }}
                  className="flex items-center gap-2 rounded-md bg-blue-500/10 px-4 py-2 text-sm font-medium text-blue-500 hover:bg-blue-500/20 transition-colors"
                >
                  <Download className="h-4 w-4" />
                  Download HTML Report
                </button>
              )}
            </div>

            {/* Tab Navigation */}
            <div className="flex gap-2 rounded-lg border border-border bg-card p-2">
              <button
                onClick={() => setActiveTab('errortypes')}
                className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === 'errortypes' ? 'bg-blue-500/10 text-blue-500' : 'text-primary/60 hover:text-primary'
                }`}
              >
                <Table2 className="h-4 w-4" />
                Error Type Breakdown
              </button>
              <button
                onClick={() => setActiveTab('issues')}
                className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === 'issues' ? 'bg-blue-500/10 text-blue-500' : 'text-primary/60 hover:text-primary'
                }`}
              >
                <AlertTriangle className="h-4 w-4" />
                Issues ({issues?.length || 0})
              </button>
              <button
                onClick={() => setActiveTab('endpoints')}
                className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === 'endpoints' ? 'bg-blue-500/10 text-blue-500' : 'text-primary/60 hover:text-primary'
                }`}
              >
                <Globe className="h-4 w-4" />
                Endpoints ({endpoints?.length || 0})
              </button>
            </div>

            {/* Tab: Error Type Breakdown */}
            {activeTab === 'errortypes' && (
              <div className="rounded-lg border border-border bg-card p-6">
                <h2 className="mb-4 text-xl font-medium text-primary flex items-center gap-2">
                  <Table2 className="h-5 w-5 text-blue-500" />
                  Error Type Breakdown
                </h2>
                {Object.keys(errorTypeSummary).length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border text-left text-xs font-semibold uppercase tracking-wider text-primary/40">
                          <th className="pb-3 pr-4">Error Type</th>
                          <th className="pb-3 pr-4">Category</th>
                          <th className="pb-3 pr-4 text-right">Occurrences</th>
                          <th className="pb-3 pr-4">Severity</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(errorTypeSummary)
                          .sort(([, a], [, b]) => b - a)
                          .map(([type, count]) => {
                            const categoryLabels: Record<string, string> = {
                              js_error: 'JavaScript Runtime',
                              network_failure: 'Network',
                              http_error: 'HTTP Error',
                              console_error: 'Console Error',
                              cors_error: 'CORS',
                              csp_violation: 'CSP Violation',
                              visual_regression: 'Visual Regression',
                              accessibility: 'Accessibility',
                              performance: 'Performance',
                              security: 'Security',
                              mixed_content: 'Mixed Content',
                              resource_load_failure: 'Resource Load',
                              framework_error: 'Framework Error',
                            }
                            const severityLevels: Record<string, string> = {
                              js_error: 'Blocking',
                              network_failure: 'Functional',
                              http_error: 'Functional',
                              cors_error: 'Functional',
                              csp_violation: 'Security',
                              security: 'Blocking',
                              framework_error: 'Blocking',
                            }
                            const label = categoryLabels[type] || type
                            const severity = severityLevels[type] || 'Advisory'
                            return (
                              <tr key={type} className="border-b border-border/50 hover:bg-secondary/30 dark:hover:bg-black/30">
                                <td className="py-3 pr-4 font-mono text-xs text-primary">{type}</td>
                                <td className="py-3 pr-4 text-primary/80">{label}</td>
                                <td className="py-3 pr-4 text-right">
                                  <span className="inline-flex items-center rounded-full bg-red-500/10 px-2.5 py-1 text-xs font-bold text-red-500">
                                    {count}
                                  </span>
                                </td>
                                <td className="py-3 pr-4">
                                  <span className={`text-xs font-medium ${
                                    severity === 'Blocking' ? 'text-red-500' :
                                    severity === 'Functional' ? 'text-orange-500' :
                                    'text-yellow-500'
                                  }`}>
                                    {severity}
                                  </span>
                                </td>
                              </tr>
                            )
                          })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="flex h-32 items-center justify-center rounded-md border border-dashed border-border/60 text-sm text-primary/40">
                    No error type data available
                  </div>
                )}
              </div>
            )}

            {/* Tab: Issues with Screenshots */}
            {activeTab === 'issues' && (
              <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-6">
                <h2 className="text-xl font-medium text-primary flex items-center gap-2">
                  <AlertTriangle className="h-5 w-5 text-orange-500" />
                  Detected Issues ({issues?.length || 0})
                </h2>
                
                {issues && issues.length > 0 ? (
                  <div className="mt-4 flex flex-col gap-4">
                    {issues.map((issue: any) => (
                      <div key={issue.fingerprint || issue.id} className="flex flex-col gap-3 rounded-md border border-border bg-secondary/30 p-4 dark:bg-black/30 transition-colors hover:border-primary/30">
                        <div className="flex items-start justify-between">
                          <div className="flex flex-col gap-1">
                            <span className="text-sm font-semibold text-primary">{issue.title}</span>
                            <span className="text-xs text-primary/60">{issue.description}</span>
                          </div>
                          <div className={`px-2 py-1 text-xs font-bold uppercase tracking-wider rounded
                            ${issue.severity === 'blocking' ? 'bg-red-500/20 text-red-500' :
                              issue.severity === 'functional' ? 'bg-orange-500/20 text-orange-500' :
                              issue.severity === 'visual' ? 'bg-yellow-500/20 text-yellow-500' :
                              'bg-blue-500/20 text-blue-500'}`}>
                            {issue.severity}
                          </div>
                        </div>
                        
                        <div className="flex items-center gap-4 text-xs text-primary/50">
                          <span className="flex items-center gap-1">
                            <Terminal className="h-3 w-3" />
                            {issue.category}
                          </span>
                          {issue.first_seen_url && (
                            <span className="flex items-center gap-1">
                              <Globe className="h-3 w-3" />
                              <span className="truncate max-w-[200px]">{issue.first_seen_url}</span>
                            </span>
                          )}
                          {issue.browser_count > 0 && (
                            <span className="flex items-center gap-1">
                              <Monitor className="h-3 w-3" />
                              {issue.browser_count} browser{issue.browser_count > 1 ? 's' : ''}
                            </span>
                          )}
                        </div>

                        {issue.error_message && (
                          <div className="mt-2 rounded bg-black/50 p-2 text-xs font-mono text-red-400 overflow-x-auto">
                            {issue.error_message}
                          </div>
                        )}

                        {/* AI Fix Status */}
                        {issue.fix_status && issue.fix_status !== 'unfixed' && (
                          <div className="mt-3 flex flex-col gap-2 rounded-md border border-border/60 bg-secondary/40 p-3 dark:bg-black/40">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className={`px-2 py-0.5 text-xs font-bold uppercase tracking-wider rounded
                                ${issue.fix_status === 'verified' ? 'bg-green-500/20 text-green-500' :
                                  issue.fix_status === 'suggested' ? 'bg-yellow-500/20 text-yellow-500' :
                                  issue.fix_status === 'pr_opened' ? 'bg-blue-500/20 text-blue-500' :
                                  issue.fix_status === 'resolved' ? 'bg-emerald-500/20 text-emerald-500' :
                                  'bg-gray-500/20 text-gray-500'}`}>
                                {issue.fix_status === 'verified' ? '✓ Verified' :
                                 issue.fix_status === 'suggested' ? '⚠ Suggested' :
                                 issue.fix_status}
                              </span>
                              {typeof issue.fix_confidence === 'number' && (
                                <span className="text-xs text-primary/60">
                                  Confidence: {Math.round(issue.fix_confidence * 100)}%
                                </span>
                              )}
                              <span className="text-xs text-primary/40">
                                {issue.fix_status === 'verified'
                                  ? 'Fix was replayed against the live site in an isolated sandbox and the original issue disappeared.'
                                  : issue.fix_status === 'suggested'
                                  ? 'AI-generated fix suggestion — review before applying.'
                                  : ''}
                              </span>
                            </div>
                            {issue.fix_diff && (
                              <details className="mt-1">
                                <summary className="cursor-pointer text-xs font-medium text-blue-500 hover:text-blue-400">
                                  Show AI fix patch
                                </summary>
                                <pre className="mt-2 max-h-64 overflow-auto rounded bg-black/60 p-3 text-xs font-mono text-green-400 whitespace-pre-wrap">
                                  {issue.fix_diff}
                                </pre>
                              </details>
                            )}
                          </div>
                        )}

                        {/* Screenshot */}
                        {issue.screenshot_path && (
                          <div className="mt-2">
                            <div className="mb-1 flex items-center gap-1 text-xs text-primary/40">
                              <ImageIcon className="h-3 w-3" />
                              Screenshot
                            </div>
                            <img
                              src={`${API_BASE}/screenshots/${issue.screenshot_path.replace(/^reports\/snapshots\//, '').replace(/^reports\//, '')}`}
                              alt="Issue screenshot"
                              className="max-h-64 rounded border border-border object-contain"
                              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                            />
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="flex h-32 items-center justify-center rounded-md border border-dashed border-border/60 text-sm text-primary/40">
                    No issues detected! 🎉
                  </div>
                )}
              </div>
            )}

            {/* Tab: Endpoints Table */}
            {activeTab === 'endpoints' && (
              <div className="rounded-lg border border-border bg-card p-6">
                <h2 className="mb-4 text-xl font-medium text-primary flex items-center gap-2">
                  <Globe className="h-5 w-5 text-blue-500" />
                  Endpoints Tested ({endpoints?.length || 0})
                </h2>
                {endpoints && endpoints.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border text-left text-xs font-semibold uppercase tracking-wider text-primary/40">
                          <th className="pb-3 pr-4">URL</th>
                          <th className="pb-3 pr-4">Method</th>
                          <th className="pb-3 pr-4">Status Code</th>
                          <th className="pb-3 pr-4">Type</th>
                          <th className="pb-3 pr-4">Depth</th>
                          <th className="pb-3 pr-4">Auth</th>
                          <th className="pb-3 pr-4">Errors</th>
                        </tr>
                      </thead>
                      <tbody>
                        {endpoints.map((ep: any, i: number) => {
                          const epKey = `${ep.url}-${i}`
                          const isExpanded = expandedEndpoints.has(epKey)
                          const hasErrors = ep.consoleErrors && Object.keys(ep.consoleErrors).length > 0
                          return (
                            <Fragment key={epKey}>
                              <tr
                                className="border-b border-border/50 hover:bg-secondary/30 dark:hover:bg-black/30 cursor-pointer"
                                onClick={() => {
                                  const newSet = new Set(expandedEndpoints)
                                  if (isExpanded) newSet.delete(epKey)
                                  else newSet.add(epKey)
                                  setExpandedEndpoints(newSet)
                                }}
                              >
                                <td className="py-3 pr-4">
                                  <div className="flex items-center gap-1">
                                    {hasErrors ? (
                                      isExpanded ? <ChevronDown className="h-3 w-3 text-primary/40" /> : <ChevronRight className="h-3 w-3 text-primary/40" />
                                    ) : null}
                                    <span className="truncate max-w-[300px] font-mono text-xs text-primary">{ep.url}</span>
                                  </div>
                                </td>
                                <td className="py-3 pr-4">
                                  <span className="rounded bg-blue-500/10 px-2 py-0.5 text-xs font-mono text-blue-500">{ep.method}</span>
                                </td>
                                <td className="py-3 pr-4">
                                  {ep.statusCode ? (
                                    <span className={`text-xs font-bold ${
                                      ep.statusCode >= 200 && ep.statusCode < 300 ? 'text-green-500' :
                                      ep.statusCode >= 300 && ep.statusCode < 400 ? 'text-blue-500' :
                                      ep.statusCode >= 400 && ep.statusCode < 500 ? 'text-orange-500' :
                                      ep.statusCode >= 500 ? 'text-red-500' : 'text-primary/60'
                                    }`}>
                                      {ep.statusCode}
                                    </span>
                                  ) : (
                                    <span className="text-xs text-primary/30">—</span>
                                  )}
                                </td>
                                <td className="py-3 pr-4 text-xs text-primary/60">{ep.endpointType || '—'}</td>
                                <td className="py-3 pr-4 text-xs text-primary/60">{ep.depth}</td>
                                <td className="py-3 pr-4">
                                  {ep.requiresAuth ? (
                                    <span className="text-xs text-orange-500">Yes</span>
                                  ) : (
                                    <span className="text-xs text-primary/30">No</span>
                                  )}
                                </td>
                                <td className="py-3 pr-4">
                                  {hasErrors ? (
                                    <span className="inline-flex items-center rounded-full bg-red-500/10 px-2 py-0.5 text-xs font-bold text-red-500">
                                      {Object.keys(ep.consoleErrors).length}
                                    </span>
                                  ) : (
                                    <span className="text-xs text-green-500">✓</span>
                                  )}
                                </td>
                              </tr>
                              {isExpanded && hasErrors && (
                                <tr>
                                  <td colSpan={7} className="pb-3 pr-4">
                                    <div className="ml-6 rounded bg-black/50 p-3 text-xs font-mono text-red-400 overflow-x-auto">
                                      <pre className="whitespace-pre-wrap">{JSON.stringify(ep.consoleErrors, null, 2)}</pre>
                                    </div>
                                  </td>
                                </tr>
                              )}
                            </Fragment>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="flex h-32 items-center justify-center rounded-md border border-dashed border-border/60 text-sm text-primary/40">
                    No endpoints discovered
                  </div>
                )}
              </div>
            )}

          </div>
        )}

      </div>
    </div>
  )
}