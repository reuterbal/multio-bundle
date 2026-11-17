multio-bundle
=============

Build system based on ecbundle that downloads and builds **multio** (branch
`develop`) together with its ECMWF dependencies (eckit, eccodes, metkit, fdb5,
atlas, ...) in one go. It can also turn the installation into self-contained
**Python wheels** that you can `pip install` into any virtual environment.

Quick start on Atos (ECMWF hpc2020)
-----------------------------------

Run on a compute or interactive node (e.g. `ecinteractive -c 16`), not on a login node.

    git clone git@github.com:ecmwf/multio-bundle.git && cd multio-bundle
    ./multio-bundle create --src-dir source
    ./multio-bundle build \
        --arch arch/ecmwf/hpc2020/gnu/14.2.0/hpcx-openmpi/2.18.1 \
        --src-dir source --build-dir build \
        --ninja -j 16 --install --install-dir <install-dir>

Replace `<install-dir>` with the directory where multio should be installed.
`./multio-bundle build --help --src-dir source` lists all options.

Python wheels
-------------

Build without MPI using the `gcc-toolset/14` arch file into a separate `<install-dir-nompi>`,
then pack that installation:

    ./multio-bundle build \
        --arch arch/ecmwf/hpc2020/gcc-toolset/14 --without-mpi \
        --src-dir source --build-dir build-nompi \
        --ninja -j 16 --install --install-dir <install-dir-nompi>
    ./multio-bundle wheel \
        --src-dir source --install-dir <install-dir-nompi> \
        --arch arch/ecmwf/hpc2020/gcc-toolset/14 --out wheels

    module load python3/3.12.11-01    # Atos: the system python3 is too old
    python3 -m venv ~/myenv && source ~/myenv/bin/activate
    pip install wheels/*.whl
    python -c "import multio"

- Two wheels: `pymultio` (the Python interface, import name `multio`) and `multiolib`
  (all libraries, data and licences).
- No `module load` or environment variables are needed to use them.
- Requirements: Linux x86_64, glibc >= 2.28, Python >= 3.10, no MPI.
- `wheel` refuses installations not built with a manylinux_2_28-compatible compiler;
  use the `gcc-toolset` arch file.
- Do not install the PyPI package `multiolib` alongside.
- Building the wheels needs network access (`build`, `auditwheel`, `patchelf` from PyPI);
  see `util/build-wheels.sh --help` for more options.

Other Linux systems
-------------------

Run the same commands without `--arch`; compilers and tools are taken from `PATH`.
Prerequisites: C/C++17 compiler, CMake >= 3.18, python3 >= 3.10, MPI (unless `--without-mpi`),
libaec >= 1.1.4 (unless `--without-aec`), Fortran compiler (only for `--with-fortran`
and `--with-ecflow-light`).

Options
-------

Pass these to `./multio-bundle build`.

| Option                | Default | Effect                                                                     |
|-----------------------|---------|----------------------------------------------------------------------------|
| `--with-mir`          | off     | build mir, enable multio's interpolate action                              |
| `--with-fortran`      | off     | Fortran APIs (fckit, eccodes, atlas, multio, output manager)               |
| `--with-ecflow-light` | off     | build ecflow-light, enable ecFlow reporting in multio (needs Fortran)      |
| `--with-atlas-orca`   | off     | build atlas-orca (ORCA grids for atlas)                                    |
| `--with-legacy-ifsio` | off     | enable multio's legacy (FDB4) IFS I/O interface                            |
| `--without-mpi`       | MPI on  | build without MPI (use for wheels: MPI is not bundled)                     |
| `--without-aec`       | AEC on  | build without libaec/AEC compression                                       |
| `--with-tests`        | off     | build the tests of all projects                                            |

Branches
--------

multio is built from `develop`, all other projects from `master` (ecflow-light: `main`).
To pin a branch or tag, edit the project's `version:` in `bundle.yml` and run
`./multio-bundle create --update`. Branches move, so pin versions for reproducibility.

Tested on ECMWF Atos (RHEL 8) on 2026-11-17 with multio develop 218b2e16.

## License

[Apache License 2.0](LICENSE) In applying this licence, ECMWF does not waive the privileges and immunities granted to it by virtue of its status as an intergovernmental organisation nor does it submit to any jurisdiction.
