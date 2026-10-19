# Spec: Device Emulation Matrix

## Objective

Add a curated set of **Playwright device descriptors** to the browser matrix so a
crawl/journey can run with iPhone/Pixel/iPad viewport, user-agent, touch, and
device-pixel-ratio — honest self-hosted scope (NOT real hardware). Today the
browser factory only supports `viewport_width/height` + optional `user_agent`.

## Non-goals

- Real physical devices / device farm (separate product line).
- Per-device network throttling (future).

## Design

Playwright ships a `devices` registry (`playwright.devices["iPhone 13"]`,
`"Pixel 5"`, `"iPad (gen 7)"`, `"Desktop Chrome HiDPI"`, etc.). Each descriptor
provides `user_agent`, `viewport`, `device_scale_factor`, `is_mobile`,
`has_touch`, and `default_browser_type`.

`BrowserConfig` gains an optional `device: str | None`. When set, `create_context`
applies the descriptor's `viewport`/`user_agent`/`device_scale_factor`/
`is_mobile`/`has_touch` to the context, overriding the flat width/height/UA.
A `device` that doesn't match a known descriptor name falls back gracefully to
the explicit viewport (never raises).

## Data model / API changes

- `CreateJobRequest` + `BrowserMatrix` gain `device: str | None` (e.g. `"iPhone 13"`).
  Stored on `Job.config["device"]` (additive, no migration) and exposed in
  `JobResponse`.
- `GET /api/devices` returns the curated list of supported descriptors
  (name, viewport, is_mobile, has_touch, default browser) — a small allow-list
  mapping descriptor → default engine, so the UI can offer a dropdown.

## Files

```
src/browser/factory.py        EXTEND (device descriptors in create_context)
src/api/app.py                EXTEND (GET /api/devices; device passthrough in create_job)
tests/test_browser_factory.py EXTEND (device context options)
tests/test_api.py             EXTEND (GET /api/devices)
frontend/src/lib/api.ts       EXTEND (listDevices)
frontend/.../_layout.new-test.tsx EXTEND (device dropdown)
```

## Success criteria

1. `GET /api/devices` lists iPhone/Pixel/iPad + desktop HiDPI with correct viewport/touch flags.
2. `create_context` with `device="iPhone 13"` produces a context whose
   `viewport`/`user_agent`/`is_mobile` match the descriptor (unit test).
3. Unknown device name falls back to explicit viewport without error.
4. A job submitted with `device="Pixel 5"` runs and the resulting crawl reflects
   the mobile viewport (integration/E2E).
5. Full suite + ruff green.
