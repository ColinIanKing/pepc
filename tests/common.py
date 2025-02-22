#!/usr/bin/env python3
#
# -*- coding: utf-8 -*-
# vim: ts=4 sw=4 tw=100 et ai si
#
# Copyright (C) 2020-2021 Intel Corporation
# SPDX-License-Identifier: BSD-3-Clause
#
# Author: Antti Laakso <antti.laakso@linux.intel.com>

"""Common bits for the 'pepc' tests."""

import re
import sys
import random
from contextlib import contextmanager
from unittest.mock import patch, mock_open
from pathlib import Path
from pepclibs import CPUInfo
from pepclibs.helperlibs import Procs, FSHelpers
from pepclibs.msr import MSR, PCStateConfigCtl
from pepclibs import pepc

def _get_mocked_data():
    """
    Get mocked data for testing purposes. The files with testdata can be opened only before mocking.
    Returns dictionary with following keys:
      * cstates - C-state info, similar to output of 'run_verify()' call from
                  'CPUIdle.get_cstates_info()'.
      * files - dictionary of file paths relevant to testing.
      * lscpu - output of 'lscpu' command used for testing.
    """

    mock_data = {}
    basepath = Path(__file__).parents[1].resolve()
    with open(basepath / "tests" / "data" / "cstates_info.txt", "r") as fobj:
        mock_data['cstates'] = fobj.readlines()

    with open(basepath / "tests" / "data" / "lscpu_info.txt", "r") as fobj:
        mock_data['lscpu'] = fobj.readlines()

    with open(basepath / "tests" / "data" / "lscpu_info_cpus.txt", "r") as fobj:
        mock_data['lscpu_cpus'] = fobj.readlines()

    return mock_data

def _get_mocked_files():
    """
    Get mocked files for testing purposes. Returns dictionary with file path as key and file content
    as value.
    """

    mock_data = {}
    for line in _MOCKED_DATA['cstates']:
        split = line.split(":")
        mock_data[split[0]] = split[1].strip()

    return mock_data

_MOCKED_DATA = _get_mocked_data()
_MOCKED_FILES = _get_mocked_files()

#pylint: disable=unused-argument
#pylint: disable=unused-variable

class mock_Proc(Procs.Proc):
    """Mocked version of 'Proc' class in pepclibs.helperlibs.Procs module."""

    def run_verify(self, command, **kwargs):
        """
        Mocked 'run_verify()' method. Inspect 'command' argument and return test data if command is
        relevant to the tests. Otherwise pass call to original method.
        """

        if re.match("find '.*' -type f -regextype posix-extended -regex", command):
            # Mock the call from CPUIdle._get_cstates_info().
            return (_MOCKED_DATA['cstates'], "")

        if command == "lscpu":
            # Mock the call from CPUInfo.get_lscpu_info().
            return (_MOCKED_DATA['lscpu'], "")

        if command == "lscpu --all -p=socket,node,core,cpu,online":
            # Mock the call from CPUInfo.CPUInfo._get_lscpu().
            return (_MOCKED_DATA['lscpu_cpus'], "")

        return self._parent_methods["run_verify"](command, **kwargs)

    def _get_mock_fobj(self, path, mode):
        """Prepare new file object."""

        # TODO: This implementation works only when single user access the file. I.e. when user A
        #       opens the file and write to it. User B opens the same file, but cannot see what user
        #       A wrote to it. Improve it by adding support for multiple users.

        if path in self._mock_fobj and self._mock_fobj[path].write.call_count:
            # Get last write value.
            read_data = self._mock_fobj[path].write.call_args.args[-1].strip()
        else:
            read_data = _MOCKED_FILES[str(path)]

        with patch("builtins.open", new_callable=mock_open, read_data=read_data) as m:
            self._mock_fobj[path] = open(path, mode)
        return self._mock_fobj[path]

    def open(self, path, mode):
        """Mocked 'open()'."""

        if str(path) in _MOCKED_FILES:
            return self._get_mock_fobj(path, mode)

        return super().open(path, mode)

    def __init__(self):
        """Initialize mock class instance."""

        super().__init__()

        self._mock_fobj = {}

        self._parent_methods = {}
        module = sys.modules[Procs.__name__]
        for name in ("run_verify",):
            if hasattr(module, name):
                self._parent_methods[name] = getattr(module, name)

class mock_MSR(MSR.MSR):
    """Mock version of MSR class in pepclibs.msr.MSR module."""

    def read_iter(self, regaddr, regsize=8, cpus="all"):
        """Mocked version of 'read_iter()'. Returns random data."""

        if regaddr in self._mocked_msr:
            mask = (1 << 8 * regsize) - 1
            read_data = int.to_bytes(self._mocked_msr[regaddr] & mask, regsize, byteorder="little")
        else:
            read_data = random.randbytes(regsize)

        with patch("builtins.open", new_callable=mock_open, read_data=read_data) as m_open:
            yield from super().read_iter(regaddr, regsize, cpus)

    def __init__(self, proc=None, cpuinfo=None):
        """Initialize MSR object with test data."""

        super().__init__(proc=proc, cpuinfo=cpuinfo)
        self._mocked_msr = {}
        # Use known values for Package C-state limits.
        self._mocked_msr[PCStateConfigCtl.MSR_PKG_CST_CONFIG_CONTROL] = 0x14000402

def mock_lsdir(path: Path, must_exist: bool = True, proc=None):
    """Mock version of 'lsdir' function in FSHelpers module."""

    m_paths = [Path(m_path) for m_path in _MOCKED_FILES if str(path) in m_path]

    if not m_paths:
        yield from FSHelpers.lsdir(path, must_exist=must_exist, proc=proc)
    else:
        # Use test data to generate output similar to 'lsdir()'.
        entries = []
        for m_path in m_paths:
            einfo = {}
            m_path = m_path.relative_to(path)
            einfo["name"] = m_path.parts[0]
            einfo["ftype"] = "/" if len(m_path.parts) > 1 else ""

            if einfo not in entries:
                entries.append(einfo)

        for einfo in entries:
            yield (einfo["name"], path / einfo["name"], einfo["ftype"])

@contextmanager
def get_mocked_objects():
    """
    Helper function to mock 'lsdir()' function in FSHelpers module, Proc and MSR classes. Returns
    objects as tuple.
    """

    with patch("pepclibs.helperlibs.FSHelpers.lsdir", new=mock_lsdir) as mock_FSHelpers_lsdir, \
         patch("pepclibs.helperlibs.Procs.Proc", new=mock_Proc) as mock_proc, \
         patch("pepclibs.msr.MSR.MSR", new=mock_MSR) as mock_msr:
        yield (mock_FSHelpers_lsdir, mock_proc, mock_msr)

def get_test_cpu_info():
    """
    Helper function to return information about the emulated CPU. Emulated methods are same as in
    'get_mocked_objects()'. Returns information as a dictionary.
    """

    with get_mocked_objects() as _, CPUInfo.CPUInfo() as cpuinfo:
        result = {}
        result["cpus"] = cpuinfo.get_cpus()
        result["max_cpu"] = max(result["cpus"])
        result["cores"] = cpuinfo.get_cores()
        result["max_core"] = max(result["cores"])
        result["packages"] = cpuinfo.get_packages()
        result["max_package"] = max(result["packages"])

        return result

def run_pepc(arguments, exp_ret=None):
    """
    Run the 'pepc' command with arguments 'arguments'. Use mocked objects described in
    'get_mocked_objects()'. The 'exp_ret' value is the return value the command is expected to
    return. The test will pass, if the 'exp_ret' is not provided, or it is equal to the return
    value. Otherwise the test will fail.
    """

    with get_mocked_objects() as _:
        sys.argv = [f"{pepc.__file__}"] + arguments.split()
        ret = pepc.main()

        if exp_ret is not None:
            assert ret == exp_ret
