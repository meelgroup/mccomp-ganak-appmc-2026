#!/bin/bash
set -x

ROOT_DIR=$(pwd)
rm -rf ganak_static

###
### build external dependencies

cd "$ROOT_DIR" || exit
rm -rf gmp-6.3.0
tar xzvf gmp-6.3.0.tar.xz
cd gmp-6.3.0 || exit
./configure --enable-cxx --enable-static --enable-shared
make "-j$(nproc)"
make install

cd "$ROOT_DIR" || exit
rm -rf mpfr-4.2.2
tar xzvf mpfr-4.2.2.tar.xz
cd mpfr-4.2.2 || exit
./configure --enable-static --enable-shared
make "-j$(nproc)"
make install

cd "$ROOT_DIR" || exit
cd flint || exit
rm -rf build
mkdir build
cd build || exit
cmake -DBUILD_SHARED_LIBS=OFF ..
make "-j$(nproc)"
make install

###
###- Set up dependencies managed by us

cd "$ROOT_DIR" || exit
cd cadical || exit
rm -rf build
mkdir build
cd build || exit
ln -s ../*build*.sh .

cd "$ROOT_DIR" || exit
cd cadiback || exit
rm -rf build
mkdir build
cd build || exit
ln -s ../*build*.sh .

cd "$ROOT_DIR" || exit
cd cryptominisat || exit
rm -rf build
mkdir build
cd build || exit
ln -s ../*build*.sh .

cd "$ROOT_DIR" || exit
cd treedecomp || exit
rm -rf build
mkdir build
cd build || exit
ln -s ../*build*.sh .
cd "$ROOT_DIR" || exit
cd sbva || exit
rm -rf build
mkdir build
cd build || exit
ln -s ../*build*.sh .

cd "$ROOT_DIR" || exit
cd arjun || exit
rm -rf build
mkdir build
cd build || exit
ln -s ../*build*.sh .

cd "$ROOT_DIR" || exit
cd approxmc || exit
rm -rf build
mkdir build
cd build || exit
ln -s ../*build*.sh .

cd "$ROOT_DIR" || exit
cd ganak || exit
rm -rf build
mkdir build
cd build || exit
ln -s ../*build*.sh .

###
### build ganak

cd "$ROOT_DIR/cadical/build" || exit 1
./build_static_release.sh
cd "$ROOT_DIR/cadiback/build" || exit 1
./build_static_release.sh
cd "$ROOT_DIR/cryptominisat/build" || exit 1
./build_static_release.sh
cd "$ROOT_DIR/sbva/build" || exit 1
./build_static.sh
cd "$ROOT_DIR/treedecomp/build" || exit 1
./build_static_release.sh
cd "$ROOT_DIR/arjun/build" || exit 1
./build_static_release.sh
cd "$ROOT_DIR/approxmc/build" || exit 1
./build_static_release.sh
cd "$ROOT_DIR/ganak/build" || exit 1
./build_static_release.sh

cp build/ganak ganak_static
ldd ganak_static
strip ganak_static
