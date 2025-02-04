#!/usr/bin/env python3
#
# -*- coding: utf-8 -*-
# vim: ts=4 sw=4 tw=100 et ai si
#
# Copyright (C) 2020-2021 Intel Corporation
# SPDX-License-Identifier: BSD-3-Clause
#
# Author: Antti Laakso <antti.laakso@linux.intel.com>

"""Unittests for the public methods of the 'MSR' module."""

import random
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open, ANY
from MockedStuff import MockedProc
from pepclibs.msr import MSR, PMEnable, HWPRequest, MiscFeatureControl

_MSR_BYTES = 8
_TEST_DATA_BYTES = random.randbytes(_MSR_BYTES)
_TEST_DATA = int.from_bytes(_TEST_DATA_BYTES, byteorder="little")

#pylint:disable=no-self-use

@patch("builtins.open", new_callable=mock_open, read_data=_TEST_DATA_BYTES)
@patch("pepclibs.helperlibs.LocalProcessManager.LocalProcessManager", new=MockedProc)
class Test_v1_MSR(unittest.TestCase):
    """Unittests for the 'MSR' module."""

    # The MSR addresses that will be tested.
    _addrs = (PMEnable.MSR_PM_ENABLE, MiscFeatureControl.MSR_MISC_FEATURE_CONTROL,
              HWPRequest.MSR_HWP_REQUEST)

    def test_v1_read_cpu(self, m_open):
        """Test the 'read()' method, and verify output data."""

        with MSR.MSR() as msr:
            for addr in self._addrs:
                for cpu in (0, 1, 99):
                    res = msr.read_cpu(addr, cpu=cpu)
                    m_open.assert_called_with(Path(f"/dev/cpu/{cpu}/msr"), ANY)
                    m_open().seek.assert_called_with(addr)
                    self.assertEqual(res, _TEST_DATA)

    def test_v1_read(self, m_open):
        """Test the 'read()' method, and verify output."""

        with MSR.MSR() as msr:
            for addr in self._addrs:
                cpus = [0, 1, 3, 4]

                for cpu, res in msr.read(addr, cpus=cpus):
                    m_open.assert_called_with(Path(f"/dev/cpu/{cpu}/msr"), ANY)
                    m_open().seek.assert_called_with(addr)
                    self.assertEqual(cpu, cpus.pop(0))
                    self.assertEqual(res, _TEST_DATA)

                self.assertEqual(m_open().read.call_count, 4)
                m_open.reset_mock()

    def test_v1_write(self, m_open):
        """Test the 'write()' method, and verify call arguments."""

        with MSR.MSR() as msr:
            for addr in self._addrs:
                for cpu in (0, 1, 99):
                    msr.write(addr, _TEST_DATA, cpus=cpu)
                    m_open.assert_called_with(Path(f"/dev/cpu/{cpu}/msr"), ANY)
                    m_open().seek.assert_called_with(addr)

                    ref_data = int.to_bytes(_TEST_DATA, _MSR_BYTES, byteorder="little")
                    m_open().write.assert_called_with(ref_data)

if __name__ == '__main__':
    unittest.main()
