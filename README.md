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
  give both, `--box` wins.
- **Land only:** `--land-only` combines with a region, a box or global. `--land-threshold` only
  works together with `--land-only`.
- **Pressure levels:** `--level` is **required** for upper-air variables (`t`, `z`, `u`, `v`, `q`,
  `r`, …) and **not allowed** for surface variables (`t2m`, `tp`, `msl`, …). `era5pool list`
  shows which is which (`pl` or `sf`).
- **Regridding:** `--remap` only works together with `--grid`.
- **Checking first:** `--dry-run` can be added to anything. It shows what would happen and
  changes nothing.

**Examples that combine several options:**
```
era5pool get t2m --freq 1D --start 2021-06 --end 2021-08 --region europe --land-only
era5pool get z --level 500 --freq 1D --start 2021-06-20 --end 2021-06-30 --box -180,-60,30,70
era5pool get tp --freq 1M --start 1991 --end 2020 --region africa --grid r360x180 --remap con
```

## Options

| Option | Meaning |
|---|---|
| `variable` | **required.** Short name (`t2m`, `tp`, `msl`, `z`, `mn2t`, `mx2t`, `u10`, …) or code (`167`) |
| `--freq` | **required.** `1H` hourly, `1D` daily, `1M` monthly (see below) |
| `--start`, `--end` | **required.** `YYYY`, `YYYY-MM` or `YYYY-MM-DD`; the end is inclusive (`--end 2020` = to 31 Dec 2020) |
| `--region` | named box: `europe`, `africa`, `mediterranean`, `north_america`, `south_america`, `asia`, `oceania`, `arctic`, `antarctic`, `greenland`, `tropics`, `nh_extratropics`, `sh_extratropics`, `north_atlantic`, `north_pacific`, `nino34`, `nao_region` (see `era5pool.py regions`). Upper/lower case and `-` or `_` are all accepted (`NAO-region` works); the name is added to the output file |
| `--box` | own box `lon1,lon2,lat1,lat2`, western longitudes negative, e.g. `-10,30,35,72`; overrides `--region` |
| `--level` | pressure level(s) in hPa, only for upper-air variables (`t`, `z`, `u`, `v`, `q`, `r`, …) |
| `--grid` | regrid, e.g. `r360x180` (1°) or `r1440x720` (0.25°); `--remap con` for precipitation and fluxes |
| `--land-only` | mask out the sea with the ERA5 land-sea mask; sea cells become missing values (NaN) |
| `--land-threshold` | land fraction a cell needs to count as land (default 0.5; e.g. `0.8` also drops coastal cells) |
| `--family ET` | provisional ERA5T, for the last few months not yet in final ERA5 |
| `--dry-run` | show what would happen, change nothing |
| `--force` | run a large request on the login node anyway |

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

## Good to know

- **Geopotential:** `z` is geopotential (m² s⁻²); divide by 9.80665 for geopotential metres.
- **Large requests:** the script estimates the output size. Above ~5 GB it stops and prints a
  ready-made SLURM job script; put your project account in it and `sbatch` it.
- **Long periods** are processed in chunks, because Levante allows ~1000 open files. Temporary
  files go next to the output and are removed afterwards.
- **Model-level data** (`ml`) are not supported; they are stored as spectral coefficients.
- **Please cite ERA5** (Hersbach et al., doi:10.24381/cds.143582cf) and acknowledge Copernicus C3S
  and DKRZ. The citation is stored in every output file (`citation` attribute).

## Land-only extraction

`--land-only` uses ERA5's own land-sea mask (`lsm`, code 172, from
`/pool/data/ERA5/E5/sf/an/IV/172/`), a land fraction from 0 to 1 per grid cell. A cell counts as
land if its fraction is at least `--land-threshold`, which defaults to 0.5. The mask goes through
the same steps as the data (regular grid, regridding, box), so it always matches. Sea cells
become missing values, and the file records the setting in its `land_only` attribute.
Area means over the result are then land-only means; still use cos(latitude) weights.

## Version history

- **1.1 (2026-11-17):** `--land-only` and `--land-threshold`; `--version`; the main help
  (`era5pool -h`) now lists all options.
- **1.0 (2026-11-17):** first shared version: `list`, `regions`, `get` with
  `--box`, `--region`, `--level`, `--grid`, `--family ET`, `--dry-run`, and the large-request check.

To update a copy you made earlier, download `era5pool.py` and `README.md` again (or `git pull`). `era5pool --version`
shows which version you have.

## Tested (2026-11-17, Levante, cdo 2.6.6, Python 3.6)

`list` and search; unknown-name suggestions; daily T2m with a western-hemisphere box; Z500 and Z850;
monthly precipitation regridded to 1° (conservative); hourly T2m in chunks; daily `mn2t`
January–February 2012; ERA5T October 2026; the error for a pressure-level variable without
`--level`; the large-request warning with its SLURM template.
Version 1.1, `--land-only`: Europe daily (land values identical to unmasked; same mask every day);
threshold 0.8 with 1° regridding; pressure levels; hourly data in chunks.

Written by Linda van Garderen with Claude (Anthropic). Questions and improvements welcome.
