# Stem → path resolve (P3)

## Purpose

One module of pure `resolve_*(watchdir, stem|sample, …)` helpers so middle, `discover_*`, controllers, and DAMMIF GNOM discovery agree on disk paths for a sample identity.

## Owner

`guisaxs_skills.liveview.session.stem_resolve` — SSOT for stem→role path rules.
`session.output_paths` remains pure family-dir / filename layout.

## Invariants

- **No** `SampleAnalysisContext` (or other new domain type).
- **No** artifact path fields on `Sample` or `history.yaml` (history stays `{path, boarding}` + index + arming).
- Same `(watchdir, stem, sample_path, mode)` ⇒ same paths from every consumer.
- Stable skill names win (`gnom_best.out`, `{stem}_results.txt`, `{stem}_fit_sizes.yml`, …).

## Residual newest-glob fallbacks (documented)

| Role | Stable first | Fallback when missing |
|------|--------------|------------------------|
| fit_distances / fit_sizes `.out` | `gnom_best.out`, `{stem}_gnom.out`, `{stem}.out`, (+ `datgnom_best.out` for distances) | newest-mtime `*.out` under family/`<stem>/` |
| Guinier results | `{stem}_results.txt` | newest `*_results.txt` excluding kratky |
| fit_sizes YAML | `{stem}_fit_sizes.yml` / `{stem}_fit_sizes_log.yml` | legacy `*_fit_sizes_quality.yml` / `*_fit_sizes_best.yml` |
| mixture CSV | `mixture_results.csv` | none |

DAMMIF still filters candidates with `gnom_out_usable_for_dammif` via `discover_gnom_out_path` (hint + shared iterator).

## Consumers

- Middle: `resolve_integrated_dat` / `resolve_subtracted_dat` (thin aliases in `middle_from_stem`).
- Right DISK: `discover_monodisperse_artifacts` / `discover_polydisperse_artifacts`.
- Controllers: `resolve_analysis_profile`.
- Pipeline / presenter DAMMIF: `iter_fit_distances_gnom_candidates` / `resolve_fit_distances_gnom_out`.
