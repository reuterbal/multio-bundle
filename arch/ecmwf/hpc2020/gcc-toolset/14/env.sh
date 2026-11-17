# Source me to get the correct configure/build/run environment
#
# RHEL 8 gcc-toolset-14 (gcc/g++ 14, no MPI, no prgenv/gcc modules).
# It links the newer C++ runtime parts statically and needs only the system
# libstdc++/glibc, like the manylinux_2_28 build images: use it for builds whose
# libraries go into Python wheels (build with --without-mpi).

# Store tracing and disable (module is *way* too verbose)
{ tracing_=${-//[^x]/}; set +x; } 2>/dev/null

module_load() {
  echo "+ module load $*"
  module load $*
}
module_unload() {
  echo "+ module unload $*"
  module unload $*
}
module_purge() {
  echo "+ module purge"
  module purge
}

# Unload all modules to be certain
module_purge

# Compiler: RHEL 8 gcc-toolset-14
echo "+ source /opt/rh/gcc-toolset-14/enable"
source /opt/rh/gcc-toolset-14/enable
export CC=gcc
export CXX=g++

# Load modules
module_load cmake/3.31.6
module_load ninja/1.12.1
module_load python3/3.12.11-01
module_load aec/1.1.4

# Record the RPATH in the executable
export LD_RUN_PATH=$LD_LIBRARY_PATH

# Restore tracing to stored setting
{ if [[ -n "$tracing_" ]]; then set -x; else set +x; fi } 2>/dev/null
