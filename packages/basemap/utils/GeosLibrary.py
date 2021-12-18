#! /usr/bin/env python
# -*- coding: utf8 -*-
#
# Copyright (c) 2021 Víctor Molina García

# GeosLibrary.py is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# GeosLibrary.py is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public License
# along with GeosLibrary.py. If not, see <https://www.gnu.org/licenses/>.
#
"""Helper class to download and build the GEOS library."""

import io
import os
import ssl
import glob
import shutil
import struct
import tempfile
import contextlib
import subprocess
import datetime as dt
from zipfile import ZipFile
try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen


URL_DATETIME_FMT = "%a, %d %b %Y %H:%M:%S GMT"
GEOS_BASEURL = "https://github.com/libgeos/geos/archive/refs/tags"


class GeosLibrary(object):
    """Helper class to download, build and install GEOS."""

    def __init__(self, version, root=None):
        """Initialise a new :class:`GeosLibrary` instance."""

        self.version_tuple = tuple(map(int, version.split(".")))

        if root is None:
            self.temp = True
            self.root = tempfile.mkdtemp(prefix="tmp_geoslibrary_")
        else:
            self.temp = False
            self.root = root
            try:
                os.makedirs(self.root)
            except OSError:
                pass

    def __del__(self):
        """Clean up after :class:`GeosLibrary` destruction."""

        if self.temp:
            try:
                shutil.rmtree(self.root)
            except OSError:
                pass

    @property
    def version(self):
        """GEOS library version in string format."""

        return ".".join(map(str, self.version_tuple))

    def download(self):
        """Download GEOS zip source code into :class:`GeosLibrary` root."""

        # Define download link.
        link = "{0}/{1}.zip".format(GEOS_BASEURL, self.version)
        suffix = os.path.splitext(link)[-1]

        # Define output path.
        zipname = "geos-{0}".format(link.rsplit("/", 1)[-1])
        zippath = os.path.join(self.root, zipname)

        # Handle creation of the HTTP request.
        kwargs = {}
        if hasattr(ssl, "SSLContext") and hasattr(ssl, "PROTOCOL_TLSv1_2"):
            kwargs.update(context=ssl.SSLContext(ssl.PROTOCOL_TLSv1_2))
        try:
            conn = urlopen(link, **kwargs)
        except TypeError:
            # Fallback if `urlopen` does not accept context.
            conn = urlopen(link)

        with contextlib.closing(conn):
            # Try to get the file timestamp from the HTTP request header.
            date = conn.headers.get("Last-Modified")
            if date is not None:
                date = dt.datetime.strptime(date, URL_DATETIME_FMT)
                date = date.replace(tzinfo=dt.timezone.utc)
                date = date.timestamp()
            with tempfile.NamedTemporaryFile(suffix=suffix) as tmpzipobj:
                # Copy the buffer into a temporary file.
                shutil.copyfileobj(conn, tmpzipobj)
                # Move the file descriptor pointer to the beginning and
                # simply copy it to the final destination.
                tmpzipobj.seek(0)
                with open(zippath, "wb") as zipobj:
                    shutil.copyfileobj(tmpzipobj, zipobj)
            # Assign the timestamps to the final file if available.
            if date is not None:
                os.utime(zippath, (date, date))

    def extract(self, overwrite=True):
        """Decompress GEOS zip source code into :class:`GeosLibrary` root."""

        # Download zip file if not present.
        zippath = os.path.join(self.root, "geos-{0}.zip".format(self.version))
        if not os.path.exists(zippath):
            self.download()

        # Remove destination folder if present and requested.
        zipfold = os.path.join(self.root, "geos-{0}".format(self.version))
        if os.path.exists(zipfold):
            if not overwrite:
                raise OSError("folder '{0}' already exists".format(zipfold))
            else:
                shutil.rmtree(zipfold)

        # Decompress zip file.
        with contextlib.closing(ZipFile(zippath, "r")) as fd:
            fd.extractall(self.root)

        # Ensure that GEOS internal sh scripts can be executed.
        for path in sorted(glob.glob(os.path.join(zipfold, "tools", "*.sh"))):
            os.chmod(path, 0o755)

        # Patch CMakeLists so that libgeos_c.so does not depend on libgeos.so.
        cmakefile = os.path.join(zipfold, "capi", "CMakeLists.txt")
        with io.open(cmakefile, "r", encoding="utf-8") as fd:
            lines = fd.readlines()
        with io.open(cmakefile, "wb") as fd:
            oldtext = "target_link_libraries(geos_c geos)"
            newtext = "target_link_libraries(geos_c geos-static)"
            for line in lines:
                fd.write(line.replace(oldtext, newtext).encode())

        # The SVN revision file is not created on the fly before 3.6.0.
        svn_hfile = os.path.join(zipfold, "geos_svn_revision.h")
        if self.version_tuple < (3, 6, 0) and not os.path.exists(svn_hfile):
            with io.open(svn_hfile, "wb") as fd:
                text = "#define GEOS_SVN_REVISION 0"
                fd.write(text.encode())

    def build(self, installdir=None, njobs=1):
        """Build and install GEOS from source."""

        # Download and extract zip file if not present.
        zipfold = os.path.join(self.root, "geos-{0}".format(self.version))
        self.extract(overwrite=True)

        # Define build directory.
        builddir = os.path.join(zipfold, "build")

        # Define installation directory.
        if installdir is None:
            installdir = os.path.expanduser("~/.local/share/libgeos")
        installdir = os.path.abspath(installdir)

        # Define configure options.
        config_opts = [
            "-DCMAKE_INSTALL_PREFIX={0}".format(installdir),
            "-DGEOS_ENABLE_TESTS=OFF",
            "-DCMAKE_BUILD_TYPE=Release",
        ]
        if os.name == "nt" and self.version_tuple < (3, 6, 0):
            config_opts = ["-G", "NMake Makefiles"] + config_opts

        # Define build options.
        build_env = os.environ.copy()
        build_opts = [
            "--config", "Release",
            "--target", "install",
        ]
        if os.name != "nt":
            build_env["MAKEFLAGS"] = "-j {0:d}".format(njobs)
        elif self.version_tuple < (3, 6, 0):
            win64 = (8 * struct.calcsize("P") == 64)
            build_opts.extend([
                "--",
                "WIN64={0}".format("YES" if win64 else "NO"),
                "BUILD_BATCH={0}".format("YES" if njobs > 1 else "NO")
            ])
        else:
            build_opts = ["-j", "{0:d}".format(njobs)] + build_opts

        # Now move to the GEOS source code folder and build with CMake.
        cwd = os.getcwd()
        try:
            # Ensure that the build directory exists.
            try:
                os.makedirs(builddir)
            except OSError:
                pass
            os.chdir(builddir)
            # Call cmake configure.
            subprocess.call(["cmake", ".."] + config_opts)
            # Ensure that the install directory exists.
            try:
                os.makedirs(installdir)
            except OSError:
                pass
            # Call cmake build and install.
            subprocess.call(["cmake", "--build", "."] + build_opts,
                            env=build_env)
        finally:
            os.chdir(cwd)
