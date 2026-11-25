# era5pool: ERA5 from the DKRZ pool as ready-to-use NetCDF

The ERA5 data in `/pool/data/ERA5` on Levante are GRIB files on a reduced Gaussian grid, with
numeric parameter codes and one file per day, month or year. `era5pool.py` does the fiddly part
for you, in one command:
- finds the right files
- cuts out your period, region and pressure level
- converts to a regular lat-lon grid, and regrids if you ask
- writes **one compressed NetCDF** with a proper variable name, units, a time axis and the ERA5
  citation

It only needs Python 3 and `cdo`, both already on Levante. There's nothing to install.

## Quick start

```bash
module load cdo                      # optional: the script also finds cdo by itself
python3 era5pool.py list             # all variables, and where/at which time step they exist
python3 era5pool.py list precip      # search
python3 era5pool.py get -h           # all options with examples
```

**Setup: get your own copy.** On Levante, download the tool into a folder of your own:

```bash
mkdir -p <your_path> && cd <your_path>
wget https://raw.githubusercontent.com/Linimie/era5pool/main/era5pool.py
wget https://raw.githubusercontent.com/Linimie/era5pool/main/README.md
```

Or clone the whole repository: `git clone https://github.com/Linimie/era5pool.git`

Then add a shortcut to `~/.bash_profile` on Levante and log in again:

```bash
alias era5pool='python3 <your_path>/era5pool.py'
```

After that, `era5pool list` and `era5pool get ...` work from any folder.

## Finding a variable

`era5pool list` shows every variable in the pool, with its short name, code, units, and where
and at which time steps it exists. Add a word to search the names, short names and codes:

```bash
era5pool list temperature     # everything with 'temperature' in the name
era5pool list precip          # total precipitation, convective precipitation, ...
era5pool list 167             # by code
```

The `available as` column shows `sf` (surface/single level) or `pl` (pressure levels, needs
`--level`), `an` (analysis) or `fc` (forecast), and the time steps `1H`, `1D`, `1M` (and `IV` for
invariant fields such as the land-sea mask).

## Examples

```bash
# Daily mean 2 m temperature, summer 2021, North America / Pacific
python3 era5pool.py get t2m --freq 1D --start 2021-06 --end 2021-08 --box -180,-60,30,70

# Daily minimum temperature during the February 2012 European cold wave, land only
python3 era5pool.py get mn2t --freq 1D --start 2012-01-15 --end 2012-02-29 --region europe --land-only

# Geopotential at 500 and 850 hPa
python3 era5pool.py get z --level 500 850 --freq 1D --start 2021-06-20 --end 2021-06-30

# Monthly precipitation 1991-2020 on a 1 degree grid (conservative remapping)
python3 era5pool.py get tp --freq 1M --start 1991 --end 2020 --grid r360x180 --remap con

# In a script or SLURM job: --yes answers era5pool's questions (here: create the folder results/)
python3 era5pool.py get t2m --freq 1D --start 1991 --end 2020 --region europe --out results/t2m_europe.nc --yes

# Not sure? Add --dry-run: it shows the files and the exact cdo command without running anything
python3 era5pool.py get t2m --freq 1H --start 2021-06-28 --end 2021-06-29 --dry-run
```

The output name is chosen automatically, e.g. `era5_t2m_1D_20210601_20210831.nc`, or set it
with `--out`. Read the file with xarray, cdo, ncview, R and so on, like any other NetCDF.

## How to build a command

```
era5pool get <variable> --freq ... --start ... --end ...   [optional extras, in any order]
```

- **Four parts are always required:** the variable, `--freq`, `--start` and `--end`.
- **Everything else is optional** and can be added in any order.
- **Options start with two dashes:** `--freq`, not `-freq`.

**Rules for combining options:**
- **Where:** `--region` (named box) **or** `--box` (your own box), or neither for global. If you
  give both, `--box` wins (a NOTE says so).
- **Land only:** `--land-only` combines with a region, a box or global. `--land-threshold` only
  works together with `--land-only`. If you give it without `--land-only`, era5pool asks whether
  you meant land only (in scripts: add `--yes`, see below).
- **Pressure levels:** `--level` is **required** for upper-air variables (`t`, `z`, `u`, `v`, `q`,
  `r`, …) and **not allowed** for surface variables (`t2m`, `tp`, `msl`, …). `era5pool list`
  shows which is which (`pl` or `sf`).
- **Regridding:** `--remap` only works together with `--grid` (without it you get an error).
- **Period:** if `--end` is before `--start`, the two are swapped (a NOTE says so).
- **Checking first:** `--dry-run` can be added to anything. It shows the files, the estimated
  size and run time and the cdo command, and creates nothing.

**Examples that combine several options:**
```
era5pool get t2m --freq 1D --start 2021-06 --end 2021-08 --region europe --land-only
era5pool get z --level 500 --freq 1D --start 2021-06-20 --end 2021-06-30 --box -180,-60,30,70
era5pool get tp --freq 1M --start 1991 --end 2020 --region africa --grid r360x180 --remap con
```

## Options

| Option | Meaning |
|---|---|
| `variable` | **required.** Short name or code: `t2m` (167), `tp` (228), `msl` (151), `z` (129), `mn2t` (202), `mx2t` (201), `u10` (165), … (`era5pool list` shows all) |
| `--freq` | **required.** `1H` hourly, `1D` daily, `1M` monthly (see below) |
| `--start`, `--end` | **required.** `YYYY`, `YYYY-MM` or `YYYY-MM-DD`; the end is inclusive (`--end 2020` = to 31 Dec 2020) |
| `--region` | named box: `europe`, `africa`, `mediterranean`, `north_america`, `south_america`, `asia`, `oceania`, `arctic`, `antarctic`, `greenland`, `northern_hemisphere`, `southern_hemisphere`, `tropics`, `nh_extratropics`, `sh_extratropics`, `north_atlantic`, `north_pacific`, `nino34`, `nao_region` (see `era5pool.py regions`). Upper/lower case and `-` or `_` are all accepted (`NAO-region` works); the name is added to the output file |
| `--box` | own box `lon1,lon2,lat1,lat2`, western longitudes negative, e.g. `-10,30,35,72`; overrides `--region` |
| `--level` | pressure level(s) in hPa, only for upper-air variables (`t`, `z`, `u`, `v`, `q`, `r`, …). One level: `--level 500`; several: separated by spaces, `--level 500 850 1000` |
| `--grid` | regrid with cdo. Accepted: `rNXxNY` regular lon-lat (`r360x180` = 1°, `r1440x720` = 0.25°), `global_D` (`global_1`, `global_0.5`), `nN` Gaussian (`n80`), `tNgrid` Gaussian grid for spectral truncation T (`t63grid`, `t106grid`), or a cdo grid description file. See the [CDO manual](https://code.mpimet.mpg.de/projects/cdo/wiki/Cdo#Documentation) |
| `--remap` | only with `--grid`: cdo remap method, `bil` bilinear (default), `con` conservative (use for precipitation and fluxes), `nn` nearest neighbour |
| `--land-only` | keep land only, with ERA5's own land-sea mask from the pool (no extra file needed); sea cells become missing values (NaN) |
| `--land-threshold` | only with `--land-only`: land fraction (0–1) a cell needs to count as land (default 0.5; `0.8` also drops coastal cells) |
| `--family ET` | provisional ERA5T, for the last few months not yet in final ERA5 |
| `--out` | output file name, may include a folder (default: an automatic name in the current folder) |
| `--yes`, `-y` | answer yes to every question without asking, for scripts and SLURM jobs: create a missing output folder; use `--land-only` when only `--land-threshold` is given |
| `--tmpdir` | folder for the temporary chunk files of long requests (default: next to the output) |
| `--threads` | cdo threads (default 4) |
| `--dry-run` | show the files, estimated size and run time, and the cdo command; create nothing |

## Named regions

| Region | lon1, lon2, lat1, lat2 | Description |
|---|---|---|
| `europe` | −25, 45, 34, 72 | Europe, close to the E-OBS / Copernicus European domain |
| `africa` | −20, 55, −36, 38 | Africa including Madagascar |
| `mediterranean` | −10, 40, 30, 46 | Mediterranean basin |
| `north_america` | −170, −50, 5, 75 | North and Central America, Alaska to Newfoundland (Greenland mostly outside) |
| `south_america` | −95, −30, −57, 15 | South America including Galápagos and Tierra del Fuego |
| `asia` | 25, 180, −11, 80 | Turkey to the Bering Strait (stops at the date line) |
| `oceania` | 110, 210, −50, 5 | Australia, New Zealand, Papua New Guinea, Pacific islands to 150°W; crosses the date line, so longitudes run 110…210 (0–360°) |
| `arctic` | −180, 180, 60, 90 | Arctic polar cap north of 60°N |
| `antarctic` | −180, 180, −90, −60 | Antarctica and Southern Ocean south of 60°S |
| `greenland` | −75, −10, 59, 84 | Greenland (ice sheet, Greenland blocking) |
| `northern_hemisphere` | −180, 180, 0, 90 | Northern Hemisphere 0–90°N |
| `southern_hemisphere` | −180, 180, −90, 0 | Southern Hemisphere 0–90°S |
| `tropics` | −180, 180, −30, 30 | Tropics 30°S–30°N |
| `nh_extratropics` | −180, 180, 30, 90 | Northern Hemisphere extratropics 30–90°N |
| `sh_extratropics` | −180, 180, −90, −30 | Southern Hemisphere extratropics 30–90°S |
| `north_atlantic` | −80, 20, 20, 80 | North Atlantic (NAO, blocking, European weather regimes) |
| `north_pacific` | 120, 240, 20, 70 | North Pacific (Pacific jet, atmospheric rivers, PNA); crosses the date line, longitudes 120…240 |
| `nino34` | −170, −120, −5, 5 | Niño 3.4 ENSO index region (use with `sst`) |
| `nao_region` | −90, 40, 20, 80 | Domain for computing the NAO, as used by Hurrell's PC-based index, 20–80°N 90°W–40°E (use with `msl`). This is the *domain*: the index itself is the leading EOF/PC of seasonal msl anomalies there (or the Azores–Iceland pressure difference) |

**Not included on purpose:**
- **IPCC AR6 reference regions** (WCE, MED, NEU, …) are polygons, not boxes. Extract a box
  around them, then mask in Python with the `regionmask` package (`regionmask.defined_regions.ar6.land`).
- **Event-specific boxes** change per study: use `--box`.

Polar regions on a lat-lon grid: the grid cells get very narrow towards the pole, so always use
area weights (cos(latitude)) when averaging, or regrid with `--grid` first.

To add a region, add one line to the `REGIONS` list near the top of `era5pool.py`.

## What the time steps mean (DKRZ definitions)

- **1H:** hourly values.
- **1D:** daily statistics computed by DKRZ.
  - Instantaneous fields (`t2m`, `msl`, `z`, …): daily **mean**, 00–23 UTC.
  - Accumulations (`tp`, radiation, …): daily **sum**.
  - `mn2t`, `mx2t`: daily **minimum or maximum**.
  - For sums, minima and maxima the day runs from 01 UTC to 00 UTC of the next day.
- **1M:** monthly **mean of the daily values**. For precipitation that is the mean *daily* total:
  multiply by the number of days to get the monthly total.

Details are in `/pool/data/ERA5/READMEs/README_ERA5_1D_files.txt` and `..._1M_files.txt`.

**Daily minima and maxima: use `--freq 1D`.** On the original ERA5 grid, DKRZ's daily `mn2t`
is exactly the minimum of the hourly values from 01 to 00 UTC (we checked this). But era5pool
converts every field to a regular grid, by interpolating along the latitude rows, and
interpolation and min/max do not commute. So if you take the minimum of era5pool's *hourly*
output yourself, you get values that differ from the daily file by up to ~3 K. Means and sums
are not affected. era5pool warns when you request hourly `mn2t`, `mx2t` or `fg10`.

## Good to know

- **Geopotential:** `z` is geopotential (m² s⁻²); divide by 9.80665 for geopotential metres.
- **Longitudes:** a box across 0° keeps negative longitudes (`-10,30` gives −10…30). A box
  entirely west of 0° comes out as 0–360° (`-125,-115` gives 235…245).
- **Questions and `--yes`:** era5pool asks before it creates a missing output folder, and when
  `--land-threshold` is given without `--land-only`. In scripts and SLURM jobs nobody can answer,
  so add `--yes` there: it answers every question with yes. Without `--yes`, a script stops
  straight away with a clear message instead of waiting. The job script era5pool prints already
  contains `--yes` where it is needed.
- **Model-level data** (`ml`) are not supported; they are stored as spectral coefficients.
- **Please cite ERA5** (Hersbach et al., doi:10.24381/cds.143582cf) and acknowledge Copernicus C3S
  and DKRZ. The citation is stored in every output file (`citation` attribute).

## Large requests and run time

era5pool estimates the size and run time of every request (also with `--dry-run`).

- **On a login node** it runs requests up to ~5 GB (before compression) or ~10 minutes. DKRZ
  asks to keep login nodes for light work, so above that era5pool does not run. It prints a
  ready-made SLURM job script instead: save it as `job.sh`, put your project in `--account`, and
  submit it with `sbatch job.sh`. era5pool never submits jobs itself.
- **Inside a SLURM job** (`sbatch` or `salloc`) there is no limit.
- **Long periods** are processed in chunks of 100 input files, which are joined at the end.
  This keeps memory low and stays below Levante's limit of ~1000 open files. Temporary files go
  next to the output (or to `--tmpdir`) and are removed afterwards. The chunk size can be changed
  with the environment variable `ERA5POOL_CHUNK`.
- **The output is compressed** (NetCDF4, zip level 1). The file is usually about two thirds of
  the size estimate, and smaller with `--land-only`. The real size is printed at the end.

Measured on Levante (shared partition, 4 CPUs × 940 MB, 2026-11-25):

| Request | Input files | Before compression | File | Run time | Memory |
|---|---|---|---|---|---|
| 1 month hourly `t2m`, global | 31 | 2.4 GB | 1.6 GB | 1 min | 0.6 GB |
| 1 year daily `z`, 3 levels, global | 12 | 3.6 GB | 2.5 GB | 1.5 min | 0.3 GB |
| 76 years monthly `tp`, regridded to 1° | 76 | 0.24 GB | 0.21 GB | 0.5 min | 1.2 GB |
| 1 year hourly `t2m`, Europe, land only | 365 | 1.2 GB | 0.44 GB | 2 min | 1.9 GB |
| 1 year hourly `t2m`, global | 365 | 28.7 GB | 19.4 GB | 25 min | 1.9 GB |
| 30 years daily `t2m`, global | 360 | 35.9 GB | 24.0 GB | 31 min | 1.9 GB |

The job script asks for 4 CPUs with 940 MB each (the maximum per CPU on the shared
partition) and twice the estimated run time.

## Land-only extraction

`--land-only` uses ERA5's own land-sea mask (`lsm`, code 172, from
`/pool/data/ERA5/E5/sf/an/IV/172/`), a land fraction from 0 to 1 per grid cell. A cell counts as
land if its fraction is at least `--land-threshold`, which defaults to 0.5. The mask goes through
the same steps as the data (regular grid, regridding, box), so it always matches. Sea cells
become missing values, and the file records the setting in its `land_only` attribute.
Area means over the result are then land-only means; still use cos(latitude) weights.

## Version history

- **1.2 (2026-11-25):** after a full test (see below).
  - Large requests: a run time estimate, measured on Levante. On a login node, requests above
    ~5 GB or ~10 min print a SLURM job script instead of running. Inside a SLURM job there is no
    limit, and `--force` is gone (it is still accepted, with a note). The job script asks for
    4 CPUs × 940 MB instead of 32 GB.
  - Long periods: chunks of 100 files (was 300, which used 3.8 GB of memory and was killed in a
    small SLURM job), joined with `cdo cat`.
  - Questions: asks before creating a missing output folder, and asks whether you meant
    `--land-only` when only `--land-threshold` is given. New `--yes` answers them in scripts and jobs.
  - New regions `northern_hemisphere`, `southern_hemisphere`.
  - Clear error for `--remap` without `--grid`;
    an `--end` before `--start` is swapped; hints for ERA5T and for years before 1940; an unknown
    `--grid` is reported before anything runs; cdo errors without a Python traceback.
  - Warning for hourly `mn2t`, `mx2t`, `fg10` (use `--freq 1D` for daily extremes).
  - Clearer help: codes next to names, `--level`, `--grid` (all forms cdo accepts), `--remap`,
    `--land-threshold`, chunking, the use of cdo; a banner for `--dry-run`.
  - The extracted data are identical to version 1.1.
- **1.1 (2026-11-17):** `--land-only` and `--land-threshold`; `--version`; the main help
  (`era5pool -h`) now lists all options.
- **1.0 (2026-11-17):** first shared version: `list`, `regions`, `get` with
  `--box`, `--region`, `--level`, `--grid`, `--family ET`, `--dry-run`, and the large-request check.

To update a copy you made earlier, download `era5pool.py` and `README.md` again (or `git pull`). `era5pool --version`
shows which version you have.

## Tested (2026-11-25, Levante, cdo 2.2.2, Python 3.10)

Every command and option was run on Levante, and the output was checked against references that
do not go through era5pool or cdo:

- **Against the raw pool GRIB**, read with eccodes and converted to the regular grid
  independently: hourly, daily and monthly `t2m`; daily `tp`; `mn2t` and `mx2t`; `z` at 500 and
  850 hPa; the regions `europe`, `oceania` (across the date line) and `nao_region`; ERA5T. All
  agree to within 0.00002 K (0.002 m² s⁻² for `z`).
- **DKRZ's definitions:** daily mean = mean of 00–23 UTC; daily sum, minimum and maximum = over
  01 UTC to 00 UTC of the next day; monthly = mean of the daily values. All confirmed.
- **Options:** `--land-only` (land values unchanged, sea exactly where the ERA5 land-sea mask is
  below the threshold); `--grid r360x180 --remap con` (global mean unchanged); chunking (identical
  to an unchunked run); `--out`, `--threads`, `--tmpdir`, `--dry-run`; all error messages.
- **Against an independent source:** the same fields downloaded from the Copernicus Climate Data
  Store agree with a bias of 0.01 K for `t2m` and 0.01 m² s⁻² for `z500` (the rest is the
  difference between the CDS 0.25° grid and the pool's N320 grid).
- **Large requests** as SLURM jobs: see the table under "Large requests and run time".

The full test report is in [TEST_REPORT.md](TEST_REPORT.md).

## License and citation

era5pool is open source under the [MIT license](LICENSE): you are free to use, change and share
it, also in your own services, as long as the copyright notice
"Copyright (c) 2026 Linda van Garderen, Van Garderen Scientific" stays with it.

If era5pool helped your work, please cite it (GitHub: "Cite this repository", or
`CITATION.cff`): van Garderen, L. (2026): era5pool, version 1.2. Van Garderen Scientific.
https://github.com/Linimie/era5pool. And please cite ERA5 itself (see "Good to know").

**Author:** Linda van Garderen. Developed and tested with Claude Code (Anthropic) as the main tool.
Questions and improvements welcome.
