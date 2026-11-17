#!/usr/bin/env bash

# Build the Python wheels (pymultio + multiolib) from a multio-bundle install.
#
#   ./multio-bundle wheel --src-dir <src> --install-dir <install> [--arch <arch-dir>] [--out <dir>]
#
# Output (in <out>, default ./wheels):
#   pymultio-<ver>-py3-none-any.whl                      upstream Python bindings (import multio), unchanged
#   multiolib-<ver>+bundle-py3-none-manylinux_2_28_<arch>.whl
#                                                        our self-contained binary wheel: multio, eckit,
#                                                        eccodes, metkit, atlas, fdb5, ... + share/ data +
#                                                        grafted non-system libraries (see make_multiolib_wheel.py)
#                                                        + licenses/ (ECMWF LICENSE/NOTICE files, THIRD_PARTY.txt)
# Both can be pip-installed into any venv (Python >= 3.10) without any environment setup:
#   pip install pymultio-*.whl multiolib-*.whl
# Build the install with the gcc-toolset arch and --without-mpi for a fully portable wheel
# (see README): MPI libraries and the system C++ runtime are never bundled.

set -euo pipefail

BUNDLE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd -P )"

usage() {
    cat <<EOF
Usage: multio-bundle wheel --src-dir <src> --install-dir <install> [options]

Required:
  --src-dir <dir>      source dir of the bundle (uses <dir>/multio for pymultio and its version,
                       and the LICENSE/NOTICE files of the ECMWF projects)
  --install-dir <dir>  install prefix of the bundle (libs in lib/ or lib64/, data in share/)
Options:
  --arch <dir>         arch directory whose env.sh is sourced first (relative to the cwd or the bundle)
  --out <dir>          output directory (default: ./wheels)
  --build-venv <dir>   venv with the build tools (build, auditwheel, patchelf, setuptools); created and
                       filled from PyPI if missing (default: throwaway venv inside the work dir)
  --work-dir <dir>     scratch directory in which a new unique temporary directory is created for the
                       build (default: \$TMPDIR or /tmp; needs ~2 GB). Only that temporary directory is
                       removed at the end; <dir> itself and its other contents are never touched.
  --keep-work          do not remove the temporary directory
  --plat <tag>         auditwheel platform tag (default manylinux_2_28_<machine>)
  --no-strip           keep debug info in the libraries (huge wheel)
  -h, --help           this text
Options take their value as '--opt value' or '--opt=value'.
EOF
}

die() { echo "build-wheels.sh: $*" >&2; exit 1; }
usage_error() { echo "build-wheels.sh: $*" >&2; echo "Try 'build-wheels.sh --help'." >&2; exit 2; }

SRC_DIR=""; INSTALL_DIR=""; ARCH=""; OUT_DIR="$PWD/wheels"; BUILD_VENV=""; WORK_PARENT=""
KEEP_WORK=0; STRIP_ARG=""; PLAT_ARG=()

# value of a value-taking option: "--opt value" (2 words) or "--opt=value"; sets OPT_VALUE and OPT_SHIFT
take_value() {
    local opt="$1"; shift
    if [[ "$opt" == *=* ]]; then
        OPT_VALUE="${opt#*=}"; OPT_SHIFT=1; opt="${opt%%=*}"
    else
        [[ $# -ge 1 ]] || usage_error "option '$opt' needs a value"
        OPT_VALUE="$1"; OPT_SHIFT=2
        [[ "$OPT_VALUE" != --* ]] || usage_error "option '$opt' needs a value (got '$OPT_VALUE')"
    fi
    [[ -n "$OPT_VALUE" ]] || usage_error "option '$opt' needs a non-empty value"
}

OPT_VALUE=""; OPT_SHIFT=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --src-dir|--src-dir=*)         take_value "$@"; SRC_DIR="$OPT_VALUE" ;;
        --install-dir|--install-dir=*) take_value "$@"; INSTALL_DIR="$OPT_VALUE" ;;
        --arch|--arch=*)               take_value "$@"; ARCH="$OPT_VALUE" ;;
        --out|--out=*)                 take_value "$@"; OUT_DIR="$OPT_VALUE" ;;
        --build-venv|--build-venv=*)   take_value "$@"; BUILD_VENV="$OPT_VALUE" ;;
        --work-dir|--work-dir=*)       take_value "$@"; WORK_PARENT="$OPT_VALUE" ;;
        --plat|--plat=*)               take_value "$@"; PLAT_ARG=(--plat "$OPT_VALUE") ;;
        --keep-work)    KEEP_WORK=1; OPT_SHIFT=1 ;;
        --no-strip)     STRIP_ARG="--no-strip"; OPT_SHIFT=1 ;;
        -h|--help)      usage; exit 0 ;;
        *) echo "build-wheels.sh: unknown option '$1'" >&2; usage >&2; exit 2 ;;
    esac
    shift "$OPT_SHIFT"
done

[[ -n "$SRC_DIR" && -n "$INSTALL_DIR" ]] || { usage >&2; usage_error "--src-dir and --install-dir are required"; }
SRC_DIR="$(cd "$SRC_DIR" 2>/dev/null && pwd -P)" || die "source dir not found"
INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd -P)" || die "install dir not found"
MULTIO_SRC="$SRC_DIR/multio"
[[ -f "$MULTIO_SRC/pyproject.toml" && -f "$MULTIO_SRC/VERSION" ]] || die "$MULTIO_SRC is not a multio source tree"
ls "$INSTALL_DIR"/lib*/libmultio-api.so >/dev/null 2>&1 || die "no libmultio-api.so in $INSTALL_DIR/lib*"

# Build environment (arch env.sh files are not written for set -eu)
if [[ -n "$ARCH" ]]; then
    if   [[ -f "$ARCH/env.sh" ]];             then ARCH_ENV="$ARCH/env.sh"
    elif [[ -f "$BUNDLE_DIR/$ARCH/env.sh" ]]; then ARCH_ENV="$BUNDLE_DIR/$ARCH/env.sh"
    else die "no env.sh in arch dir '$ARCH'"; fi
    set +eu
    # shellcheck disable=SC1090
    source "$ARCH_ENV"
    set -eu
fi

command -v python3 >/dev/null || die "python3 not found (use --arch or load a python3 module)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "python3 >= 3.10 is needed to build the wheels (found: $(python3 -V 2>&1))"
for t in readelf strip; do command -v $t >/dev/null || die "$t not found"; done

# Output and scratch directories. The scratch dir may be any existing directory: the build happens in a new
# uniquely named child created by mktemp, and ONLY that child is ever removed (never a user-supplied path).
if [[ -n "$WORK_PARENT" ]]; then
    mkdir -p -- "$WORK_PARENT"; WORK_PARENT="$(cd "$WORK_PARENT" && pwd -P)"
else
    WORK_PARENT="${TMPDIR:-/tmp}"
fi
WORK_DIR="$(mktemp -d "$WORK_PARENT/multio-wheels.XXXXXX")" || die "cannot create a temporary directory in $WORK_PARENT"
WORK_DIR="$(cd "$WORK_DIR" && pwd -P)"
cleanup() {
    if [[ "$KEEP_WORK" == 1 ]]; then echo "kept work dir $WORK_DIR"
    elif [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then rm -rf -- "$WORK_DIR"; fi
}
trap cleanup EXIT

mkdir -p -- "$OUT_DIR"; OUT_DIR="$(cd "$OUT_DIR" && pwd -P)"
path_within() { [[ "$1" == "$2" || "$1" == "$2"/* ]]; }   # $1 equals or is inside $2
[[ "$OUT_DIR" != "$SRC_DIR" && "$OUT_DIR" != "$INSTALL_DIR" ]] || usage_error "--out must not be the source dir or the install dir"
path_within "$OUT_DIR" "$WORK_DIR" && usage_error "--out must not be inside the temporary work dir $WORK_DIR"
path_within "$SRC_DIR" "$WORK_DIR" && usage_error "--src-dir must not be inside the temporary work dir $WORK_DIR"
path_within "$INSTALL_DIR" "$WORK_DIR" && usage_error "--install-dir must not be inside the temporary work dir $WORK_DIR"

MULTIO_VERSION="$(tr -d '[:space:]' < "$MULTIO_SRC/VERSION")"

# Build tools in a venv
[[ -n "$BUILD_VENV" ]] || BUILD_VENV="$WORK_DIR/build-venv"
if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
    echo "+ python3 -m venv $BUILD_VENV"
    python3 -m venv "$BUILD_VENV"
fi
echo "+ pip install build tools into $BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install -q --upgrade pip
# patchelf < 0.18: newer versions pad every patched RHEL 8 library by ~2-6 MB (2 MB page alignment)
"$BUILD_VENV/bin/python" -m pip install -q build wheel "setuptools>=80" auditwheel "patchelf<0.18"
export PATH="$BUILD_VENV/bin:$PATH"

mkdir -p "$WORK_DIR/pymultio-src" "$WORK_DIR/pymultio-wheel"

# 1. pymultio: upstream wheel, built unchanged from a copy of the needed files
#    (building in place would write build/ and *.egg-info into the source tree)
echo "== pymultio wheel"
( cd "$MULTIO_SRC" && cp -r pyproject.toml VERSION README.md LICENSE NOTICE "$WORK_DIR/pymultio-src/" 2>/dev/null || true
  mkdir -p "$WORK_DIR/pymultio-src/python" && cp -r python/pymultio "$WORK_DIR/pymultio-src/python/" )
python -m pip wheel --no-deps -q -w "$WORK_DIR/pymultio-wheel" "$WORK_DIR/pymultio-src"
cp "$WORK_DIR"/pymultio-wheel/pymultio-*.whl "$OUT_DIR/"

# 2. multiolib: our own binary wheel (the script makes its own unique temporary child inside $WORK_DIR/multiolib-scratch)
echo "== multiolib wheel"
mkdir -p "$WORK_DIR/multiolib-scratch"
python "$BUNDLE_DIR/util/make_multiolib_wheel.py" \
    --install-dir "$INSTALL_DIR" --src-dir "$SRC_DIR" --multio-version "$MULTIO_VERSION" \
    --out "$OUT_DIR" --work-dir "$WORK_DIR/multiolib-scratch" \
    --license-file "$MULTIO_SRC/LICENSE" ${PLAT_ARG[@]+"${PLAT_ARG[@]}"} $STRIP_ARG \
    $( [[ "$KEEP_WORK" == 1 ]] && echo --keep-work )

echo "== done"
for w in "$OUT_DIR"/pymultio-*.whl "$OUT_DIR"/multiolib-*.whl; do
    printf '%s  %s\n' "$(du -h "$w" | cut -f1)" "$w"
done
python - "$OUT_DIR"/multiolib-*.whl <<'EOF'
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
grafted = sorted(n.split("/", 1)[1] for n in names if n.startswith("multiolib.libs/") and not n.endswith("/"))
print("grafted external libraries (%d):" % len(grafted))
for g in grafted:
    print("  " + g)
EOF
