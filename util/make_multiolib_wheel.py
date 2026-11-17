#!/usr/bin/env python3
"""Build the self-contained ``multiolib`` wheel from a multio-bundle install.

The wheel provides the Python package ``multiolib`` that ``pymultio`` finds through
``findlibs.find("multio-api", "multiolib")``:

    multiolib/__init__.py
    multiolib/lib64/lib*.so      every shared library of the install (SONAME names), RUNPATH $ORIGIN
    multiolib/share/...          the install's share/ (eckit-based libraries find it as <lib>/../share)
    multiolib.libs/              non-system runtime dependencies grafted by auditwheel (libaec, ...)

Steps: stage the package (copy, strip, RUNPATH=$ORIGIN) -> write a linux_<arch> wheel with the
standard library -> ``auditwheel repair`` (grafts external libraries, fixes RPATHs, retags as
manylinux). MPI libraries and the system C++ runtime are never bundled.

Needs on PATH: auditwheel, patchelf, readelf, strip (build-wheels.sh sets them up).
"""

import argparse
import base64
import fnmatch
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

DIST = "multiolib"

# ECMWF projects whose license notices go to multiolib/licenses/<project>/:
# (project dir in the bundle sources, SONAME patterns, always included)
ECMWF_PROJECTS = [
    ("eckit", ["libeckit*", "libdummy_daos*"], True),
    ("eccodes", ["libeccodes*"], True),
    ("metkit", ["libmetkit*"], True),
    ("fdb5", ["libfdb5*"], True),
    ("atlas", ["libatlas.*", "libatlas_io*", "libpluto*"], True),
    ("multio", ["libmultio*"], True),
    ("mir", ["libmir*"], False),
    ("atlas-orca", ["libatlas-orca*", "libatlas_orca*"], False),
    ("fckit", ["libfckit*"], False),
    ("ecflow-light", ["libecflow*"], False),
]

# NEEDED names that identify an MPI dependency (never bundled)
MPI_RE = re.compile(
    r"^(libmpi[a-z_]*|libmpich[a-z_]*|libopen-(pal|rte)|libompi.*|libucp|libucs|libucm|libuct|"
    r"libhcoll|libocoms|libsharp|libnccl|libfabric|libpmix.*|libpmi[0-9]?|libevent.*|libhwloc|"
    r"libxpmem|libknem|libmca_.*)\.so(\.[0-9]+)*$"
)
# never graft these (provided by the system, see the portability contract)
SYSTEM_EXCLUDE = ["libstdc++.so.6", "libgcc_s.so.1"]

INIT_PY = '''\
"""multiolib: self-contained multio libraries (multio, eckit, eccodes, metkit, atlas, fdb5, ...).

Built by multio-bundle (``./multio-bundle wheel``). The package holds no Python code: pymultio finds
``lib64/libmultio-api.so`` through ``findlibs.find("multio-api", "multiolib")``. The eckit-based
libraries find their data in ``share/`` relative to ``lib64/``.
"""

import os as _os

__version__ = "{version}"
__multio_version__ = "{multio_version}"

LIB_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "lib64")
SHARE_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "share")
'''


def run(cmd, **kw):
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def readelf_dynamic(path):
    out = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def is_elf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def dyn_values(text, tag):
    return re.findall(r"\(%s\)\s+[^\[]*\[([^\]]*)\]" % tag, text)


def soname_of(path):
    v = dyn_values(readelf_dynamic(path), "SONAME")
    return v[0] if v else Path(path).name


def collect_libs(install):
    """Return {soname: real path} for every ELF shared library in <install>/lib*."""
    libs = {}
    for libdir in sorted(Path(install).glob("lib*")):
        if not libdir.is_dir():
            continue
        for f in sorted(libdir.iterdir()):
            if ".so" not in f.name or f.is_dir() or not is_elf(f):
                continue
            real = f.resolve()
            libs.setdefault(soname_of(real), real)
    return libs


def search_paths_of(libs):
    """Non-$ORIGIN RPATH/RUNPATH directories recorded in the install (where its external libs live)."""
    dirs = []
    for p in libs.values():
        dyn = readelf_dynamic(p)
        for tag in ("RPATH", "RUNPATH"):
            for v in dyn_values(dyn, tag):
                for d in v.split(":"):
                    if d and "$ORIGIN" not in d and d not in dirs and os.path.isdir(d):
                        dirs.append(d)
    return dirs


def mpi_needed(libs):
    found = set()
    for p in libs.values():
        for n in dyn_values(readelf_dynamic(p), "NEEDED"):
            if MPI_RE.match(n):
                found.add(n)
    return sorted(found)


def stage(install, pkg, libs, do_strip):
    lib64 = pkg / "lib64"
    lib64.mkdir(parents=True)
    for soname, real in libs.items():
        dst = lib64 / soname
        shutil.copyfile(real, dst)
        dst.chmod(0o755)
        if do_strip:
            run(["strip", "--strip-unneeded", dst])
        # drop the build/install RPATHs (they point into toolchain/module trees), keep only $ORIGIN
        run(["patchelf", "--set-rpath", "$ORIGIN", dst])
    share = Path(install) / "share"
    if share.is_dir():
        for entry in sorted(share.iterdir()):
            if entry.name.endswith("-bundle"):
                continue  # the bundle's own build log / arch files, not runtime data
            dst = pkg / "share" / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dst, symlinks=False)
            else:
                (pkg / "share").mkdir(exist_ok=True)
                shutil.copyfile(entry, dst)


def record_line(arcname, path):
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    digest = base64.urlsafe_b64encode(h.digest()).rstrip(b"=").decode()
    return "%s,sha256=%s,%d" % (arcname, digest, size)


def record_line_bytes(arcname, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return "%s,sha256=%s,%d" % (arcname, digest, len(data))


def stage_licenses(src_dir, pkg, libs):
    """Copy LICENSE/NOTICE (and eckit's LICENSES/) of every ECMWF project whose libraries are in the wheel
    from <src_dir>/<project>/ to <pkg>/licenses/<project>/."""
    if not src_dir:
        print("WARNING: no --src-dir: the ECMWF license notices are not added to the wheel", file=sys.stderr)
        return []
    done = []
    for project, patterns, core in ECMWF_PROJECTS:
        present = core or any(fnmatch.fnmatch(s, pat) for s in libs for pat in patterns)
        if not present:
            continue
        srcp = Path(src_dir) / project
        dst = pkg / "licenses" / project
        found = False
        for f in sorted(srcp.glob("*")) if srcp.is_dir() else []:
            if f.is_file() and re.match(r"^(LICENSE|LICENCE|NOTICE|COPYING)", f.name):
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dst / f.name)
                found = True
            elif f.is_dir() and f.name == "LICENSES":
                shutil.copytree(f, dst / f.name)
                found = True
        if found:
            done.append(project)
        else:
            print("WARNING: no LICENSE/NOTICE found for '%s' in %s" % (project, srcp), file=sys.stderr)
    print("make_multiolib_wheel: license notices for: %s" % ", ".join(done), flush=True)
    return done


def ldd_resolve(libs_dir, env):
    """{real file name: real path} of every external library the staged libraries resolve to (ldd)."""
    res = {}
    for f in sorted(Path(libs_dir).glob("*.so*")):
        out = subprocess.run(["ldd", str(f)], capture_output=True, text=True, env=env)
        for m in re.finditer(r"=>\s+(/\S+)", out.stdout):
            real = os.path.realpath(m.group(1))
            res.setdefault(os.path.basename(real), real)
    return res


def package_of(path):
    """(package, license) of the file that provides `path`: rpm, then dpkg, then 'unknown'."""
    for cmd, fmt in (
        (["rpm", "-qf", "--queryformat", "%{NAME}-%{VERSION} %{LICENSE}\\n", path], "rpm"),
        (["dpkg", "-S", path], "dpkg"),
    ):
        if shutil.which(cmd[0]) is None:
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            continue
        line = r.stdout.strip().splitlines()[0]
        if fmt == "rpm":
            pkg, _, lic = line.partition(" ")
            return pkg, lic or "see upstream"
        pkg = line.split(":")[0]
        return pkg, "see /usr/share/doc/%s/copyright" % pkg
    return None, None


# libraries that do not come from a system package (module trees): known upstream licenses
KNOWN_LICENSES = {"libaec": "BSD-2-Clause", "libsz": "BSD-2-Clause"}


def third_party_text(grafted, originals):
    lines = [
        "Third-party libraries bundled in multiolib.libs/ of this wheel (copied by auditwheel from the build",
        "machine). Each entry: bundled file, the original file it was copied from, the package that provides",
        "the original and its license. The license texts of the packages are not reproduced here: see the",
        "package's own documentation or upstream project. The ECMWF libraries in multiolib/lib64/ come with",
        "their licenses in multiolib/licenses/<project>/.",
        "",
    ]
    for name in grafted:
        orig = re.sub(r"-[0-9a-f]{8}(?=\.)", "", name)  # auditwheel: <stem>-<hash8><rest>
        path = originals.get(orig)
        lines.append("multiolib.libs/%s" % name)
        lines.append("  original: %s" % (path or orig + " (path not found)"))
        pkg, lic = package_of(path) if path else (None, None)
        if pkg:
            lines.append("  package:  %s" % pkg)
            lines.append("  license:  %s" % lic)
        else:
            known = next((v for k, v in KNOWN_LICENSES.items() if orig.startswith(k + ".")), None)
            lines.append("  package:  not provided by a system package (module/other tree)")
            lines.append("  license:  %s" % (
                "see upstream (%s: %s)" % (orig.split(".so")[0], known) if known else "see upstream"))
        lines.append("")
    return "\n".join(lines)


def add_to_wheel(wheel_in, wheel_out, extra):
    """Copy a wheel and add files ({arcname: bytes}); RECORD is updated."""
    with zipfile.ZipFile(wheel_in) as zin, zipfile.ZipFile(wheel_out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
        record_name = next(n for n in zin.namelist() if n.endswith(".dist-info/RECORD"))
        record = [l for l in zin.read(record_name).decode().splitlines() if l and l != record_name + ",,"]
        for info in zin.infolist():
            if info.filename == record_name:
                continue
            with zin.open(info) as s, zout.open(info, "w", force_zip64=True) as d:
                shutil.copyfileobj(s, d, 1 << 20)
        for arc, data in extra.items():
            zi = zipfile.ZipInfo(arc, date_time=(2020, 1, 1, 0, 0, 0))
            zi.external_attr = 0o644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, data)
            record.append(record_line_bytes(arc, data))
        record.append(record_name + ",,")
        zout.writestr(record_name, "\n".join(record) + "\n")


def write_wheel(stage_dir, out_dir, version, multio_version, tag, license_file):
    distinfo = "%s-%s.dist-info" % (DIST, version)
    di = stage_dir / distinfo
    di.mkdir()
    (di / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: %s\n"
        "Version: %s\n"
        "Summary: Self-contained multio libraries (multio, eckit, eccodes, metkit, atlas, fdb5) "
        "for pymultio, built by multio-bundle\n"
        "Home-page: https://github.com/ecmwf/multio\n"
        "License: Apache-2.0\n"
        "Requires-Python: >=3.10\n"
        "Requires-Dist: packaging\n" % (DIST, version)
    )
    (di / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: multio-bundle (make_multiolib_wheel.py)\n"
        "Root-Is-Purelib: false\nTag: %s\n" % tag
    )
    (di / "top_level.txt").write_text(DIST + "\n")
    if license_file and Path(license_file).is_file():
        shutil.copyfile(license_file, di / "LICENSE")
    wheel = out_dir / ("%s-%s-%s.whl" % (DIST, version.replace("-", "_"), tag))
    files = sorted(p for p in stage_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        record = []
        for p in files:
            arc = p.relative_to(stage_dir).as_posix()
            zi = zipfile.ZipInfo(arc, date_time=(2020, 1, 1, 0, 0, 0))
            zi.external_attr = (0o755 if p.suffix == ".so" or ".so." in p.name else 0o644) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as src, z.open(zi, "w", force_zip64=True) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            record.append(record_line(arc, p))
        record.append("%s/RECORD,," % distinfo)
        z.writestr("%s/RECORD" % distinfo, "\n".join(record) + "\n")
    return wheel


def is_inside(path, parent):
    """True if `path` is `parent` or below it (both resolved)."""
    try:
        Path(path).relative_to(parent)
        return True
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install-dir", required=True, help="multio-bundle install prefix")
    ap.add_argument("--src-dir", default=None,
                    help="bundle source dir: LICENSE/NOTICE of the ECMWF projects are copied from <src-dir>/<project>/")
    ap.add_argument("--multio-version", required=True, help="multio version (from multio/VERSION)")
    ap.add_argument("--out", required=True, help="directory for the final wheel")
    ap.add_argument("--work-dir", default=None,
                    help="scratch directory in which a new unique temporary directory is created and, unless "
                         "--keep-work, removed at the end (default: the system temp dir). The scratch directory "
                         "itself and its other contents are never touched.")
    ap.add_argument("--keep-work", action="store_true", help="do not remove the temporary directory")
    ap.add_argument("--license-file", default=None)
    ap.add_argument("--plat", default=None, help="auditwheel platform tag (default manylinux_2_28_<machine>)")
    ap.add_argument("--no-strip", action="store_true", help="keep debug info/symbol tables (much bigger wheel)")
    args = ap.parse_args()

    install = Path(args.install_dir).resolve()
    src = Path(args.src_dir).resolve() if args.src_dir else None
    out = Path(args.out).resolve()
    machine = platform.machine()
    version = args.multio_version + "+bundle"
    plat = args.plat or ("manylinux_2_28_" + machine)

    if out == install or (src and out == src):
        ap.error("--out must not be the install dir or the source dir")

    scratch = Path(args.work_dir).resolve() if args.work_dir else None
    if scratch:
        scratch.mkdir(parents=True, exist_ok=True)
    # a uniquely owned child: the only thing this script ever deletes
    work = Path(tempfile.mkdtemp(prefix="multiolib-wheel.", dir=scratch)).resolve()
    try:
        for what, p in (("--out", out), ("--install-dir", install), ("--src-dir", src)):
            if p and is_inside(p, work):
                ap.error("%s must not be inside the temporary work directory %s" % (what, work))
        build(args, install, src, out, work, machine, version, plat)
    finally:
        if args.keep_work:
            print("make_multiolib_wheel: kept work directory %s" % work)
        else:
            shutil.rmtree(work)


def build(args, install, src, out, work, machine, version, plat):
    libs = collect_libs(install)
    if "libmultio-api.so" not in libs:
        sys.exit("make_multiolib_wheel: no libmultio-api.so in %s/lib*" % install)
    print("make_multiolib_wheel: %d libraries from %s" % (len(libs), install))

    mpi = mpi_needed(libs)
    excludes = list(SYSTEM_EXCLUDE) + mpi
    if mpi:
        print(
            "\n"
            "WARNING: this install links against MPI (%s).\n"
            "         MPI is NOT bundled: the wheel only works where a compatible MPI is loadable at\n"
            "         run time (e.g. 'module load hpcx-openmpi', LD_LIBRARY_PATH).\n"
            "         For a self-contained wheel build the install with './multio-bundle build ... --without-mpi'.\n"
            % ", ".join(mpi),
            file=sys.stderr,
            flush=True,
        )

    stage_dir = work / "stage"
    pkg = stage_dir / DIST
    stage(install, pkg, libs, not args.no_strip)
    (pkg / "__init__.py").write_text(INIT_PY.format(version=version, multio_version=args.multio_version))
    stage_licenses(src, pkg, libs)

    raw_dir = work / "raw"
    raw_dir.mkdir()
    raw = write_wheel(stage_dir, raw_dir, version, args.multio_version, "py3-none-linux_" + machine, args.license_file)
    print("make_multiolib_wheel: staged wheel %s (%.0f MB)" % (raw.name, raw.stat().st_size / 1e6))

    # auditwheel resolves external libraries with LD_LIBRARY_PATH: give it the directories that the
    # install's own RPATHs pointed to (e.g. the libaec module), so no module has to be loaded.
    env = dict(os.environ)
    extra = search_paths_of(libs)
    env["LD_LIBRARY_PATH"] = ":".join(extra + ([env["LD_LIBRARY_PATH"]] if env.get("LD_LIBRARY_PATH") else []))
    originals = ldd_resolve(pkg / "lib64", env)  # real file name -> original path, for THIRD_PARTY.txt

    cmd = ["auditwheel", "repair", "--plat", plat, "-w", work / "repaired", raw]
    for e in excludes:
        cmd += ["--exclude", e]
    try:
        run(cmd, env=env)
    except subprocess.CalledProcessError:
        sys.exit(
            "\nmake_multiolib_wheel: auditwheel could not repair the wheel for platform '%s'.\n"
            "  If it says 'too-recent versioned symbols', the install was built with a compiler whose C++ runtime is\n"
            "  newer than the system one: build it with the portable toolchain (arch/ecmwf/hpc2020/gcc-toolset/14,\n"
            "  --without-mpi). A wheel from such an install would not be portable (it would need the newer\n"
            "  libstdc++ of that compiler at run time)." % plat
        )
    repaired = sorted((work / "repaired").glob("*.whl"))
    if len(repaired) != 1:
        sys.exit("make_multiolib_wheel: auditwheel did not produce exactly one wheel")

    # list the libraries grafted by auditwheel with their package and license
    with zipfile.ZipFile(repaired[0]) as z:
        grafted = sorted(n.split("/", 1)[1] for n in z.namelist() if n.startswith(DIST + ".libs/") and not n.endswith("/"))
    third_party = third_party_text(grafted, originals)
    final_raw = work / "final" / repaired[0].name
    final_raw.parent.mkdir()
    add_to_wheel(repaired[0], final_raw, {DIST + "/licenses/THIRD_PARTY.txt": third_party.encode()})

    out.mkdir(parents=True, exist_ok=True)
    final = out / repaired[0].name
    shutil.copyfile(final_raw, final)
    print("make_multiolib_wheel: wrote %s (%.0f MB)" % (final, final.stat().st_size / 1e6))


if __name__ == "__main__":
    main()
