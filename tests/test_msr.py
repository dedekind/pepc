#!/usr/bin/env python3
#
# -*- coding: utf-8 -*-
# vim: ts=4 sw=4 tw=100 et ai si
#
# Copyright (C) 2022 Intel Corporation
# SPDX-License-Identifier: BSD-3-Clause
#
# Author: Antti Laakso <antti.laakso@linux.intel.com>

"""Unittests for the public methods of the 'MSR' module."""

from importlib import import_module
import pytest
from common import get_datasets, get_pman
from pepclibs import CPUInfo
from pepclibs.msr import MSR
from pepclibs.msr.TurboRatioLimit import MSR_TURBO_RATIO_LIMIT
from pepclibs.msr.TurboRatioLimit1 import MSR_TURBO_RATIO_LIMIT1
from pepclibs.helperlibs.Exceptions import Error

_MSR_MODULES = (
    "PMEnable", "MiscFeatureControl", "HWPRequest", "EnergyPerfBias", "FSBFreq", "HWPRequestPkg",
    "PCStateConfigCtl", "PlatformInfo", "PowerCtl", "TurboRatioLimit1", "TurboRatioLimit")

# Following features are safe for testing on real HW. The bits of each feature can be written to any
# value.
_SAFE_TO_TEST_FEATURES = ("epb", "epp", "pkg_control", "epp", "c1e_autopromote", "cstate_prewake",
                          "c1_demotion", "c1_undemotion", "l2_hw_prefetcher", "l1_adj_prefetcher",
                          "dcu_hw_prefetcher")

def _get_msr_objs(params):
    """
    Yield the 'MSR' objects initialized with different parameters that we want to run tests with.
    """

    with get_pman(params["hostname"], params["dataset"]) as pman:
        for enable_cache in (True, False):
            with MSR.MSR(pman=pman, enable_cache=enable_cache) as msr:
                yield msr

def _get_msr_test_params(params, include_ro=True, include_rw=True):
    """
    Yields the dictionary with information that should be used for testing MSR module methods. The
    dictionary has following keys:
      * addr - the MSR register address.
      * bits - list of bit ranges in 'addr' to test. This is a list of tuples. If 'include_ro' is
               'False', then read-only MSR bit ranges are not included. If 'include_rw' is False,
               then readable and writable MSR bit ranges are not included.
    """

    for addr, features in params["msrs"].items():
        bits = []
        for finfo in features.values():
            if finfo.get("writable"):
                if not include_rw:
                    continue
            elif not include_ro:
                continue

            if finfo["bits"]:
                bits.append(finfo["bits"])

        if bits:
            yield { "addr" : addr, "bits" : bits }

def _bits_to_mask(allbits):
    """
    Convert list of tuples to bit mask. You can refer to '_get_msr_test_params()' for the details on
    the list of tuples.
    """

    mask = 0
    for bits in allbits:
        bits_cnt = (bits[0] - bits[1]) + 1
        max_val = (1 << bits_cnt) - 1
        mask |= max_val << bits[1]
    return mask

def _get_bad_msr_cpu_nums(params):
    """Yield CPU numbers which should not be accepted by any method."""

    for cpu in (params["allcpus"][-1] + 1, -1, "ALL", "a"):
        yield cpu

def _get_good_msr_cpu_nums(params):
    """Yield good CPU numbers."""

    allcpus = params["allcpus"]
    medidx = int(len(allcpus)/2)
    for cpu in (allcpus[0], allcpus[medidx], allcpus[-1]):
        yield cpu

def _get_params(hostname, dataset, pman):
    """Implements the 'get_params()' fixture."""

    params = {}
    params["hostname"] = hostname
    params["dataset"] = dataset

    with CPUInfo.CPUInfo(pman=pman) as cpuinfo:
        allcpus = cpuinfo.get_cpus()
        medidx = int(len(allcpus)/2)
        params["testcpus"] = [allcpus[0], allcpus[medidx], allcpus[-1]]
        params["allcpus"] = allcpus

    # The MSR addresses that will be tested.
    params["msrs"] = {}
    for modname in _MSR_MODULES:
        msr_feature_class = getattr(import_module(f"pepclibs.msr.{modname}"), modname)
        with msr_feature_class(pman=pman, cpuinfo=cpuinfo) as msr:
            for name, finfo in msr._features.items(): # pylint: disable=protected-access
                if not msr.is_feature_supported(name):
                    continue
                if hostname != "emulation" and name not in _SAFE_TO_TEST_FEATURES:
                    continue
                if msr.regaddr not in params["msrs"]:
                    params["msrs"][msr.regaddr] = {}
                params["msrs"][msr.regaddr].update({name : finfo})

    return params

@pytest.fixture(name="params", scope="module", params=get_datasets())
def get_params(hostname, request):
    """
    Yield a dictionary with information we need for testing. For example, to optimize the test
    duration, use only subset of all CPUs available on target system to run tests on.
    """

    dataset = request.param
    with get_pman(hostname, dataset) as pman:
        yield _get_params(hostname, dataset, pman)

def _test_msr_read_good(params):
    """Test 'read()' method for good option values."""

    for msr in _get_msr_objs(params):
        for tp in  _get_msr_test_params(params):
            for cpu, _ in msr.read(tp["addr"], cpus=params["testcpus"]):
                assert cpu in params["testcpus"]

            read_cpus = []
            for cpu, _ in msr.read(tp["addr"]):
                read_cpus.append(cpu)
            assert read_cpus == params["allcpus"]

def _test_msr_read_bad(params):
    """Test 'read()' method for bad option values."""

    for msr in _get_msr_objs(params):
        tp = next(_get_msr_test_params(params))
        for bad_cpu in _get_bad_msr_cpu_nums(params):
            with pytest.raises(Error):
                for cpu, _ in msr.read(tp["addr"], cpus=[bad_cpu]):
                    assert cpu == bad_cpu

def test_msr_read(params):
    """Test the 'read()' method of the 'MSR' class."""

    _test_msr_read_good(params)
    _test_msr_read_bad(params)

def _test_msr_write_good(params):
    """Test 'write()' method for good option values."""

    for msr in _get_msr_objs(params):
        for tp in  _get_msr_test_params(params, include_ro=False):
            val = msr.read_cpu(tp["addr"], params["testcpus"][0])
            mask = _bits_to_mask(tp["bits"])
            newval = mask ^ val
            msr.write(tp["addr"], newval, cpus=params["testcpus"])

            for cpu, val in msr.read(tp["addr"], cpus=params["testcpus"]):
                assert cpu in params["testcpus"]
                assert val == newval

            msr.write(tp["addr"], val)
            for cpu, newval in msr.read(tp["addr"]):
                assert cpu in params["allcpus"]
                assert val == newval

def _test_msr_write_bad(params):
    """Test 'write()' method for bad option values."""

    tp = next(_get_msr_test_params(params))

    for msr in _get_msr_objs(params):
        val = msr.read_cpu(tp["addr"], params["testcpus"][0])
        for bad_cpu in _get_bad_msr_cpu_nums(params):
            with pytest.raises(Error):
                msr.write(tp["addr"], val, cpus=bad_cpu)

    # Following test will expect failure when writing to readonly MSR. On emulated host, such writes
    # don't fail.
    if params["hostname"] == "emulation":
        return

    for msr in _get_msr_objs(params):
        for tp in  _get_msr_test_params(params, include_rw=False):
            # Writes to Turbo MSRs pass, skip them.
            if tp["addr"] in (MSR_TURBO_RATIO_LIMIT, MSR_TURBO_RATIO_LIMIT1):
                continue

            val = msr.read_cpu(tp["addr"], params["testcpus"][0])
            mask = _bits_to_mask(tp["bits"])
            with pytest.raises(Error):
                msr.write(tp["addr"], mask ^ val, cpus=params["testcpus"])

def test_msr_write(params):
    """Test the 'write()' method of the 'MSR' class."""

    _test_msr_write_good(params)
    _test_msr_write_bad(params)

def _test_msr_read_cpu_good(params):
    """Test the 'read_cpu()' method for good option values."""

    for msr in _get_msr_objs(params):
        for tp in  _get_msr_test_params(params):
            for cpu in _get_good_msr_cpu_nums(params):
                msr.read_cpu(tp["addr"], cpu=cpu)

def _test_msr_read_cpu_bad(params):
    """Test the 'read_cpu()' method for bad option values."""

    tp = next(_get_msr_test_params(params))
    for msr in _get_msr_objs(params):
        for bad_cpu in _get_bad_msr_cpu_nums(params):
            with pytest.raises(Error):
                msr.read_cpu(tp["addr"], cpu=bad_cpu)

def test_msr_read_cpu(params):
    """Test the 'read_cpu()' method."""

    _test_msr_read_cpu_good(params)
    _test_msr_read_cpu_bad(params)

def _test_msr_write_cpu_good(params):
    """Test the 'write_cpu()' method for good option values."""

    for msr in _get_msr_objs(params):
        for tp in _get_msr_test_params(params, include_ro=False):
            mask = _bits_to_mask(tp["bits"])
            for cpu in _get_good_msr_cpu_nums(params):
                val = msr.read_cpu(tp["addr"], cpu)
                newval = mask ^ val
                msr.write_cpu(tp["addr"], newval, cpu)
                assert newval == msr.read_cpu(tp["addr"], cpu)

def _test_msr_write_cpu_bad(params):
    """Test the 'write_cpu()' method for bad option values."""

    tp = next(_get_msr_test_params(params))

    for msr in _get_msr_objs(params):
        val = msr.read_cpu(tp["addr"], params["testcpus"][0])
        for bad_cpu in _get_bad_msr_cpu_nums(params):
            with pytest.raises(Error):
                msr.write_cpu(tp["addr"], val, bad_cpu)

def test_msr_write_cpu(params):
    """Test the 'write_cpu()' method."""

    _test_msr_write_cpu_good(params)
    _test_msr_write_cpu_bad(params)

def _test_msr_read_bits_good(params):
    """Test 'read_bits()' method for good option values."""

    for msr in _get_msr_objs(params):
        for tp in _get_msr_test_params(params, include_ro=False):
            for bits in tp["bits"]:
                for cpu, _ in msr.read_bits(tp["addr"], bits, cpus=params["testcpus"]):
                    assert cpu in params["testcpus"]

        for tp in _get_msr_test_params(params, include_ro=False):
            bits = tp["bits"][0]
            read_cpus = []
            for cpu, _ in msr.read_bits(tp["addr"], bits):
                read_cpus.append(cpu)
            assert read_cpus == params["allcpus"]

            # No need to test 'read_bits()' with default 'cpus' argument multiple times.
            break

def _test_msr_read_bits_bad(params):
    """Test 'read_bits()' method for bad option values."""

    tp = next(_get_msr_test_params(params))
    bits = tp["bits"][0]
    cpu = params["testcpus"][0]

    for msr in _get_msr_objs(params):

        for bad_cpu in _get_bad_msr_cpu_nums(params):
            with pytest.raises(Error):
                for cpu, _ in msr.read_bits(tp["addr"], bits, cpus=[bad_cpu]):
                    assert cpu == bad_cpu

        bad_bits = (msr.regbits + 1, 0)
        with pytest.raises(Error):
            for cpu1, _ in msr.read_bits(tp["addr"], bad_bits, cpus=cpu):
                assert cpu == cpu1

def test_msr_read_bits(params):
    """Test the 'read_bits()' method."""

    _test_msr_read_bits_good(params)
    _test_msr_read_bits_bad(params)

def _test_msr_write_bits_good(params):
    """Test the 'write_bits()' method with good option values."""

    for msr in _get_msr_objs(params):
        for tp in _get_msr_test_params(params, include_ro=False):
            mask = _bits_to_mask(tp["bits"])
            bits = tp["bits"][0]

            for cpu, val in msr.read(tp["addr"], cpus=params["testcpus"]):
                newval = msr.get_bits(val ^ mask, bits)
                msr.write_bits(tp["addr"], bits, newval, cpus=[cpu])

                for _, bitsval in msr.read_bits(tp["addr"], bits, cpus=[cpu]):
                    assert newval == bitsval

            val = msr.read_cpu(tp["addr"], params["testcpus"][0])
            newval = msr.get_bits(val ^ mask, bits)
            msr.write_bits(tp["addr"], bits, newval)
            for _, val in msr.read_bits(tp["addr"], bits):
                assert val == newval

def _test_msr_write_bits_bad(params):
    """Test the 'write_bits()' method with bad option values."""

    tp = next(_get_msr_test_params(params))
    bits = tp["bits"][0]
    cpu = params["testcpus"][0]

    for msr in _get_msr_objs(params):
        val = msr.read(tp["addr"], cpus=cpu)

        for bad_cpu in _get_bad_msr_cpu_nums(params):
            with pytest.raises(Error):
                msr.write_bits(tp["addr"], bits, val, cpus=[bad_cpu])

        bits_cnt = (bits[0] - bits[1]) + 1
        bad_val = (1 << bits_cnt)
        with pytest.raises(Error):
            msr.write_bits(tp["addr"], bits, bad_val, cpus=[cpu])

        bad_bits = (msr.regbits + 1, 0)
        with pytest.raises(Error):
            msr.write_bits(tp["addr"], bad_bits, val, cpus=[cpu])

        # Repeating this negative test for every CPU is an overkill.
        break

def test_msr_write_bits(params):
    """Test the 'write_bits()' method."""

    _test_msr_write_bits_good(params)
    _test_msr_write_bits_bad(params)

def _test_msr_read_cpu_bits_good(params):
    """Test 'read_cpu_bits()' method for good option values."""

    for msr in _get_msr_objs(params):
        for tp in _get_msr_test_params(params):
            for bits in tp["bits"]:
                for cpu in _get_good_msr_cpu_nums(params):
                    msr.read_cpu_bits(tp["addr"], bits, cpu)

def _test_msr_read_cpu_bits_bad(params):
    """Test 'read_cpu_bits()' method for bad option values."""

    tp = next(_get_msr_test_params(params))
    cpu = params["testcpus"][0]

    for msr in _get_msr_objs(params):
        bits = tp["bits"][0]
        for bad_cpu in _get_bad_msr_cpu_nums(params):
            with pytest.raises(Error):
                msr.read_cpu_bits(tp["addr"], bits, bad_cpu)

        bad_bits = (msr.regbits + 1, 0)
        with pytest.raises(Error):
            msr.read_cpu_bits(tp["addr"], bad_bits, cpu)

        # Repeating this negative test for every CPU is an overkill.
        break

def test_msr_read_cpu_bits(params):
    """Test the 'read_cpu_bits()' method."""

    _test_msr_read_cpu_bits_good(params)
    _test_msr_read_cpu_bits_bad(params)

def _test_msr_write_cpu_bits_good(params):
    """Test the 'write_cpu_bits()' method with good option values."""

    for msr in _get_msr_objs(params):
        for tp in _get_msr_test_params(params, include_ro=False):
            mask = _bits_to_mask(tp["bits"])
            for bits in tp["bits"]:
                for cpu in _get_good_msr_cpu_nums(params):
                    val = msr.read_cpu(tp["addr"], cpu)
                    newval = msr.get_bits(val ^ mask, bits)
                    msr.write_cpu_bits(tp["addr"], bits, newval, cpu)

                    val = msr.read_cpu_bits(tp["addr"], bits, cpu)
                    assert val == newval

def _test_msr_write_cpu_bits_bad(params):
    """Test the 'write_cpu_bits()' method with bad option values."""

    tp = next(_get_msr_test_params(params))
    bits = tp["bits"][0]
    cpu = params["testcpus"][0]

    for msr in _get_msr_objs(params):
        val = msr.read_cpu_bits(tp["addr"], bits, cpu)
        for bad_cpu in _get_bad_msr_cpu_nums(params):
            with pytest.raises(Error):
                msr.write_cpu_bits(tp["addr"], bits, val, bad_cpu)

        bits_cnt = (bits[0] - bits[1]) + 1
        bad_val = (1 << bits_cnt)
        with pytest.raises(Error):
            msr.write_cpu_bits(tp["addr"], bits, bad_val, cpu)

        bad_bits = (msr.regbits + 1, 0)
        with pytest.raises(Error):
            msr.write_cpu_bits(tp["addr"], bad_bits, val, cpu)

        # Repeating this negative test for every CPU is an overkill.
        break

def test_msr_write_cpu_bits(params):
    """Test the 'write_cpu_bits()' method."""

    _test_msr_write_cpu_bits_good(params)
    _test_msr_write_cpu_bits_bad(params)
