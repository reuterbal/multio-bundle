multio-bundle
==========

Build-system based on ecbuild, which bundles all ECMWF-dependencies
inside one large project, saving the extra work to make sure all
dependencies are in place.

This bundle downloads and builds **multio** (branch `develop`) together
with everything it needs (eckit, eccodes, metkit, fdb, atlas, ...), installs
it, and can turn the result into **Python wheels** that you can `pip install`
into any Python virtual environment.

Words used below:

- **bundle**: this repository. It lists the projects to download and build.
- **arch file**: a small `env.sh` script in `arch/...` that loads the compiler,
  CMake, MPI etc. (on Atos: the right `module load` lines).
- **wheel**: a `.whl` file, the ready-to-install package format of `pip`.
- **venv**: a Python virtual environment (`python3 -m venv <dir>`), a private
  folder of Python packages.

Contents: [Quick start on Atos](#quick-start-on-atos-ecmwf-hpc2020) |
[Generic Linux](#generic-linux-no-arch-file) |
[Projects and branches](#projects-and-branches) |
[Switches](#switches-bundle-options) |
[Python wheels](#python-wheels) | [What was tested](#what-was-tested)

Quick start on Atos (ECMWF hpc2020)
-----------------------------------

Run this on a compute or interactive node (e.g. `ecinteractive -c 16`), not on a
shared login node; the build takes about 15 minutes with 8 to 16 cores.

    # Clone this bundle
    git clone -b <THIS_BRANCH> git@github.com:ecmwf/multio-bundle.git
    cd multio-bundle

    # Download all projects into ./source (needs working SSH keys for GitHub)
    ./multio-bundle create --src-dir source

    # Configure, compile and install (here with 16 parallel jobs)
    ./multio-bundle build \
        --arch arch/ecmwf/hpc2020/gnu/14.2.0/hpcx-openmpi/2.18.1 \
        --src-dir source --build-dir build \
        --ninja -j 16 --install --install-dir $HOME/multio-install

The arch file `arch/ecmwf/hpc2020/gnu/14.2.0/hpcx-openmpi/2.18.1/env.sh` loads
gcc 14.2.0, hpcx-openmpi 2.18.1, CMake, ninja, python3 3.12 and aec 1.1.4.
To use the installation later, load the same modules (`source` the same `env.sh`).
The libraries are in `<install-dir>/lib64`, the headers in `<install-dir>/include`.

Partial command names are accepted, e.g. `./multio-bundle bui` or `./multio-bundle cr`.
Everything that is not `wheel` is passed on to [ecbundle](https://github.com/ecmwf/ecbundle)
(version 2.6.0, downloaded into `./ecbundle` on first use). Check the options with

    ./multio-bundle create --help
    ./multio-bundle build  --help --src-dir source

`./multio-bundle create --update` updates sources that were already downloaded;
`build` also accepts `--clean`, `--build-type RelWithDebInfo` and others from ecbundle
(these three were not run while preparing this README).
The other `arch/` directories come from upstream (IFS) and were not changed or tested here;
some of them load an `aec` older than 1.1.4, which is too old for the current eccodes.

### Python wheels on Atos

For wheels, build a second installation with the `gcc-toolset/14` arch file and
**without MPI** (reasons in [Python wheels](#python-wheels)), then pack it:

    ./multio-bundle build \
        --arch arch/ecmwf/hpc2020/gcc-toolset/14 --without-mpi \
        --src-dir source --build-dir build-nompi \
        --ninja -j 16 --install --install-dir $HOME/multio-install-nompi

    ./multio-bundle wheel \
        --src-dir source --install-dir $HOME/multio-install-nompi \
        --arch arch/ecmwf/hpc2020/gcc-toolset/14 --out wheels

This writes two files into `wheels/`: `pymultio-<version>-py3-none-any.whl` and
`multiolib-<version>+bundle-py3-none-manylinux_2_28_x86_64.whl` (about 23 MB).
Install them into any venv (Python 3.10 or newer; on Atos e.g. `module load python3/3.12.11-01`):

    python3 -m venv ~/myenv
    source ~/myenv/bin/activate
    pip install wheels/*.whl
    python -c "import multio; print(multio.__version__)"

No `module load` or environment variable is needed to use the wheels afterwards.
`pip` fetches the Python dependencies (numpy, cffi, pydantic, pyyaml, findlibs, packaging)
from PyPI, so the venv needs network access (or a local mirror).

Generic Linux (no arch file)
----------------------------

Without `--arch`, the build uses whatever compilers and tools are on your `PATH`:

    ./multio-bundle create --src-dir source
    ./multio-bundle build --src-dir source --build-dir build --ninja -j 8 \
        --install --install-dir $HOME/multio-install

Add `--without-mpi` and/or `--without-aec` if you have no MPI or no libaec.

Prerequisites:

| What | Needed |
|------|--------|
| C and C++17 compiler | always (gcc 14 was used) |
| CMake | >= 3.18 |
| ninja | optional (`--ninja`); without it, make is used (not tried) |
| MPI (e.g. OpenMPI) | unless `--without-mpi` |
| libaec | >= 1.1.4 (with headers), unless `--without-aec` |
| Fortran compiler | only for `--with-fortran` and `--with-ecflow-light` |
| python3 | >= 3.10, to run ecbundle and to build/use the wheels |
| git, network | `create` clones from GitHub (SSH keys needed); `wheel` downloads build tools from PyPI |

`./multio-bundle create` needs no compiler, but ecbundle needs a recent python3
(the system python3 on Atos is 3.6, too old; the script loads a `python3` module if there is one).

**Wheels on generic Linux.** The same `wheel` command works:

    ./multio-bundle build --without-mpi --src-dir source --build-dir build-nompi \
        --ninja -j 8 --install --install-dir $HOME/multio-install-nompi
    ./multio-bundle wheel --src-dir source --install-dir $HOME/multio-install-nompi --out wheels

(Not run as written: the generic build was tested only with `--without-mpi --without-aec`
and no install, and the `wheel` step only on Atos.) The portable (`manylinux_2_28`) wheel
needs libraries that only depend on the *system* C++ runtime. This holds if you compile with a
compiler that links newer C++ parts statically, such as RHEL 8's `gcc-toolset-14`, or inside a
`manylinux_2_28` container. With a plain newer GCC (e.g. gcc 14 from a module, built against its own
`libstdc++`), `./multio-bundle wheel` refuses with an error, because the result would only run on
systems with the same compiler runtime. That is on purpose.

Projects and branches
---------------------

`./multio-bundle create` clones every project to `<src-dir>/<name>` on the branch below.
Names, URLs and branches are in `bundle.yml`.

| Project | Repository | Branch | Built |
|---------|------------|--------|-------|
| ecbuild | ecmwf/ecbuild | master | always |
| eckit | ecmwf/eckit | master | always |
| eccodes | ecmwf/eccodes | master | always |
| fckit | ecmwf/fckit | master | with `--with-fortran` |
| ecflow-light | ecmwf/ecflow-light | main | with `--with-ecflow-light` |
| metkit | ecmwf/metkit | master | always |
| fdb5 | ecmwf/fdb | master | always |
| atlas | ecmwf/atlas | master | always (multio needs it) |
| mir | ecmwf/mir | master | with `--with-mir` |
| multio | ecmwf/multio | **develop** | always |
| atlas-orca | ecmwf/atlas-orca | master | with `--with-atlas-orca` |

The repositories are cloned over SSH (`git@github.com:ecmwf/...`), so GitHub SSH keys must work.

**Branches move**, so two builds made on different days can differ. To record what you built,
note the commit of each project (`git -C source/<name> rev-parse HEAD`).

To use another branch or tag of a project, edit its `version:` line in `bundle.yml`
(e.g. `version : 2.11.0` for multio), then run `./multio-bundle create --update`.
ecbundle can also read a `<BUNDLE>_<PROJECT>_VERSION` environment variable, where the names
are upper case with `-` replaced by `_`. For this bundle that gives for example
`MULTIO_DEVELOP_BUNDLE_MULTIO_VERSION=2.11.0 ./multio-bundle create` (the name is taken from the
code of ecbundle; this was not run).

Switches (bundle options)
-------------------------

Everything optional is off by default, except MPI and libaec.
Use the switches with `./multio-bundle build`.

| Switch | Default | Effect |
|--------|---------|--------|
| `--with-mir` | off | build mir and enable multio's interpolate action |
| `--with-fortran` | off | Fortran APIs: fckit, eccodes and atlas Fortran, multio Fortran and output manager; installs the `.mod` files; needs a Fortran compiler |
| `--with-ecflow-light` | off | build ecflow-light and enable ecFlow reporting in multio; needs a Fortran compiler even without `--with-fortran` |
| `--with-atlas-orca` | off | build atlas-orca (ORCA grids for atlas) |
| `--with-legacy-ifsio` | off | enable multio's legacy (FDB4) IFS I/O interface |
| `--without-mpi` | MPI on | build without MPI (needed for portable wheels) |
| `--without-aec` | AEC on | build without libaec/AEC compression |
| `--with-tests` | off | build the tests of all projects (**not tested**: `ctest` was never run) |

Also always on or off in `bundle.yml`: multio's FDB5 support is on; ODB in metkit is off;
eccodes has its definitions compiled in (`MEMFS`).
`./multio-bundle build --help --src-dir <src>` lists the switches (once `create` has run).
Switches can be combined; the combination `--with-fortran --without-mpi` was not tried.

Python wheels
-------------

`./multio-bundle wheel` (script `util/build-wheels.sh`) packs an installation into two wheels:

- **`pymultio`** (import name `multio`): the Python interface from the multio sources,
  unchanged. It is plain Python and contains no compiled code.
- **`multiolib`**: built here. It contains `libmultio-api.so` and all libraries that come with it
  (eckit, eccodes, metkit, atlas, fdb5, ...), the `share/` data folders (eccodes, metkit and multio
  configuration and samples), and the few non-system libraries they need (libaec, curl, openssl,
  ...; collected by `auditwheel`). `pymultio` finds `multiolib` on its own.

Install both into the venv of your own package:

    pip install pymultio-*.whl multiolib-*.whl

What you get:

- **No environment variables, no `module load`, no `LD_LIBRARY_PATH`** needed when using the wheels.
- **Portable:** Linux x86_64, glibc >= 2.28 (RHEL/Rocky 8 and newer, Ubuntu 20.04 and newer),
  Python >= 3.10, **no MPI** (MPI libraries are never put into a wheel; build the wheel's installation
  with `--without-mpi`; if the installation links MPI the command prints a warning).
- **Build requirements:** the install must come from a compiler that needs only the system C++
  runtime (`gcc-toolset/14` on Atos, see [Generic Linux](#generic-linux-no-arch-file)), and the build
  needs network access (it downloads `build`, `auditwheel` and `patchelf` from PyPI into a
  temporary venv) and about 2 GB of scratch space. Options such as `--out`, `--work-dir`, `--keep-work`
  are listed by `util/build-wheels.sh --help`. `--work-dir <dir>` names the *scratch directory* (default:
  `$TMPDIR` or `/tmp`): the build runs in a new temporary sub-directory of it, and only that
  sub-directory is removed at the end (unless `--keep-work`); `<dir>` and its other contents are never
  deleted. `--out` must not be the source or install directory.
- **Licenses:** the `multiolib` wheel contains the `LICENSE` and `NOTICE` files of the bundled ECMWF
  projects in `multiolib/licenses/<project>/`, and `multiolib/licenses/THIRD_PARTY.txt`, which lists every
  third-party library copied into `multiolib.libs/` with the system package it came from and its license.

Caveats:

- **Do not install the PyPI package `multiolib` next to this one**: it has the same package name.
- If the venv also has PyPI wheels of `eccodeslib`/`eckitlib` (for example through the `eccodes`
  Python package), a process can load two copies of those libraries. The libstdc++ clash test (below) passed, but this
  double loading was not tested.
- With the default switches the wheel has no Fortran, no mir and no ecFlow. Only a select/encode/file-sink
  plan was run from Python (see [What was tested](#what-was-tested)).

### Short example

Encode one field of a small global grid (O32) to a GRIB file with a multio plan:
select the field, encode it using a GRIB template shipped in the wheel, write it to a file.

```python
import numpy as np, multio, multiolib
from pathlib import Path

share = Path(multiolib.SHARE_DIR)                     # data folder inside the wheel
template = share / "multio/config/grib-samples/reduced_gg_sfc_grib2.tmpl"
npoints = 2 * sum(20 + 4 * i for i in range(32))      # points of the O32 grid

plan = {"plans": [{"name": "to-grib", "actions": [
    {"type": "select", "match": [{"category": "prognostic"}]},
    {"type": "encode", "format": "grib", "template": str(template), "atlas-named-grid": "O32"},
    {"type": "sink", "sinks": [{"type": "file", "append": False, "per-server": False, "path": "out.grib"}]},
]}]}

metadata = {"category": "prognostic", "domain": "g", "gridType": "reduced_gg", "levtype": "sfc",
    "param": 167, "paramId": 167, "class": "rd", "stream": "oper", "type": "fc", "expver": "xxxx",
    "date": 20240101, "time": 0, "step": 6, "level": 0, "bitsPerValue": 16, "precision": "double",
    "missingValue": 9999.0, "toAllServers": False, "name": "2t", "trigger": "step",
    "gribEdition": "2", "generatingProcessIdentifier": 148, "typeOfLevel": "surface",
    "globalSize": npoints, "misc-globalSize": npoints}   # both sizes are required

with multio.MultioPlan(plan):
    m = multio.Multio()
    m.open_connections(); m.write_field(metadata, np.linspace(250.0, 300.0, npoints))
    m.flush(); m.close_connections()
```

This writes `out.grib` (about 10 kB; multio may print a harmless line "REQUESTED ATLAS GRID DEFINITION UPDATE: O32"). Two quirks of multio's Python interface: the metadata needs `misc-globalSize`
in addition to `globalSize`, and boolean values such as `toAllServers` are passed on as numbers.
This example was run exactly as shown in a venv with the two wheels installed. The longer
test script also checks the GRIB message with eccodes and does a metkit data lookup.

What was tested
---------------

Tested on 2026-11-17 on an Atos interactive node (RHEL 8, 16 cores), with multio `develop`
at commit `218b2e16` (multio 2.12.0). The other projects were at their `master`/`main`
tips of that day: ecbuild d27768a, eckit 11fa27c9a, eccodes 4bbc3ab11, fckit 863eb59,
ecflow-light a3d14bc, metkit 933cba6a, fdb5 2308d270, atlas 29f6653ee, mir 5a2b08b8b, atlas-orca 91e1b0e.

Built and installed successfully:

| Configuration | Command line | Time |
|---------------|--------------|------|
| Default, Atos gnu 14.2.0 / hpcx-openmpi 2.18.1 | no switches | ~13 min (`-j 8`) |
| No MPI, Atos `gcc-toolset/14` (for wheels) | `--without-mpi` | ~11 min (`-j 8`) |
| Everything on, Atos gnu 14.2.0 | `--with-mir --with-fortran --with-ecflow-light --with-atlas-orca --with-legacy-ifsio` | ~14 min (`-j 12`) |
| Generic Linux, no arch file (only gcc, CMake, ninja, python3 modules; no MPI, no aec) | `--without-mpi --without-aec`, no install | ~11 min |

Also checked:

- Each of `--with-mir`, `--with-fortran`, `--with-ecflow-light`, `--with-atlas-orca`, `--with-legacy-ifsio`
  alone configures successfully (CMake configure only, no build).
- A small Fortran program, built as a separate CMake project with `find_package(multio)` against
  the "everything on" installation only, compiles, links and prints the multio version (2.12.0).
- Wheels (from the no-MPI installation): in a clean shell with only a `python3/3.12.11-01` module
  loaded, with the build and install directories hidden, and a new venv with the two wheels
  installed: `import multio` works, the library is the one inside the wheel, the 48 `pymultio`
  tests pass, and a GRIB2 field is encoded and read back with eccodes. The same smoke test also passes
  when the system `libstdc++` is loaded first, and with Python 3.11 (`python3/3.11.10-01`) in a
  second venv. `strace` showed that no file was read from the source, build or install directories.
- `./multio-bundle wheel` on the MPI installation prints the MPI warning, then stops with an
  error (its libraries need a newer C++ runtime than the system one).

**Not tested:**

- `ctest` / `--with-tests` (the tests were never built or run).
- `--with-fortran` together with `--without-mpi`.
- Linux distributions other than RHEL 8 (Atos), other CPUs than x86_64, other compilers than gcc.
- Generic Linux `wheel` step and the wheels outside the Atos node.
- fdb and mir plans from Python (the wheel contains the libraries, but no plan using them was run).
- The environment-variable version override, `--clean`, `--build-type`.

## License

[Apache License 2.0](LICENSE) In applying this licence, ECMWF does not waive the privileges and immunities granted to it by virtue of its status as an intergovernmental organisation nor does it submit to any jurisdiction.
