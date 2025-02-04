# -*- coding: utf-8 -*-
# vim: ts=4 sw=4 tw=100 et ai si
#
# Copyright (C) 2020-2021 Intel Corporation
# SPDX-License-Identifier: BSD-3-Clause
#
# Authors: Antti Laakso <antti.laakso@linux.intel.com>
#          Artem Bityutskiy <artem.bityutskiy@linux.intel.com>

"""
This module provides a capability for reading and writing to read and write CPU Model Specific
Registers. This module has been designed and implemented for Intel CPUs.
"""

import logging
from pathlib import Path
from pepclibs.helperlibs import LocalProcessManager, FSHelpers, KernelModule, Trivial
from pepclibs.helperlibs.Exceptions import Error
from pepclibs import CPUInfo

_CPU_BYTEORDER = "little"

# A special value which can be used to specify that all bits have to be set to "1" in methods like
# 'write_bits()'.
ALL_BITS_1 = object()

# Feature control MSR.
MSR_MISC_FEATURE_CONTROL = 0x1A4
MLC_STREAMER = 0
MLC_SPACIAL = 1
DCU_STREAMER = 2
DCU_IP = 3

_LOG = logging.getLogger()

class MSR:
    """
    This class provides helpers to read and write CPU Model Specific Registers.

    Public methods overview.

    1. Multiple CPUs.
        * Read/write entire MSR: 'read()', 'write()'.
        * Read/write MSR bits range: 'read_bits()', 'write_bits()'.
    2. Single CPU.
        * Read/write entire MSR: 'read_cpu()', 'write_cpu()'.
        * Read/write MSR bits range: 'read_cpu_bits()', 'write_cpu_bits()'.
    3. CPU-independent, involve no MSR read/write.
        * Get/set bits from/in a user-provided MSR value: 'get_bits()', 'set_bits()'.
    """

    def _cache_add(self, regaddr, regval, cpu, dirty=False):
        """Add CPU 'cpu' MSR at 'regaddr' with its value 'regval' to the cache."""

        if not self._enable_cache:
            return

        if cpu not in self._cache:
            self._cache[cpu] = {}
        if regaddr not in self._cache[cpu]:
            self._cache[cpu][regaddr] = {}

        self._cache[cpu][regaddr] = { "regval" : regval, "dirty" : dirty }

    def _cache_get(self, regaddr, cpu):
        """
        If MSR register at 'regaddr' is in the cache, return the cached value, otherwise return
        'None'.
        """

        if not self._enable_cache:
            return None
        if cpu not in self._cache:
            return None
        if regaddr not in self._cache[cpu]:
            return None

        return self._cache[cpu][regaddr]["regval"]

    def start_transaction(self):
        """
        Start transaction. All writes to MSR registers will be cached, and will only be written
        to the actual hardware on 'commit_transaction()'.
        """

        if self._in_transaction:
            raise Error("cannot start a transaction, it has already started")

        if not self._enable_cache:
            raise Error("transactions support requires caching to be enabled (see 'enable_cache' "
                        "argument of the 'MSR.MSR()' constructor.")

        self._in_transaction = True

    def commit_transaction(self):
        """
        Commit the transaction. Write all the MSR registers that have been modified after
        'start_transaction()'.
        """

        if not self._in_transaction:
            raise Error("cannot commit a transaction, it did not start")

        for cpu, cdata in self._cache.items():
            # Pick all the dirty data from the cache.
            to_write = []
            for regaddr in cdata:
                if cdata[regaddr]["dirty"]:
                    to_write.append((regaddr, cdata[regaddr]["regval"]))
                    cdata[regaddr]["dirty"] = False

            if not to_write:
                continue

            # Write all the dirty data.
            path = Path(f"/dev/cpu/{cpu}/msr")
            with self._pman.open(path, "wb") as fobj:
                for regaddr, regval in to_write:
                    try:
                        fobj.seek(regaddr)
                        regval_bytes = regval.to_bytes(self.regbytes, byteorder=_CPU_BYTEORDER)
                        fobj.write(regval_bytes)
                        _LOG.debug("CPU%d: commit MSR 0x%x: wrote 0x%x%s",
                                   cpu, regaddr, regval, self._pman.hostmsg)
                    except Error as err:
                        raise Error(f"failed to write '{regval:#x}' to MSR '{regaddr:#x}' of CPU "
                                    f"{cpu}:\nfailed to write to file '{path}'"
                                    f"{self._pman.hostmsg}:\n{err}") from err

        self._in_transaction = False

    def _normalize_bits(self, bits):
        """Validate and normalize bits range 'bits'."""

        orig_bits = bits
        try:
            if not Trivial.is_int(orig_bits[0]) or not Trivial.is_int(orig_bits[1]):
                raise Error("bad bits range '{bits}', must be a list or tuple of 2 integers")

            bits = (int(orig_bits[0]), int(orig_bits[1]))

            if bits[0] < bits[1]:
                raise Error(f"bad bits range ({bits[0]}, {bits[1]}), the first number must be "
                            f"greater or equal to the second number")

            bits_cnt = (bits[0] - bits[1]) + 1
            if bits_cnt > self.regbits:
                raise Error(f"too many bits in ({bits[0]}, {bits[1]}), MSRs only have "
                            f"{self.regbits} bits")
        except TypeError:
            raise Error("bad bits range '{bits}', must be a list or tuple of 2 integers") from None

        return bits

    def get_bits(self, regval, bits):
        """
        Fetch bits 'bits' from an MSR value 'regval'. The arguments are as follows.
          * regval - an MSR value to fetch the bits from.
          * bits - the MSR bits range. A tuple or a list of 2 integers: (msb, lsb), where 'msb' is
                   the more significant bit, and 'lsb' is a less significant bit. For example, (3,1)
                   would mean bits 3-1 of the MSR. In a 64-bit number, the least significant bit
                   number would be 0, and the most significant bit number would be 63.
        """

        bits = self._normalize_bits(bits)
        bits_cnt = (bits[0] - bits[1]) + 1
        mask = (1 << bits_cnt) - 1
        return (regval >> bits[1]) & mask

    def _read_cpu(self, regaddr, cpu):
        """Read an MSR at address 'regaddr' on CPU 'cpu'."""

        path = Path(f"/dev/cpu/{cpu}/msr")
        try:
            with self._pman.open(path, "rb") as fobj:
                fobj.seek(regaddr)
                regval = fobj.read(self.regbytes)
        except Error as err:
            raise Error(f"failed to read MSR '{regaddr:#x}' from file '{path}'"
                        f"{self._pman.hostmsg}:\n{err}") from err

        regval = int.from_bytes(regval, byteorder=_CPU_BYTEORDER)
        _LOG.debug("CPU%d: MSR 0x%x: read 0x%x%s", cpu, regaddr, regval, self._pman.hostmsg)

        return regval

    def read(self, regaddr, cpus="all"):
        """
        Read an MSR on CPUs 'cpus' and yield the result. The arguments are as follows.
          * regaddr - address of the MSR to read.
          * cpus - list of CPUs and CPU ranges. This can be either a list or a string containing a
                   comma-separated list. For example, "0-4,7,8,10-12" would mean CPUs 0 to 4, CPUs
                   7, 8, and 10 to 12. 'None' and 'all' mean "all CPUs" (default).

        Yields tuples of '(cpunum, regval)'.
          * cpunum - the CPU number the MSR was read from.
          * regval - the read MSR value.
        """

        cpus = self._cpuinfo.normalize_cpus(cpus)

        for cpu in cpus:
            # Return the cached value if possible.
            regval = self._cache_get(regaddr, cpu)
            if regval is None:
                # Not in the cache, read from the HW.
                regval = self._read_cpu(regaddr, cpu)
                self._cache_add(regaddr, regval, cpu, dirty=False)

            yield (cpu, regval)

    def read_cpu(self, regaddr, cpu):
        """
        Read an MSR at 'regaddr' on CPU 'cpu' and return read result. The arguments are as follows.
          * regaddr - address of the MSR to read.
          * cpu - the CPU to read the MSR at. Can be an integer or a string with an integer number.
        """

        regval = None
        for _, regval in self.read(regaddr, cpus=(cpu,)):
            pass

        return regval

    def read_bits(self, regaddr, bits, cpus="all"):
        """
        Read bits 'bits' from an MSR at 'regaddr' from CPUs in 'cpus' and yield the results. The
        arguments are as follows.
          * regaddr - address of the MSR to read the bits from.
          * bits - the MSR bits range (similar to the 'bits' argument in 'get_bits()').
          * cpus - the CPUs to read from (similar to the 'cpus' argument in 'read()').

        Yields tuples of '(cpunum, regval)'.
          * cpunum - the CPU number the MSR was read from.
          * val - the value in MSR bits 'bits'.
        """

        for cpunum, regval in self.read(regaddr, cpus):
            yield (cpunum, self.get_bits(regval, bits))

    def read_cpu_bits(self, regaddr, bits, cpu):
        """
        Read bits 'bits' from an MSR at 'regaddr' on CPU 'cpu'. The arguments are as follows.
          * regaddr - address of the MSR to read the bits from.
          * bits - the MSR bits range (similar to the 'bits' argument in 'get_bits()').
          * cpu - the CPU to read the MSR at. Can be an integer or a string with an integer number.
        """

        regval = self.read_cpu(regaddr, cpu)
        return self.get_bits(regval, bits)

    def set_bits(self, regval, bits, val):
        """
        Set bits 'bits' to value 'val' in an MSR value 'regval', and return the result. The
        arguments are as follows.
          * regval - an MSR register value to set the bits in.
          * bits - the bits range to set (similar to the 'bits' argument in 'get_bits()').
          * val - the value to set the bits to.
        """

        bits = self._normalize_bits(bits)
        bits_cnt = (bits[0] - bits[1]) + 1
        max_val = (1 << bits_cnt) - 1

        if val is ALL_BITS_1:
            val = max_val
        else:
            if not Trivial.is_int(val):
                raise Error(f"bad value {val}, please provide a positive integer")
            val = int(val)

        if val > max_val:
            raise Error(f"too large value {val} for bits range ({bits[0]}, {bits[1]})")

        clear_mask = max_val << bits[1]
        set_mask = val << bits[1]
        return (regval & ~clear_mask) | set_mask

    def _write(self, regaddr, regval, cpu, regval_bytes=None):
        """Write value 'regval' to MSR at 'regaddr' on CPU 'cpu."""

        if regval_bytes is None:
            regval_bytes = regval.to_bytes(self.regbytes, byteorder=_CPU_BYTEORDER)

        path = Path(f"/dev/cpu/{cpu}/msr")
        with self._pman.open(path, "wb") as fobj:
            try:
                fobj.seek(regaddr)
                fobj.write(regval_bytes)
                _LOG.debug("CPU%d: MSR 0x%x: wrote 0x%x", cpu, regaddr, regval)
            except Error as err:
                raise Error(f"failed to write '{regval:#x}' to MSR '{regaddr:#x}' of CPU {cpu}:\n"
                            f"failed to write to file '{path}'{self._pman.hostmsg}:\n"
                            f"{err}") from err

    def write(self, regaddr, regval, cpus="all"):
        """
        Write 'regval' to an MSR at 'regaddr' on CPUs in 'cpus'. The arguments are as follows.
          * regaddr - address of the MSR to write to.
          * regval - the value to write to the MSR.
          * cpus - the CPUs to write to (similar to the 'cpus' argument in 'read()').
        """

        cpus = self._cpuinfo.normalize_cpus(cpus)
        regval_bytes = None

        for cpu in cpus:
            if not self._in_transaction:
                if regval_bytes is not None:
                    regval_bytes = regval.to_bytes(self.regbytes, byteorder=_CPU_BYTEORDER)
                self._write(regaddr, regval, cpu, regval_bytes=regval_bytes)
                dirty = False
            else:
                dirty = True

            self._cache_add(regaddr, regval, cpu, dirty=dirty)

    def write_cpu(self, regaddr, regval, cpu):
        """
        Write 'regval' to an MSR at 'regaddr' on CPU 'cpu'. The arguments are as follows.
          * regaddr - address of the MSR to write to.
          * regval - the value to write to the MSR.
          * cpu - the CPU to write the MSR on. Can be an integer or a string with an integer number.
        """

        self.write(regaddr, regval, cpus=(cpu,))

    def write_bits(self, regaddr, bits, val, cpus="all"):
        """
        Write value 'val' to bits 'bits' of an MSR at 'regaddr' on CPUs in 'cpus'. The arguments are
        as follows.
          * regaddr - address of the MSR to write the bits to.
          * bits - the MSR bits range (similar to the 'bits' argument in 'get_bits()').
          * val - the integer value to write to MSR bits 'bits'. Use 'MSR.ALL_BITS_1' to set all
                  bits to '1'.
          * cpus - the CPUs to write to (similar to the 'cpus' argument in 'read()').
        """

        for cpunum, regval in self.read(regaddr, cpus):
            new_regval = self.set_bits(regval, bits, val)
            if regval != new_regval:
                self.write(regaddr, new_regval, cpunum)

    def write_cpu_bits(self, regaddr, bits, val, cpu):
        """
        Write value 'val' to bits 'bits' of an MSR at 'regaddr' on CPU 'cpu'. The arguments are
        as follows.
          * regaddr - address of the MSR to write the bits to.
          * bits - the MSR bits range (similar to the 'bits' argument in 'get_bits()').
          * val - the integer value to write to MSR bits 'bits'. Use 'MSR.ALL_BITS_1' to set all
                  bits to '1'.
          * cpu - the CPU to write the MSR on. Can be an integer or a string with an integer number.
        """

        self.write_bits(regaddr, bits, val, cpus=(cpu,))

    def _ensure_dev_msr(self):
        """
        Make sure that device nodes for accessing MSR registers are available. Try to load the MSR
        driver if necessary.
        """

        cpus = self._cpuinfo.get_cpus()
        dev_path = Path(f"/dev/cpu/{cpus[0]}/msr")
        if FSHelpers.exists(dev_path, self._pman):
            return

        drvname = "msr"
        msg = f"file '{dev_path}' is not available{self._pman.hostmsg}\nMake sure your kernel" \
              f"has the '{drvname}' driver enabled (CONFIG_X86_MSR)."
        try:
            self._msr_drv = KernelModule.KernelModule(drvname, pman=self._pman)
            loaded = self._msr_drv.is_loaded()
        except Error as err:
            raise Error(f"{msg}\n{err}") from err

        if loaded:
            raise Error(msg)

        try:
            self._msr_drv.load()
            self._unload_msr_drv = True
            FSHelpers.wait_for_a_file(dev_path, timeout=1, pman=self._pman)
        except Error as err:
            raise Error(f"{msg}\n{err}") from err

    def __init__(self, pman=None, cpuinfo=None, enable_cache=True):
        """
        The class constructor. The arguments are as follows.
          * pman - the process manager object that defines the host to run the measurements on.
          * cpuinfo - CPU information object generated by 'CPUInfo.CPUInfo()'.
          * enable_cache - by default, this class caches values read from MSRs. This means that
                           the first time an MSR is read, it will be read from the hardware, but the
                           subsequent reads will return the cached value. The writes are not cached
                           (write-through cache policy). This option can be used to disable
                           caching.

        Important: current implementation is not thread-safe. Can only be used by single-threaded
        applications (add locking to improve this).
        """

        self._pman = pman
        self._cpuinfo = cpuinfo
        self._enable_cache = enable_cache

        self._close_pman = pman is None
        self._close_cpuinfo = cpuinfo is None

        if not self._pman:
            self._pman = LocalProcessManager.LocalProcessManager()

        if not self._cpuinfo:
            self._cpuinfo = CPUInfo.CPUInfo(pman=self._pman)

        # MSR registers' size in bits and bytes.
        self.regbits = 64
        self.regbytes = self.regbits // 8

        self._msr_drv = None
        self._unload_msr_drv = False

        # The MSR I/O cache. Indexed by CPU number and MSR address. Contains MSR values.
        self._cache = {}
        # Whether there is an ongoing transaction.
        self._in_transaction = False

        self._ensure_dev_msr()

    def close(self):
        """Uninitialize the class object."""

        if getattr(self, "_msr_drv", None):
            if self._unload_msr_drv:
                self._msr_drv.unload()
            self._msr_drv = None

        for attr in ("_cpuinfo", "_pman"):
            obj = getattr(self, attr, None)
            if obj:
                if getattr(self, f"_close{attr}", False):
                    getattr(obj, "close")()
                setattr(self, attr, None)

    def __enter__(self):
        """Enter the runtime context."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context."""
        self.close()
