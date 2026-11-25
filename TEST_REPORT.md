# era5pool test report

**Version tested:** 1.1, and 1.2 after the fixes (2026-11-25)
**Where:** DKRZ Levante, cdo 2.2.2, Python 3.10 (1.2 also with Python 3.6)
**Tested by:** the author. The test checklist and the checking scripts were written with Claude (Anthropic).

## Summary

- era5pool extracts the ERA5 data **correctly**. Every output agrees with the raw pool GRIB to
  within 0.00002 K, and with an independent download from the Copernicus Climate Data Store to
  within a bias of 0.01 K.
- All DKRZ time-step definitions (daily mean, sum, minimum and maximum; monthly mean) are confirmed.
- The test found one real problem: long requests used too much memory for a small SLURM job.
  It also found several usability points. All of them are fixed in version 1.2, which gives
  identical data to 1.1.

## Method

Each era5pool command was run on Levante, and its output was compared with a reference that does
**not** go through era5pool or cdo:

1. **The raw pool GRIB**, read with eccodes (Python). The pool files are on a reduced Gaussian
   grid (N320). era5pool converts them to a regular grid with `cdo setgridtype,regular`, which
   interpolates linearly along each latitude row. The checking script repeats this step itself
   in numpy, so the comparison is independent of cdo.
2. **Aggregation from finer time steps**, e.g. the daily value against the 24 hourly values.
3. **Physical plausibility** for known events: the June 2021 Pacific Northwest heatwave, the
   February 2012 European cold wave, and the July 2021 Ahr/Eifel flood.
4. **The Copernicus Climate Data Store (CDS)**: the same fields downloaded from ECMWF, a fully
   independent source.

The test checklist and the checking script are available on request, so the tests can be repeated.

## Results

| # | Test | Result |
|---|---|---|
| 1 | Help, `list`, `regions` | works; several points for clearer help (fixed in 1.2) |
| 2 | Hourly `t2m`, own box, 29 June 2021 | identical to the GRIB (max diff 1.5e-5 K); 24 steps; box edges correct; Lytton grid cell 311 K |
| 3 | Daily mean `t2m` | = mean of the 24 hourly values 00–23 UTC (max diff 0.001 K); stamped 11:30; = GRIB |
| 4 | Monthly `t2m` | June = mean of the 30 daily values (max diff 0.001 K) |
| 5 | Daily `tp`, 14 July 2021 | = sum of the hourly values 01 UTC … 00 UTC next day (max diff 4e-6 m); = GRIB; 105 mm in the Eifel |
| 6 | Daily `mn2t`/`mx2t`, Feb 2012 | DKRZ daily = min/max of hourly 01 … 00 UTC on the original grid (max diff 0.001 K); netCDF = GRIB; minimum −38.4 °C. See finding 2 |
| 7 | `z` at 500 and 850 hPa | exactly these two levels; = GRIB (max diff 0.002 m² s⁻²); z500 up to 5981 gpm (heat dome) |
| 8 | Regions `europe`, `oceania`, `NAO-region` | correct edges; no gap at the date line; = GRIB |
| 9 | 1° grid, conservative remapping | global mean unchanged (3.0238 mm/day native, 1°, and raw GRIB) |
| 10 | `--land-only`, thresholds 0.5 and 0.8 | land values unchanged; sea exactly where the ERA5 land-sea mask is below the threshold (0 of 33 615 cells differ) |
| 11 | ERA5T (`--family ET`), October 2026 | 31 days, = GRIB |
| 12 | `--out` in another folder, `--threads 1` | identical result |
| 13 | Chunking (forced to 3 files per chunk) | identical to an unchunked run; attributes kept; no temporary files left |
| 14 | `--dry-run`, large request | no files written; the job script is printed |
| 15 | Error messages (9 cases) | all clear; no files left behind |
| 16 | Options that only work together | 1.1 silently ignored them (1.2: `--land-threshold` alone asks whether land only is meant; `--remap` without `--grid` is an error) |
| 17 | Comparison with the CDS | `t2m`: bias 0.013 K, RMS 0.32 K; `z500`: bias −0.01 m² s⁻², RMS 0.87 m² s⁻² (remaining difference: CDS 0.25° grid vs pool N320 grid) |

All checks were repeated with version 1.2, with the same results.

## Large requests (SLURM, shared partition, 4 CPUs × 940 MB)

| Request | Files | Before compression | File | Run time | Peak memory |
|---|---|---|---|---|---|
| 1 month hourly `t2m`, global | 31 | 2.4 GB | 1.6 GB | 1.0 min | 0.6 GB |
| 1 year daily `z`, 3 levels, global | 12 | 3.6 GB | 2.5 GB | 1.5 min | 0.3 GB |
| 76 years monthly `tp`, 1°, conservative | 76 | 0.24 GB | 0.21 GB | 0.4 min | 1.2 GB |
| 1 year hourly `t2m`, Europe, land only | 365 | 1.2 GB | 0.44 GB | 1.9 min | 1.9 GB |
| 1 year hourly `t2m`, global | 365 | 28.7 GB | 19.4 GB | 24.6 min | 1.9 GB |
| 30 years daily `t2m`, global | 360 | 35.9 GB | 24.0 GB | 30.8 min | 1.9 GB |

In version 1.1, the last three requests were killed for lack of memory (3.8 GB), because a
chunk of 300 files was open at once. Version 1.2 uses chunks of 100 files. The run time
estimate in 1.2 comes from these measurements (about 24 s per GB, about 51 s per GB when
chunked, plus about 0.2 s per file).

## Findings and what changed in 1.2

1. **Memory for long requests** (real problem): chunks of 300 files needed 3.8 GB and were
   killed in a small SLURM job. Now chunks of 100 files (1.9 GB), joined with `cdo cat`.
2. **Daily extremes from hourly data:** min/max and the interpolation to a regular grid do not
   commute. The minimum of era5pool's hourly `mn2t` therefore differs from the daily `mn2t` by
   up to 3.3 K (1.5 K for `mx2t`). This is not an error in the data, but it is a trap for users.
   era5pool now warns, and the README explains it: take daily extremes from `--freq 1D`.
3. **Large requests:** the job script asked for 32 GB, while the measured peak is 1.9 GB. It
   now asks for 4 CPUs × 940 MB (the shared-partition maximum per CPU) and twice the estimated
   run time. On a login node, era5pool stops above ~5 GB or ~10 min. Inside a SLURM job there is
   no limit, and `--force` was removed. era5pool never submits jobs itself.
4. **Compression:** the help said the file is 30–60 % of the estimate. Measured: about two
   thirds (less with `--land-only`). Corrected.
5. **Usability:**
   - Options that only work together: `--land-threshold` alone asks whether `--land-only`
     was meant, and `--remap` without `--grid` gives a clear error.
   - A missing output folder is created only after asking.
   - New `--yes` answers all questions in scripts and SLURM jobs, so a job never waits for input.
   - The dates are swapped if `--end` is before `--start`.
   - Hints for ERA5T and for years before 1940.
   - An unknown grid is reported before anything runs.
   - cdo errors without a Python traceback.
   - A dry-run banner.
   - Clearer help for `--level`, `--grid`, `--remap` and `--land-threshold`, codes next to the
     variable names, and the land-sea mask named.
   - New regions `northern_hemisphere` and `southern_hemisphere`.
