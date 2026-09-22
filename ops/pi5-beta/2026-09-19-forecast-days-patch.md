# 2026-09-19 — pi5-beta: restore the 7th forecast day (Open-Meteo `forecast_days`)

**Device:** `pi5-beta` · **Follows:** `2026-09-19-now-playing-1.1.1.md` (same-day
upgrade) · **Status:** APPLIED + VERIFIED

## Symptom

After the Now Playing `1.0.6` → `1.1.1` upgrade, the idle screen rendered
**TODAY + 6** forecast days. Before the upgrade it rendered **TODAY + 7**.

## Root cause

Not a layout regression — a data regression introduced by the weather-provider
migration, which is an off-by-one the migration did not carry over.

| Version | Provider | Requested | Then | Client showed |
|---|---|---|---|---|
| `1.0.6` | OpenWeatherMap | One Call `daily` (8 entries) | `return forecast.slice(1); // First day of forecast is actually current day` (`src/lib/api/WeatherAPI.ts`) | TODAY + **7** |
| `1.1.1` | Open-Meteo | `forecast_days: 7` (`src/lib/api/open-meteo/index.ts:129`) | no slice — the first returned day *is* today | TODAY + **6** |

So the old code was correct by arithmetic: 8 days fetched, minus today = 7 forecast
days. The rewrite kept "today is the first entry" but dropped the request size to 7,
silently consuming one forecast day.

## How it was isolated

1. Diffed the compiled client CSS between the two versions — the **only** change was
   an unrelated track-info "audio format text" refactor. The weather area and
   forecast row CSS were untouched, ruling out a layout change.
2. Diffed the provider request code between the two versions — found the `slice(1)`
   / `forecast_days: 7` mismatch above.

## Fix

`forecast_days: 7` → `8`, applied to **both** the compiled file (what actually runs)
and the TypeScript source, so they stay consistent. Artifact:
`patches/now-playing-open-meteo-forecast-days.patch`.

Open-Meteo supports up to 16 forecast days, so 8 is well within the free API, and no
client-side change is needed.

## Verification

| Check | Result |
|---|---|
| Backend restarted, plugin reloaded | `:3000` up in 4 s, plugin app on `:4004` up in 6 s |
| Running file carries the patch | `forecast_days: 8` at `dist/lib/api/open-meteo/index.js:132` |
| Rendered idle screen | **TODAY, SUN, MON, TUE, WED, THU, FRI, SAT — 8 columns**, i.e. TODAY + 7 |

## Notes for the next person

- The backend must be **restarted** for this to take effect: there is no
  `require.cache` invalidation anywhere in `app/`, so disabling/re-enabling the
  plugin at runtime does **not** reload its code.
- After a backend restart the kiosk shows a stale page until Chromium reloads, so the
  kiosk was restarted too (`systemctl restart volumio-kiosk`, ~21 s to repaint).
- **A plugin update will overwrite this patch** — the file lives in the plugin tree,
  not in a stable overlay. Re-apply and re-verify after any Now Playing upgrade.
- Rollback: the pre-patch file is kept on the device as
  `np-backups/open-meteo-index.js.orig-1.1.1`; restore it and restart the backend.

## Upstream

This looks like a genuine upstream defect (a forecast day silently lost in the
provider migration), but **nothing has been filed**. Proposing it to
`patrickkfkan/volumio-now-playing` is a separate, explicit decision.
