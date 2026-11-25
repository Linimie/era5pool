#!/usr/bin/env python3
# Copyright (c) 2026 Linda van Garderen, Van Garderen Scientific
# SPDX-License-Identifier: MIT  (full text: LICENSE, or https://opensource.org/license/mit)
"""
era5pool: extract ERA5 data from the DKRZ pool (/pool/data/ERA5) as ready-to-use NetCDF.

The pool stores ERA5 as GRIB files on a reduced Gaussian grid with numeric parameter codes,
one file per day (1H), month (1D) or year (1M). This tool finds the right files, cuts out the
period / region / level you ask for, converts to a regular lat-lon grid and writes one
compressed NetCDF file with readable variable names and units. It only needs Python 3 and cdo.

QUICK START (on Levante)
  python3 era5pool.py list temperature                      # find variables
  python3 era5pool.py get t2m --freq 1D --start 2021-06-01 --end 2021-06-30 \\
          --box -180,-60,30,70                              # daily mean T2m, own box
  python3 era5pool.py get mn2t --freq 1D --start 2012-02 --end 2012-02 --region europe --land-only
  python3 era5pool.py regions                               # named regions
  python3 era5pool.py get z --level 500 --freq 1D --start 2021-06-20 --end 2021-06-30
  python3 era5pool.py get tp --freq 1M --start 1991 --end 2020 --grid r360x180
  ... add --dry-run to any 'get' to see the files and the cdo command without running it.

Run 'python3 era5pool.py get -h' for all options.

QUESTIONS AND --yes: era5pool asks before it creates a missing output folder, and when
--land-threshold is given without --land-only. In scripts and SLURM jobs nobody can answer:
add --yes there, which answers yes to every question. Without --yes a script stops straight
away with a clear message instead of waiting.

HOW IT WORKS: era5pool builds one cdo command (cdo = Climate Data Operators) and runs it. Options
that hand values to cdo (--grid, --remap) accept what cdo accepts; see the CDO manual,
https://code.mpimet.mpg.de/projects/cdo/wiki/Cdo#Documentation

LONG AND LARGE REQUESTS
  - Long periods are processed in chunks of 100 input files (Levante allows ~1000 open files),
    which are joined at the end. This happens automatically. Temporary files go next to the
    output (or to --tmpdir) and are removed afterwards. Chunk size: environment variable ERA5POOL_CHUNK.
  - On a login node, requests above ~LOGIN_GB GB or ~LOGIN_MIN minutes are not run: era5pool prints a
    ready-made SLURM job script instead (DKRZ: login nodes are for light work only). Inside a
    SLURM job (sbatch or salloc) there is no limit.

WHAT THE TIME STEPS MEAN (from DKRZ's pool READMEs)
  1H  hourly values
  1D  daily statistics from DKRZ: daily MEAN for instantaneous fields (t2m, msl, z, ...),
      daily SUM for accumulations (tp, radiation fluxes, ...), daily MIN/MAX for mn2t/mx2t.
      For sums/min/max the day is 01 UTC .. 00 UTC of the next day (the hour ending at the stamp).
  1M  monthly MEAN of the 1D values. For precipitation that is mean daily total, so multiply by
      the number of days in the month to get the monthly total.

GOOD TO KNOW
  - z is geopotential (m2 s-2), not height: divide by 9.80665 for geopotential metres.
  - Daily values carry a mid-day time stamp (e.g. 11:30); monthly values the 1st of the month.
  - Longitudes: a box across 0 keeps negative values (-10,30 gives -10..30); a box entirely west
    of 0 comes out as 0..360 (-125,-115 gives 235..245; -180,-60 gives 180..300). Same points.
  - The most recent months exist only as provisional ERA5T: use --family ET.
  - Daily minima/maxima (mn2t, mx2t, fg10): take them from --freq 1D. Do not compute them from
    --freq 1H output: after the conversion to a regular grid, min/max of the hourly fields differ
    from DKRZ's daily values by up to a few K (interpolation and min/max do not commute).

NOT SUPPORTED: model-level (ml) data (stored as spectral coefficients). Use cdo sp2gp directly.

AUTHOR: Linda van Garderen. Developed and tested with Claude Code (Anthropic) as the main tool.
LICENSE: MIT, Copyright (c) 2026 Linda van Garderen, Van Garderen Scientific. Free to use,
change and share, as long as this copyright notice is kept. Source: https://github.com/Linimie/era5pool

CITATION: please cite ERA5 (Hersbach et al., 2017/2020, doi:10.24381/cds.143582cf) and
acknowledge Copernicus C3S and DKRZ data distribution. The citation is written into every file.
"""
import argparse
import datetime as dt
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

__version__ = "1.2 (2026-11-25)"

POOL = "/pool/data/ERA5"
PARAM_TABLE = POOL + "/READMEs/README_ERA5_PARAMIDs.txt"
LSM_FILE = POOL + "/E5/sf/an/IV/172/E5sf00_IV_INVARIANT_172.grb"   # ERA5 land-sea mask (0..1)
# cdo mergetime keeps all files of a chunk open (~12 MB each): 100 files need ~1.5 GB, which fits a
# small SLURM job (300 files were killed at the 3.8 GB of 4 CPUs x 940 MB). Levante: max 1024 open files.
MAX_FILES_PER_CDO_CALL = int(os.environ.get("ERA5POOL_CHUNK", 100))
# Login nodes are for light work only (DKRZ). Above these estimates era5pool prints a SLURM job
# script instead of running. Inside a SLURM job (SLURM_JOB_ID set) there is no limit.
LOGIN_MAX_GB = 5.0                    # estimated output size before compression
LOGIN_MAX_MIN = 10.0                  # estimated run time in minutes
# Run time model, measured on Levante 2026-11-25 (shared partition, 4 CPUs; see TEST_REPORT.md):
# seconds per GB of output (before compression) plus seconds per input file. Chunked requests
# take about twice as long per GB, because joining the parts rewrites the compressed data.
SEC_PER_GB = 24.0
SEC_PER_GB_CHUNKED = 51.0
SEC_PER_FILE = 0.2
# SLURM resources for the job template (shared partition: max 940 MB per CPU). Measured peak
# memory with chunks of 100 files: 1.85 GB, so 4 x 940 MB leaves a safe margin.
SLURM_CPUS = 4
SLURM_MEM_PER_CPU = "940M"
# Parameters that are a minimum/maximum over the hour (DKRZ pool README, sf12 min/max)
MINMAX = {"049": "maximum", "201": "maximum", "202": "minimum"}
CITATION = ("Hersbach, H. et al. (2017): Complete ERA5 from 1940: Fifth generation of ECMWF "
            "atmospheric reanalyses of the global climate. Copernicus Climate Change Service (C3S) "
            "Climate Data Store (CDS). doi:10.24381/cds.143582cf. Data distribution by the German "
            "Climate Computing Center (DKRZ), /pool/data/ERA5.")
# ECMWF short names that start with a digit are not good NetCDF/Python names; use the
# names that xarray/cfgrib users know.
NC_NAME = {"2t": "t2m", "2d": "d2m", "10u": "u10", "10v": "v10", "100u": "u100",
           "100v": "v100", "10si": "si10", "10fg": "fg10", "10ws": "ws10"}
FREQS = ("1H", "1D", "1M")
VARIABLE_HELP = ("short name or code: t2m (167), tp (228), msl (151), z (129), mn2t (202), mx2t (201), "
                 "u10 (165), ...  ('era5pool.py list' shows all)")
# Named regions for --region: lon1, lon2, lat1, lat2 (west and south negative).
# To add one, add a line here; 'era5pool.py regions' lists them.
REGIONS = {
    "europe":        ("-25,45,34,72",  "Europe, close to the E-OBS / Copernicus European domain"),
    "africa":        ("-20,55,-36,38", "Africa including Madagascar"),
    "mediterranean": ("-10,40,30,46",  "Mediterranean basin"),
    "north_america": ("-170,-50,5,75", "North and Central America, Alaska to Newfoundland (Greenland mostly outside)"),
    "south_america": ("-95,-30,-57,15", "South America incl. Galapagos and Tierra del Fuego"),
    "asia":          ("25,180,-11,80", "Asia, Turkey to the Bering Strait (stops at the date line)"),
    "oceania":       ("110,210,-50,5", "Australia, New Zealand, Papua New Guinea, Pacific islands to 150W (crosses date line)"),
    "arctic":        ("-180,180,60,90", "Arctic polar cap north of 60N"),
    "antarctic":     ("-180,180,-90,-60", "Antarctica and Southern Ocean south of 60S"),
    "greenland":     ("-75,-10,59,84", "Greenland (ice sheet, Greenland blocking)"),
    # large-scale bands and ocean basins
    "northern_hemisphere": ("-180,180,0,90", "Northern Hemisphere 0-90N"),
    "southern_hemisphere": ("-180,180,-90,0", "Southern Hemisphere 0-90S"),
    "tropics":         ("-180,180,-30,30", "Tropics 30S-30N"),
    "nh_extratropics": ("-180,180,30,90",  "Northern Hemisphere extratropics 30-90N"),
    "sh_extratropics": ("-180,180,-90,-30", "Southern Hemisphere extratropics 30-90S"),
    "north_atlantic":  ("-80,20,20,80",    "North Atlantic (NAO, blocking, European weather regimes)"),
    "north_pacific":   ("120,240,20,70",   "North Pacific (Pacific jet, atmospheric rivers, PNA; crosses date line)"),
    "nino34":          ("-170,-120,-5,5",  "Nino 3.4 ENSO index region (use with SST)"),
    "nao_region":      ("-90,40,20,80",    "Domain for computing the NAO (Hurrell PC-based), 20-80N 90W-40E; use with msl"),
}


# ----------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------
def die(msg):
    sys.exit(color("era5pool: ERROR: " + msg))


def color(msg, code="31"):
    """Red (31) or yellow (33) text in a terminal; plain text in log files."""
    return "\033[%sm%s\033[0m" % (code, msg) if sys.stdout.isatty() and sys.stderr.isatty() else msg


def confirm(question, args, how_to_avoid):
    """Ask a yes/no question. --yes answers yes; without a terminal (scripts, SLURM) stop instead."""
    if args.yes:
        print("NOTE     : %s -> yes (--yes)" % question)
        return
    if args.dry_run:
        print("NOTE     : %s -> assumed yes for this dry run (a real run asks, or add --yes)" % question)
        return
    if not sys.stdin.isatty():
        die("%s Nobody can answer this here (no terminal): %s, or add --yes" % (question, how_to_avoid))
    answer = input(question + " [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        die("stopped (%s)" % how_to_avoid)


def on_login_node():
    return "SLURM_JOB_ID" not in os.environ


def grid_points(cdo, grid):
    """Number of points of a cdo target grid (r360x180, global_1, n80, t63grid, a grid file, ...)."""
    r = subprocess.run([cdo, "-s", "griddes", "-const,1," + grid], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True)
    m = re.search(r"gridsize\s*=\s*(\d+)", r.stdout)
    if r.returncode or not m:
        die("cdo does not know the grid '%s'. Use e.g. r360x180 or global_1 (regular lon-lat), "
            "n80 (Gaussian), t63grid (Gaussian for spectral T63) or a cdo grid description file" % grid)
    return int(m.group(1))


def minutes_text(minutes):
    if minutes < 1:
        return "< 1 min"
    return "%.0f min" % minutes if minutes < 90 else "%.1f h" % (minutes / 60)


def find_cdo():
    cdo = shutil.which("cdo")
    if cdo:
        return cdo
    cands = sorted(glob.glob("/sw/spack-levante/cdo-*/bin/cdo"))
    if cands:
        return cands[-1]
    die("cdo not found. Run 'module load cdo' first.")


def read_param_table():
    """DKRZ table: PARAM;Name;Short Name;Units;Grib table;Grib code"""
    if not os.path.exists(PARAM_TABLE):
        die("cannot read %s (are you on Levante?)" % PARAM_TABLE)
    params = {}
    with open(PARAM_TABLE, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = [p.strip() for p in line.strip().split(";")]
            if len(parts) >= 4 and re.fullmatch(r"\d{3}", parts[0]):
                code, name, short, units = parts[:4]
                params[code] = {"code": code, "name": name, "short": short, "units": units,
                                "ncname": NC_NAME.get(short, short)}
    return params


def locations(code, family="E5"):
    """Where does this code exist in the pool? -> list of (levtype, type, freq)."""
    found = []
    for lev in ("sf", "pl"):
        for typ in ("an", "fc"):
            for fr in FREQS + ("IV",):
                if os.path.isdir("%s/%s/%s/%s/%s/%s" % (POOL, family, lev, typ, fr, code)):
                    found.append((lev, typ, fr))
    return found


def resolve_variable(query, params):
    q = query.strip().lower()
    if re.fullmatch(r"\d{1,3}", q):
        code = "%03d" % int(q)
        if code in params:
            return params[code]
        die("code %s is not in the DKRZ parameter table" % code)
    hits = [p for p in params.values() if q in (p["short"].lower(), p["ncname"].lower())]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        die("'%s' is ambiguous: %s" % (query, ", ".join("%s (%s)" % (h["short"], h["code"]) for h in hits)))
    sugg = [p for p in params.values() if q in p["name"].lower()]
    msg = "unknown variable '%s'." % query
    if sugg:
        msg += " Did you mean: " + ", ".join("%s = %s" % (s["ncname"], s["name"]) for s in sugg[:8])
    die(msg + "  (try: era5pool.py list)")


def parse_date(s, end=False):
    """Accept YYYY, YYYY-MM or YYYY-MM-DD. For --end, a partial date means the end of that period."""
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", s.strip())
    if not m:
        die("bad date '%s' (use YYYY, YYYY-MM or YYYY-MM-DD)" % s)
    y, mo, d = int(m.group(1)), m.group(2), m.group(3)
    if not end:
        return dt.date(y, int(mo or 1), int(d or 1))
    if d:
        return dt.date(y, int(mo), int(d))
    if mo:
        nxt = dt.date(y + (int(mo) == 12), int(mo) % 12 + 1, 1)
        return nxt - dt.timedelta(days=1)
    return dt.date(y, 12, 31)


def file_list(family, lev, typ, freq, code, start, end):
    typeid = "00" if typ == "an" else "12"
    d = "%s/%s/%s/%s/%s/%s" % (POOL, family, lev, typ, freq, code)
    prefix = "%s%s%s_%s_" % (family, lev, typeid, freq)
    files, missing = [], []
    if freq == "1H":
        day = start
        while day <= end:
            files.append("%s/%s%s_%s.grb" % (d, prefix, day.isoformat(), code))
            day += dt.timedelta(days=1)
    elif freq == "1D":
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            files.append("%s/%s%04d-%02d_%s.grb" % (d, prefix, y, m, code))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    else:  # 1M
        for y in range(start.year, end.year + 1):
            files.append("%s/%s%04d_%s.grb" % (d, prefix, y, code))
    present = [f for f in files if os.path.exists(f)]
    missing = [f for f in files if not os.path.exists(f)]
    return present, missing


def run(cmd, dry):
    print("  $ " + " ".join(cmd) if len(" ".join(cmd)) < 600 else
          "  $ " + " ".join(cmd[:12]) + " ... (%d arguments)" % len(cmd))
    if not dry:
        subprocess.run(cmd, check=True)


# ----------------------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------------------
def cmd_list(args):
    params = read_param_table()
    q = (args.search or "").lower()
    print("%-6s %-9s %-55s %-14s %s" % ("code", "name", "description", "units", "available as"))
    for code in sorted(params):
        p = params[code]
        if q and q not in (p["name"] + " " + p["short"] + " " + p["ncname"] + " " + code).lower():
            continue
        locs = locations(code)
        if not locs:
            continue
        where = sorted({"%s/%s" % (l, t) for l, t, _ in locs})
        freqs = sorted({f for _, _, f in locs}, key=lambda f: (FREQS + ("IV",)).index(f))
        print("%-6s %-9s %-55s %-14s %s %s" % (code, p["ncname"], p["name"][:55], p["units"][:14],
                                              ",".join(where), ",".join(freqs)))


def regions_text():
    """Region table for the help screens (built from REGIONS, so it is always up to date)."""
    lines = ["NAMED REGIONS (use with --region, e.g. --region europe; or your own box with --box)",
             "  %-20s %-18s %s" % ("name", "lon1,lon2,lat1,lat2", "description")]
    for name in sorted(REGIONS):
        box, desc = REGIONS[name]
        lines.append("  %-20s %-18s %s" % (name, box, desc))
    return "\n".join(lines)


def cmd_regions(args):
    print("%-20s %-18s %s" % ("region", "lon1,lon2,lat1,lat2", "description"))
    for name in sorted(REGIONS):
        box, desc = REGIONS[name]
        print("%-20s %-18s %s" % (name, box, desc))
    print("\nUse: era5pool.py get t2m ... --region europe   (or your own box with --box)")


def cmd_get(args):
    if args.force:
        print("NOTE     : --force no longer exists (version 1.2). Large requests run as a SLURM job, "
              "where era5pool has no size limit; the option is ignored.")
    # options that only work together with another option
    if args.land_threshold is not None and not args.land_only:
        confirm("--land-threshold only works together with --land-only. Use land only, with threshold %g?"
                % args.land_threshold, args, "add --land-only, or drop --land-threshold")
        args.land_only = True
        args.auto_land_only = True
    if args.remap and not args.grid:
        die("--remap only works together with --grid, e.g. --grid r360x180 --remap con")
    threshold = 0.5 if args.land_threshold is None else args.land_threshold
    if not 0 < threshold <= 1:
        die("--land-threshold must be a land fraction between 0 and 1, e.g. 0.8")
    remap = args.remap or "bil"

    params = read_param_table()
    var = resolve_variable(args.variable, params)
    code, ncname = var["code"], var["ncname"]
    locs = [l for l in locations(code, args.family) if l[2] == args.freq]
    if not locs:
        avail = ", ".join(sorted({f for _, _, f in locations(code, args.family)})) or "none"
        die("%s (%s) has no %s data in %s. Available: %s" % (ncname, code, args.freq, args.family, avail))
    lev, typ, freq = locs[0]
    if lev == "pl" and args.level is None:
        die("%s is a pressure-level variable: add --level (hPa), e.g. --level 500 or --level 500 850" % ncname)
    if lev == "sf" and args.level is not None:
        die("%s is a single-level variable: drop --level" % ncname)

    region_tag = ""
    if args.region:
        key = args.region.lower().replace("-", "_")   # NAO-region == nao_region
        if key not in REGIONS:
            die("unknown region '%s'. Known: %s (or use --box)" % (args.region, ", ".join(sorted(REGIONS))))
        if args.box:
            print("NOTE     : --box given, so --region %s is ignored" % key)
        else:
            args.box = REGIONS[key][0]
            region_tag = "_" + key

    start, end = parse_date(args.start), parse_date(args.end, end=True)
    if end < start:
        print("NOTE     : --end (%s) is before --start (%s), so the two are swapped" % (args.end, args.start))
        args.start, args.end = args.end, args.start
        start, end = parse_date(args.start), parse_date(args.end, end=True)
    files, missing = file_list(args.family, lev, typ, freq, code, start, end)
    if not files:
        hint = ""
        if end.year < 1940:
            hint = " ERA5 starts in 1940."
        elif args.family == "E5" and file_list("ET", lev, typ, freq, code, start, end)[0]:
            hint = " This period only exists as provisional ERA5T so far: add --family ET"
        die("no files found for %s .. %s in %s/%s/%s/%s/%s/%s.%s"
            % (start, end, POOL, args.family, lev, typ, freq, code, hint))
    cdo = find_cdo()
    gridpts = grid_points(cdo, args.grid) if args.grid else 1280 * 640   # also checks the grid name

    # ---- describe the request ----------------------------------------------------------
    out = args.out or "era5_%s%s%s_%s_%s_%s.nc" % (
        ncname, ("%shPa" % "-".join("%g" % l for l in args.level)) if args.level else "",
        region_tag, freq, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    outdir = os.path.dirname(os.path.abspath(out))
    new_dir = not os.path.isdir(outdir)
    if args.tmpdir and not os.path.isdir(args.tmpdir):
        die("--tmpdir %s does not exist" % args.tmpdir)
    kind = {"an": "analysis", "fc": "forecast"}[typ]
    if args.dry_run:
        print(color("DRY RUN  : nothing is extracted and no files or folders are created", "33"))
    print("Variable : %s = %s [%s] (code %s, %s %s)" % (ncname, var["name"], var["units"], code, lev, kind))
    print("Period   : %s .. %s, time step %s" % (start, end, freq))
    print("Files    : %d to read, e.g. %s" % (len(files), os.path.basename(files[0])))
    if missing:
        print("WARNING  : %d expected files are missing (first: %s)." % (len(missing), os.path.basename(missing[0])))
        if args.family == "E5":
            print("           Recent months may only exist as provisional ERA5T: try --family ET")
    if freq == "1H" and code in MINMAX:
        print(color("WARNING  : these are hourly %ss. For daily %ss use --freq 1D: computing them from this\n"
                    "           output gives values that differ by up to a few K (the conversion to a regular\n"
                    "           grid and taking a %s do not commute)." % ((MINMAX[code],) * 3)))
    print("Region   : %s" % (("%s (%s)" % (region_tag[1:], args.box)) if region_tag else (args.box or "global")))
    print("Grid     : %s" % (("%s (cdo remap%s)" % (args.grid, remap)) if args.grid
                             else "regular Gaussian N320, 0.28125 deg (native ERA5 grid, unpacked)"))
    if args.land_only:
        if not os.path.exists(LSM_FILE):
            die("land-sea mask not found: %s" % LSM_FILE)
        print("Mask     : land only: ERA5 land-sea mask from the pool (no extra file needed),\n"
              "           cells with land fraction >= %g kept, sea set to missing\n"
              "           (%s)" % (threshold, LSM_FILE))
    print("Output   : %s" % os.path.abspath(out))
    if new_dir:
        print("Folder   : %s does not exist yet%s" % (outdir, "" if args.yes else
                                                         " (you will be asked to create it, or add --yes)"))

    # size and run time estimate (float32 before compression; the zip-compressed file is smaller)
    if args.box:
        lo1, lo2, la1, la2 = [float(x) for x in args.box.split(",")]
        frac = min((lo2 - lo1) % 360 or 360, 360) / 360.0 * abs(la2 - la1) / 180.0
    else:
        frac = 1.0
    npts = frac * gridpts
    nsteps = {"1H": 24 * ((end - start).days + 1), "1D": (end - start).days + 1,
              "1M": (end.year - start.year) * 12 + end.month - start.month + 1}[freq]
    nlev = len(args.level) if args.level else 1
    gb = npts * nsteps * nlev * 4 / 1e9
    per_gb = SEC_PER_GB_CHUNKED if len(files) > MAX_FILES_PER_CDO_CALL else SEC_PER_GB
    minutes = (per_gb * gb + SEC_PER_FILE * len(files)) / 60.0
    print("Size     : estimated %s before compression (the compressed file is usually about two thirds "
          "of this%s)" % ("%.1f GB" % gb if gb >= 1 else "%.0f MB" % max(gb * 1000, 1),
                          "; less with --land-only" if args.land_only else ""))
    print("Time     : estimated %s" % minutes_text(minutes))
    if on_login_node() and (gb > LOGIN_MAX_GB or minutes > LOGIN_MAX_MIN):
        print(color("\nThis request is too large for a login node (limits: %g GB, %g min). DKRZ asks to run\n"
                    "larger processing as a SLURM job. Save the lines below as job.sh, put your project\n"
                    "in --account, and submit it with 'sbatch job.sh':\n" % (LOGIN_MAX_GB, LOGIN_MAX_MIN), "33"))
        job_min = int(max(30, 2 * minutes) // 30 + 1) * 30            # twice the estimate, in half hours
        argv = [a for a in sys.argv[1:] if a not in ("--force", "--dry-run")]
        if getattr(args, "auto_land_only", False):
            argv.append("--land-only")
        if new_dir and "--yes" not in argv and "-y" not in argv:
            argv.append("--yes")        # nobody can answer questions inside a SLURM job
        print("#!/bin/bash\n#SBATCH --job-name=era5pool\n#SBATCH --account=<your_project>\n"
              "#SBATCH --partition=shared\n#SBATCH --cpus-per-task=%d\n#SBATCH --mem-per-cpu=%s\n"
              "#SBATCH --time=%02d:%02d:00\n#SBATCH --output=era5pool_%%j.log\n"
              "module load cdo\ncd %s\npython3 %s %s"
              % (SLURM_CPUS, SLURM_MEM_PER_CPU, job_min // 60, job_min % 60, os.getcwd(),
                 os.path.abspath(__file__), " ".join(argv)))
        return

    # ---- build the cdo operator chain (applied right to left) --------------------------
    attrs = '%s@long_name=%s,%s@units=%s,source=ERA5 from DKRZ pool %s/%s/%s/%s/%s/%s,citation=%s' % (
        ncname, var["name"].replace(",", " "), ncname, var["units"].replace(",", " "),
        POOL, args.family, lev, typ, freq, code, CITATION.replace(",", ";"))  # cdo uses ',' as separator
    if args.land_only:
        attrs += ",land_only=ERA5 land-sea mask >= %g (sea set to missing)" % threshold
    # spatial steps, applied identically to the data and (if --land-only) to the land-sea mask
    spatial = []
    if args.box:
        spatial.append("-sellonlatbox," + args.box)
    if args.grid:
        spatial.append("-remap%s,%s" % (remap, args.grid))
    spatial.append("-setgridtype,regular")
    datasel = []
    if args.level:
        datasel.append("-sellevel," + ",".join(str(int(round(l * 100))) for l in args.level))  # hPa -> Pa
    datasel.append("-seldate,%sT00:00:00,%sT23:59:59" % (start.isoformat(), end.isoformat()))

    def chain_for(file_group):
        """Full cdo operator chain for a group of input files (right-most operators act first)."""
        data = spatial + datasel + ["-mergetime", "["] + file_group + ["]"]
        if args.land_only:
            mask = ["-gec,%g" % threshold] + spatial + [LSM_FILE]
            # ifthen has exactly 2 inputs, so cdo assigns both chains without brackets
            data = ["-ifthen"] + mask + data
        return ["-setattribute," + attrs, "-setname," + ncname] + data
    # -r: relative time axis ("hours since ..."), which every NetCDF reader understands
    base = [cdo, "-s", "-r", "-P", str(args.threads), "-f", "nc4", "-z", "zip_1", "-t", "ecmwf"]

    if args.dry_run:
        pass
    elif new_dir:
        confirm("The output folder %s does not exist. Create it?" % outdir, args,
                "create it first with mkdir -p %s" % outdir)
        os.makedirs(outdir)
        print("Created  : %s" % outdir)
    print("\ncdo command%s:" % (" (not run: --dry-run)" if args.dry_run else ""))
    try:
        if len(files) <= MAX_FILES_PER_CDO_CALL:
            run(base + chain_for(files) + [out], args.dry_run)
        else:
            # process in chunks, then join: keeps the number of open files low
            tmpdir = (None if args.dry_run else
                      tempfile.mkdtemp(prefix=".era5pool_tmp_", dir=args.tmpdir or outdir))
            parts = []
            try:
                for i in range(0, len(files), MAX_FILES_PER_CDO_CALL):
                    part = os.path.join(tmpdir or "<tmpdir>", "part_%04d.nc" % len(parts))
                    run(base + chain_for(files[i:i + MAX_FILES_PER_CDO_CALL]) + [part], args.dry_run)
                    parts.append(part)
                # cat reads the parts one after another (they are in time order), so joining
                # many parts does not need much memory
                run([cdo, "-s", "-f", "nc4", "-z", "zip_1", "cat"] + parts + [out], args.dry_run)
            finally:
                if tmpdir:
                    shutil.rmtree(tmpdir, ignore_errors=True)
    except subprocess.CalledProcessError:
        if os.path.exists(out):
            os.remove(out)
        die("cdo stopped with an error (see its message above); no output file was written")
    if not args.dry_run:
        print("\nDone: %s (%.1f MB)" % (os.path.abspath(out), os.path.getsize(out) / 1e6))


def main():
    doc = __doc__.replace("LOGIN_GB", "%g" % LOGIN_MAX_GB).replace("LOGIN_MIN", "%g" % LOGIN_MAX_MIN)
    ap = argparse.ArgumentParser(description="version " + __version__ + "\n" + doc, epilog=regions_text(),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap._optionals.title = "general options (the options for 'get' follow below)"
    ap._positionals.title = "commands"
    ap.add_argument("--version", action="version", version="era5pool " + __version__)
    sub = ap.add_subparsers(dest="command")

    pl = sub.add_parser("list", help="show available variables (optionally filtered by a search word)")
    pl.add_argument("search", nargs="?", help="e.g. 'temperature', 'precip', '167'")

    sub.add_parser("regions", help="show the named regions for --region")

    pg = sub.add_parser("get", help="extract a variable to NetCDF",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog="examples:\n"
                               "  era5pool.py get t2m --freq 1D --start 2021-06 --end 2021-08 --box -10,30,35,72\n"
                               "  era5pool.py get mn2t --freq 1D --start 2012-02-01 --end 2012-02-15\n"
                               "  era5pool.py get z --level 500 850 --freq 1D --start 2021-06-20 --end 2021-06-30\n"
                               "  era5pool.py get t2m --freq 1M --start 1991 --end 2020 --grid r360x180 --dry-run\n"
                               "  era5pool.py get msl --freq 1M --start 1950 --end 2025 --region nao_region\n"
                               "  era5pool.py get t2m --freq 1D --start 2012-02 --end 2012-02 --region europe --land-only\n"
                               "  era5pool.py get tp --freq 1M --start 2021 --end 2021 --grid global_1 --remap con\n"
                               "  era5pool.py get t2m --freq 1D --start 1991 --end 2020 --out results/t2m.nc --yes   # in a script\n\n"
                               "long periods: processed automatically in chunks of 100 input files, joined at the end\n"
                               "  (temporary files next to the output or in --tmpdir, removed afterwards).\n"
                               "large requests: on a login node, era5pool stops above ~%g GB or ~%g min and prints a\n"
                               "  SLURM job script to submit with sbatch; inside a SLURM job there is no limit.\n"
                               "questions: era5pool asks before creating a missing output folder, and when\n"
                               "  --land-threshold is given without --land-only. In scripts and SLURM jobs add --yes:\n"
                               "  it answers yes to every question (without it, a script stops with a clear message).\n"
                               "cdo: era5pool runs cdo; --grid and --remap are passed to cdo, see the CDO manual\n"
                               "  https://code.mpimet.mpg.de/projects/cdo/wiki/Cdo#Documentation\n\n"
                               % (LOGIN_MAX_GB, LOGIN_MAX_MIN) + regions_text())
    pg.add_argument("variable", help=VARIABLE_HELP)
    pg.add_argument("--freq", required=True, choices=FREQS, help="1H hourly, 1D daily, 1M monthly")
    pg.add_argument("--start", required=True, help="YYYY, YYYY-MM or YYYY-MM-DD")
    pg.add_argument("--end", required=True, help="YYYY, YYYY-MM or YYYY-MM-DD (inclusive)")
    pg.add_argument("--box", help="lon1,lon2,lat1,lat2 e.g. -10,30,35,72 (negative lon = west)")
    pg.add_argument("--region", help="named box, e.g. europe, arctic, nao_region (full list: NAMED REGIONS below)")
    pg.add_argument("--level", type=float, nargs="+",
                    help="pressure level(s) in hPa, only for upper-air variables (z, t, u, v, q, r, ...). "
                         "One level: --level 500; several: separate them by spaces, --level 500 850 1000")
    pg.add_argument("--grid", help="regrid with cdo to: rNXxNY regular lon-lat (r360x180 = 1 deg, r1440x720 = 0.25 deg), "
                                   "global_D (global_1, global_0.5), nN Gaussian (n80), tNgrid Gaussian for "
                                   "spectral truncation T (t63grid, t106grid), or a cdo grid description file")
    pg.add_argument("--remap", choices=["bil", "con", "nn"],
                    help="only with --grid: cdo remap method, bil = bilinear (default), "
                         "con = conservative (use for precipitation and fluxes), nn = nearest neighbour")
    pg.add_argument("--land-only", action="store_true",
                    help="keep land only, using ERA5's own land-sea mask from the pool (no extra file needed); "
                         "sea cells become missing values (NaN)")
    pg.add_argument("--land-threshold", type=float,
                    help="belongs to --land-only: the land fraction (0-1) a grid cell needs to count as land. "
                         "Default 0.5; 0.8 also drops coastal cells. Given without --land-only, era5pool asks "
                         "whether you meant land only (--yes answers yes)")
    pg.add_argument("--family", default="E5", choices=["E5", "ET"],
                    help="E5 final ERA5 (default) or ET provisional ERA5T (last months)")
    pg.add_argument("--out", help="output file (default: era5_<var>[_<level>hPa][_<region>]_<freq>_<start>_<end>.nc)")
    pg.add_argument("--threads", type=int, default=4, help="cdo threads (default 4)")
    pg.add_argument("--tmpdir", help="where to put temporary chunk files for long requests "
                                     "(default: next to the output file; removed afterwards)")
    pg.add_argument("-y", "--yes", action="store_true",
                    help="answer yes to every question without asking (for scripts and SLURM jobs): "
                         "create a missing output folder; use --land-only when only --land-threshold is given")
    pg.add_argument("--dry-run", action="store_true",
                    help="show the files, the estimated size and run time, and the cdo command; create nothing")
    pg.add_argument("--force", action="store_true", help=argparse.SUPPRESS)   # removed in 1.2, see cmd_get

    # Main help (-h): also list every 'get' option, generated from the parser so it stays complete
    opt_lines = ["OPTIONS FOR 'get'   (details and examples: era5pool.py get -h)",
                 "  %-20s %s" % ("variable", textwrap.fill(VARIABLE_HELP + "  [required]", 99,
                                                           initial_indent=" " * 23, subsequent_indent=" " * 23, break_on_hyphens=False)[23:])]
    for act in pg._actions:
        if not act.option_strings or act.dest == "help" or act.help == argparse.SUPPRESS:
            continue
        name = act.option_strings[-1]
        if act.nargs != 0:
            name += " " + ("{%s}" % ",".join(act.choices) if act.choices else act.dest.upper())
        helptext = (act.help or "") + ("  [required]" if act.required else "")
        helptext = textwrap.fill(helptext, 99, initial_indent=" " * 23, subsequent_indent=" " * 23, break_on_hyphens=False)[23:]
        opt_lines.append("  %-20s %s" % (name, helptext) if len(name) <= 20
                         else "  %s\n  %-20s %s" % (name, "", helptext))
    ap.epilog = "\n".join(opt_lines) + "\n\n" + regions_text()

    # allow "--box -180,-60,30,70" (argparse would read the leading minus as a new option)
    argv = sys.argv[1:]
    for i, a in enumerate(argv[:-1]):
        if a == "--box" and argv[i + 1].startswith("-"):
            argv[i:i + 2] = ["--box=" + argv[i + 1]]
            break
    args = ap.parse_args(argv)
    if args.command == "list":
        cmd_list(args)
    elif args.command == "regions":
        cmd_regions(args)
    elif args.command == "get":
        cmd_get(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
