#!/usr/bin/env python3
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
  - Longitudes follow your --box: -10,30 gives -10..30; -180,-60 gives 180..300 (same points).
  - The most recent months exist only as provisional ERA5T: use --family ET.

NOT SUPPORTED: model-level (ml) data (stored as spectral coefficients). Use cdo sp2gp directly.

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

__version__ = "1.1 (2026-11-17)"

POOL = "/pool/data/ERA5"
PARAM_TABLE = POOL + "/READMEs/README_ERA5_PARAMIDs.txt"
LSM_FILE = POOL + "/E5/sf/an/IV/172/E5sf00_IV_INVARIANT_172.grb"   # ERA5 land-sea mask (0..1)
MAX_FILES_PER_CDO_CALL = int(os.environ.get("ERA5POOL_CHUNK", 300))  # Levante: max 1024 open files
WARN_OUTPUT_GB = 5.0                  # above this, suggest a SLURM job
CITATION = ("Hersbach, H. et al. (2017): Complete ERA5 from 1940: Fifth generation of ECMWF "
            "atmospheric reanalyses of the global climate. Copernicus Climate Change Service (C3S) "
            "Climate Data Store (CDS). doi:10.24381/cds.143582cf. Data distribution by the German "
            "Climate Computing Center (DKRZ), /pool/data/ERA5.")
# ECMWF short names that start with a digit are not good NetCDF/Python names; use the
# names that xarray/cfgrib users know.
NC_NAME = {"2t": "t2m", "2d": "d2m", "10u": "u10", "10v": "v10", "100u": "u100",
           "100v": "v100", "10si": "si10", "10fg": "fg10", "10ws": "ws10"}
FREQS = ("1H", "1D", "1M")
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
    sys.exit("era5pool: ERROR: " + msg)


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
             "  %-16s %-20s %s" % ("name", "lon1,lon2,lat1,lat2", "description")]
    for name in sorted(REGIONS):
        box, desc = REGIONS[name]
        lines.append("  %-16s %-20s %s" % (name, box, desc))
    return "\n".join(lines)


def cmd_regions(args):
    print("%-15s %-16s %s" % ("region", "lon1,lon2,lat1,lat2", "description"))
    for name in sorted(REGIONS):
        box, desc = REGIONS[name]
        print("%-15s %-19s %s" % (name, box, desc))
    print("\nUse: era5pool.py get t2m ... --region europe   (or your own box with --box)")


def cmd_get(args):
    params = read_param_table()
    var = resolve_variable(args.variable, params)
    code, ncname = var["code"], var["ncname"]
    locs = [l for l in locations(code, args.family) if l[2] == args.freq]
    if not locs:
        avail = ", ".join(sorted({f for _, _, f in locations(code, args.family)})) or "none"
        die("%s (%s) has no %s data in %s. Available: %s" % (ncname, code, args.freq, args.family, avail))
    lev, typ, freq = locs[0]
    if lev == "pl" and args.level is None:
        die("%s is a pressure-level variable: add --level (hPa), e.g. --level 500" % ncname)
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
        die("--end is before --start")
    files, missing = file_list(args.family, lev, typ, freq, code, start, end)
    if not files:
        die("no files found for this period in %s/%s/%s/%s/%s/%s" % (POOL, args.family, lev, typ, freq, code))

    # ---- describe the request ----------------------------------------------------------
    out = args.out or "era5_%s%s%s_%s_%s_%s.nc" % (
        ncname, ("%shPa" % "-".join("%g" % l for l in args.level)) if args.level else "",
        region_tag, freq, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    kind = {"an": "analysis", "fc": "forecast"}[typ]
    print("Variable : %s = %s [%s] (code %s, %s %s)" % (ncname, var["name"], var["units"], code, lev, kind))
    print("Period   : %s .. %s, time step %s" % (start, end, freq))
    print("Files    : %d to read, e.g. %s" % (len(files), os.path.basename(files[0])))
    if missing:
        print("WARNING  : %d expected files are missing (first: %s)." % (len(missing), os.path.basename(missing[0])))
        if args.family == "E5":
            print("           Recent months may only exist as provisional ERA5T: try --family ET")
    print("Region   : %s" % (("%s (%s)" % (region_tag[1:], args.box)) if region_tag else (args.box or "global")))
    print("Grid     : %s" % (args.grid or "regular 0.28125 deg (native N320, unpacked)"))
    if args.land_only:
        if not os.path.exists(LSM_FILE):
            die("land-sea mask not found: %s" % LSM_FILE)
        print("Mask     : land only (ERA5 land-sea mask >= %g; sea set to missing)" % args.land_threshold)
    print("Output   : %s" % os.path.abspath(out))

    # rough size estimate (float32, compressed output is usually 30-60% of this)
    if args.box:
        lo1, lo2, la1, la2 = [float(x) for x in args.box.split(",")]
        npts = (abs(lo2 - lo1) / 0.28125 + 1) * (abs(la2 - la1) / 0.28125 + 1)
    else:
        npts = 1280 * 640
    if args.grid and re.fullmatch(r"r(\d+)x(\d+)", args.grid):
        nx, ny = map(int, re.fullmatch(r"r(\d+)x(\d+)", args.grid).groups())
        npts = npts * (nx * ny) / (1280 * 640)
    nsteps = {"1H": 24 * ((end - start).days + 1), "1D": (end - start).days + 1,
              "1M": 12 * (end.year - start.year + 1)}[freq]
    nlev = len(args.level) if args.level else 1
    gb = npts * nsteps * nlev * 4 / 1e9
    print("Size     : ~%s uncompressed" % ("%.1f GB" % gb if gb >= 1 else "%.0f MB" % max(gb * 1000, 1)))
    if gb > WARN_OUTPUT_GB and not args.force:
        print("\nThis is a large request. Run it as a SLURM job, e.g. save this as job.sh and 'sbatch job.sh':\n")
        print("#!/bin/bash\n#SBATCH --account=<your_project>\n#SBATCH --partition=shared\n"
              "#SBATCH --cpus-per-task=4\n#SBATCH --mem=32G\n#SBATCH --time=08:00:00\n"
              "module load cdo\npython3 %s %s --force" % (os.path.abspath(__file__), " ".join(sys.argv[1:])))
        print("\n(or add --force to run it here anyway)")
        return

    # ---- build the cdo operator chain (applied right to left) --------------------------
    cdo = find_cdo()
    attrs = '%s@long_name=%s,%s@units=%s,source=ERA5 from DKRZ pool %s/%s/%s/%s/%s/%s,citation=%s' % (
        ncname, var["name"].replace(",", " "), ncname, var["units"].replace(",", " "),
        POOL, args.family, lev, typ, freq, code, CITATION.replace(",", ";"))  # cdo uses ',' as separator
    if args.land_only:
        attrs += ",land_only=ERA5 land-sea mask >= %g (sea set to missing)" % args.land_threshold
    # spatial steps, applied identically to the data and (if --land-only) to the land-sea mask
    spatial = []
    if args.box:
        spatial.append("-sellonlatbox," + args.box)
    if args.grid:
        spatial.append("-remap%s,%s" % (args.remap, args.grid))
    spatial.append("-setgridtype,regular")
    datasel = []
    if args.level:
        datasel.append("-sellevel," + ",".join(str(int(round(l * 100))) for l in args.level))  # hPa -> Pa
    datasel.append("-seldate,%sT00:00:00,%sT23:59:59" % (start.isoformat(), end.isoformat()))

    def chain_for(file_group):
        """Full cdo operator chain for a group of input files (right-most operators act first)."""
        data = spatial + datasel + ["-mergetime", "["] + file_group + ["]"]
        if args.land_only:
            mask = ["-gec,%g" % args.land_threshold] + spatial + [LSM_FILE]
            # ifthen has exactly 2 inputs, so cdo assigns both chains without brackets
            data = ["-ifthen"] + mask + data
        return ["-setattribute," + attrs, "-setname," + ncname] + data
    # -r: relative time axis ("hours since ..."), which every NetCDF reader understands
    base = [cdo, "-s", "-r", "-P", str(args.threads), "-f", "nc4", "-z", "zip_1", "-t", "ecmwf"]

    print("\ncdo command%s:" % (" (not run: --dry-run)" if args.dry_run else ""))
    if len(files) <= MAX_FILES_PER_CDO_CALL:
        run(base + chain_for(files) + [out], args.dry_run)
    else:
        # process in chunks, then join: keeps the number of open files low
        tmpdir = tempfile.mkdtemp(prefix=".era5pool_tmp_",
                                  dir=args.tmpdir or os.path.dirname(os.path.abspath(out)))
        parts = []
        try:
            for i in range(0, len(files), MAX_FILES_PER_CDO_CALL):
                part = os.path.join(tmpdir, "part_%04d.nc" % len(parts))
                run(base + chain_for(files[i:i + MAX_FILES_PER_CDO_CALL]) + [part], args.dry_run)
                parts.append(part)
            run([cdo, "-s", "-f", "nc4", "-z", "zip_1", "mergetime"] + parts + [out], args.dry_run)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    if not args.dry_run:
        print("\nDone: %s (%.1f MB)" % (os.path.abspath(out), os.path.getsize(out) / 1e6))


def main():
    ap = argparse.ArgumentParser(description="version " + __version__ + "\n" + __doc__, epilog=regions_text(),
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
                               "  era5pool.py get t2m --freq 1D --start 2012-02 --end 2012-02 --region europe --land-only\n\n"
                               + regions_text())
    pg.add_argument("variable", help="short name (t2m, tp, msl, z, mn2t, ...) or code (167)")
    pg.add_argument("--freq", required=True, choices=FREQS, help="1H hourly, 1D daily, 1M monthly")
    pg.add_argument("--start", required=True, help="YYYY, YYYY-MM or YYYY-MM-DD")
    pg.add_argument("--end", required=True, help="YYYY, YYYY-MM or YYYY-MM-DD (inclusive)")
    pg.add_argument("--box", help="lon1,lon2,lat1,lat2 e.g. -10,30,35,72 (negative lon = west)")
    pg.add_argument("--region", help="named box, e.g. europe, arctic, nao_region (full list: NAMED REGIONS below)")
    pg.add_argument("--level", type=float, nargs="+", help="pressure level(s) in hPa, e.g. 500 850")
    pg.add_argument("--grid", help="regrid target, e.g. r360x180 (1 deg) or r1440x720 (0.25 deg)")
    pg.add_argument("--remap", default="bil", choices=["bil", "con", "nn"],
                    help="remap method for --grid: bil(inear), con(servative, use for precip), nn")
    pg.add_argument("--land-only", action="store_true",
                    help="mask out the sea (ERA5 land-sea mask; sea cells become missing values)")
    pg.add_argument("--land-threshold", type=float, default=0.5,
                    help="land fraction a cell needs to count as land (default 0.5; e.g. 0.8 drops coasts)")
    pg.add_argument("--family", default="E5", choices=["E5", "ET"],
                    help="E5 final ERA5 (default) or ET provisional ERA5T (last months)")
    pg.add_argument("--out", help="output file (default: era5_<var>[_<level>hPa][_<region>]_<freq>_<start>_<end>.nc)")
    pg.add_argument("--threads", type=int, default=4, help="cdo threads (default 4)")
    pg.add_argument("--tmpdir", help="where to put temporary chunk files for long requests "
                                     "(default: next to the output file; removed afterwards)")
    pg.add_argument("--dry-run", action="store_true", help="show files and cdo command, do not run")
    pg.add_argument("--force", action="store_true", help="run even if the request is large")

    # Main help (-h): also list every 'get' option, generated from the parser so it stays complete
    opt_lines = ["OPTIONS FOR 'get'   (details and examples: era5pool.py get -h)",
                 "  %-20s %s" % ("variable", "short name (t2m, tp, msl, z, mn2t, ...) or code (167)  [required]")]
    for act in pg._actions:
        if not act.option_strings or act.dest == "help":
            continue
        name = act.option_strings[-1]
        if act.nargs != 0:
            name += " " + ("{%s}" % ",".join(act.choices) if act.choices else act.dest.upper())
        helptext = (act.help or "") + ("  [required]" if act.required else "")
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
