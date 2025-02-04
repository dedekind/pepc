# -*- coding: utf-8 -*-
# vim: ts=4 sw=4 tw=100 et ai si
#
# Copyright (C) 2020-2022 Intel Corporation
# SPDX-License-Identifier: BSD-3-Clause
#
# Authors: Antti Laakso <antti.laakso@linux.intel.com>
#          Artem Bityutskiy <artem.bityutskiy@linux.intel.com>

"""
This module provides a capability of reading and changing EPP (Energy Performance Preference) on
Intel CPUs.
"""

import logging
from pathlib import Path
from pepclibs.helperlibs.Exceptions import Error, ErrorNotFound, ErrorNotSupported
from pepclibs.helperlibs import LocalProcessManager, Trivial, FSHelpers
from pepclibs import CPUInfo
from pepclibs.msr import MSR, HWPRequest, HWPRequestPkg

_LOG = logging.getLogger()

# The fall-back EPP policy to EPP value map.
#
# Note, we do not expose the values to the user because they are platform-specific (even though in
# current implementation they are not, but we can improve this later).
_EPP_POLICIES = {"performance": 0,
                 "balance_performance": 0x80,
                 "balance_power": 0xC0,
                 "power": 0xFF}

# The minimum and maximum EPP values.
_EPP_MIN, _EPP_MAX = 0, 0xFF

# A unique object used for the 'not_supported_ok' key in some methods.
_RAISE = object()

class EPP:
    """
    This module provides a capability of reading and changing EPP (Energy Performance Preference) on
    Intel CPUs.

    Public methods overview.

    1. Multiple CPUs.
        * Get/set EPP: 'get_epp()', 'set_epp()'.
        * Get EPP policy name: 'get_epp_policy()'.
        * Get the list of available EPP policies: 'get_epp_policies()'.
    2. Single CPU.
        * Get/set EPP: 'get_cpu_epp()', 'set_cpu_epp()'.
        * Check if the CPU supports EPP: 'is_epp_supported()'
        * Get EPP policy name: 'get_cpu_epp_policy()'.
        * Get the list of available EPP policies: 'get_cpu_epp_policies()'.
    """

    def _get_msr(self):
        """Returns an 'MSR.MSR()' object."""

        if not self._msr:
            self._msr = MSR.MSR(self._pman, cpuinfo=self._cpuinfo)
        return self._msr

    def _get_hwpreq(self):
        """Returns an 'HWPRequest.HWPRequest()' object."""

        if not self._hwpreq:
            msr = self._get_msr()
            self._hwpreq = HWPRequest.HWPRequest(pman=self._pman, cpuinfo=self._cpuinfo, msr=msr)

        return self._hwpreq

    def _get_hwpreq_pkg(self):
        """Returns an 'HWPRequest.HWPRequest()' object."""

        if not self._hwpreq_pkg:
            msr = self._get_msr()
            self._hwpreq_pkg = HWPRequestPkg.HWPRequestPkg(pman=self._pman, cpuinfo=self._cpuinfo,
                                                           msr=msr)
        return self._hwpreq_pkg

    def _is_cached(self, key, cpu):
        """Returns 'True' if there is a cached value for 'key' and CPU 'cpu'."""

        if cpu in self._cache and key in self._cache[cpu]:
            return True
        return False

    def _remove_from_cache(self, key, cpu):
        """Remove key 'key' of CPU 'cpu' from the cache."""

        if cpu in self._cache and key in self._cache[cpu]:
            del self._cache[cpu][key]

    def _add_to_cache(self, key, val, cpu):
        """Add key 'key' of CPU 'cpu' to the cache."""

        if cpu not in self._cache:
            self._cache[cpu] = {}
        self._cache[cpu][key] = val

    def is_epp_supported(self, cpu):
        """Returns 'True' if EPP is supported, on CPU 'cpu', otherwise returns 'False'."""

        if self._is_cached("supported", cpu):
            return self._cache[cpu]["supported"]

        if FSHelpers.exists(Path(self._sysfs_epp_path % cpu), pman=self._pman):
            self._add_to_cache("supported", True, cpu)
        else:
            val = self._get_hwpreq().is_cpu_feature_supported("epp", cpu)
            self._add_to_cache("supported", val, cpu)

        return self._cache[cpu]["supported"]

    def _get_cpu_epp_policies(self, cpu, not_supported_ok=_RAISE):
        """Implements 'get_cpu_epp_policies()'."""

        if not self.is_epp_supported(cpu):
            if not_supported_ok is _RAISE:
                raise Error(f"CPU {cpu} does not support EPP")
            return None

        if self._is_cached("policies", cpu):
            return self._cache[cpu]["policies"]

        # Prefer using the names from the Linux kernel.
        path = self._sysfs_epp_policies_path % cpu
        line = FSHelpers.read(path, default=None, pman=self._pman)
        if line is None:
            policies = list(_EPP_POLICIES)
        else:
            policies = Trivial.split_csv_line(line, sep=" ")

        self._add_to_cache("policies", policies, cpu)
        return policies

    def get_epp_policies(self, cpus="all", not_supported_ok=_RAISE):
        """
        Yield (CPU number, List of supported EPP policy names) pairs for CPUs in 'cpus'.
          * cpus - same as in 'get_epp_policy()'.
          * not_supported_ok - same as in 'get_epp()'.
        """

        for cpu in self._cpuinfo.normalize_cpus(cpus):
            yield cpu, self._get_cpu_epp_policies(cpu, not_supported_ok=not_supported_ok)

    def get_cpu_epp_policies(self, cpu, not_supported_ok=_RAISE):
        """Return a list of all EPP policy names for CPU 'cpu."""

        cpu = self._cpuinfo.normalize_cpu(cpu)
        return self._get_cpu_epp_policies(cpu, not_supported_ok=not_supported_ok)

    def _get_cpu_epp_policy_from_sysfs(self, cpu):
        """
        Returns EPP policy name for CPU 'cpu' by reading it from sysfs. Returns 'None' if the kernel
        does not support EPP policy.
        """

        try:
            policy = FSHelpers.read(self._sysfs_epp_path % cpu, pman=self._pman)
        except ErrorNotFound:
            return None

        return policy.strip()

    def _get_cpu_epp_policy(self, cpu, not_supported_ok=_RAISE):
        """Returns EPP policy for CPU 'cpu'."""

        policy = self._get_cpu_epp_policy_from_sysfs(cpu)
        policies = self._get_cpu_epp_policies(cpu, not_supported_ok=not_supported_ok)
        if policies is None:
            return None

        if policy in policies:
            return policy

        if policy is not None:
            # We got a direct EPP value instead.
            return f"unknown EPP={policy}"

        # The kernel does not support EPP sysfs knobs. Try to figure the policy out.
        epp = self._get_cpu_epp(cpu, not_supported_ok=not_supported_ok)
        if epp is None:
            return None
        if epp in self._epp_rmap:
            return self._epp_rmap[epp]

        raise Error(f"unknown policy name for EPP value {epp} on CPU {cpu}{self._pman.hostmsg}")

    def get_epp_policy(self, cpus="all", not_supported_ok=_RAISE):
        """
        Yield (CPU number, EPP policy name) pairs for CPUs in 'cpus'.
          * cpus - list of CPUs and CPU ranges. This can be either a list or a string containing a
                   comma-separated list. For example, "0-4,7,8,10-12" would mean CPUs 0 to 4, CPUs
                   7, 8, and 10 to 12. 'None' and 'all' mean "all CPUs" (default).
          * not_supported_ok - same as in 'get_epp()'.
        """

        for cpu in self._cpuinfo.normalize_cpus(cpus):
            yield (cpu, self._get_cpu_epp_policy(cpu, not_supported_ok=not_supported_ok))

    def get_cpu_epp_policy(self, cpu, not_supported_ok=_RAISE):
        """Similar to 'get_epp_policy()', but for a single CPU 'cpu'."""

        cpu = self._cpuinfo.normalize_cpu(cpu)
        return self._get_cpu_epp_policy(cpu, not_supported_ok=not_supported_ok)

    def _get_cpu_epp(self, cpu, not_supported_ok=_RAISE):
        """Implements 'get_cpu_epp()'."""

        try:
            # Find out if EPP should be read from 'MSR_HWP_REQUEST' or 'MSR_HWP_REQUEST_PKG'.
            hwpreq = self._get_hwpreq()
            # Note, some Broadwell architecture-based platforms support EPP, but do not support
            # package control. Therefore, the "is_cpu_feature_supported()" check.
            pkg_control = hwpreq.is_cpu_feature_supported("pkg_control", cpu) and \
                          hwpreq.is_cpu_feature_enabled("pkg_control", cpu)
            epp_valid = hwpreq.is_cpu_feature_enabled("epp_valid", cpu)
            if pkg_control and not epp_valid:
                hwpreq = self._get_hwpreq_pkg()

            return hwpreq.read_cpu_feature("epp", cpu)
        except ErrorNotSupported as err:
            if not_supported_ok is _RAISE:
                raise Error(f"CPU {cpu} does not support EPP:\n{err}") from err
            return None

    def get_epp(self, cpus="all", not_supported_ok=_RAISE):
        """
        Yield (CPU number, EPP) pairs for CPUs in 'cpus'. The arguments are as follows:
          * cpus - the same as in 'set_epp()'.
          * not_supported_ok - controls what to do if EPP is not supported on a CPU. By default,
            'ErrorNotSupported' is raised, othewise 'None' is yielded as the EPP value.
        """

        for cpu in self._cpuinfo.normalize_cpus(cpus):
            yield (cpu, self._get_cpu_epp(cpu, not_supported_ok=not_supported_ok))

    def get_cpu_epp(self, cpu, not_supported_ok=_RAISE):
        """Similar to 'get_epp()', but for a single CPU 'cpu'."""

        cpu = self._cpuinfo.normalize_cpu(cpu)
        return self._get_cpu_epp(cpu, not_supported_ok=not_supported_ok)

    def _set_cpu_epp_via_sysfs(self, epp, cpu):
        """Set EPP to 'epp' for CPU 'cpu' via the sysfs file."""

        try:
            FSHelpers.write(self._sysfs_epp_path % cpu, epp, pman=self._pman)
        except ErrorNotFound:
            return None
        except Error as err:
            # Writing to the sysfs file failed, provide a meaningful error message.
            msg = f"failed to set EPP to {epp}{self._pman.hostmsg}: {err}"

            try:
                policies = self._get_cpu_epp_policies(cpu)
                policies = ", ".join(policies)
                msg += f"\nEPP must be an integer from 0 to 255 or one of: {policies}"
            except Error:
                pass

            raise Error(msg) from err

        return epp

    def _set_cpu_epp(self, epp, cpu):
        """Implements 'set_cpu_epp()'."""

        if not self.is_epp_supported(cpu):
            raise Error(f"CPU {cpu} does not support EPP")

        if Trivial.is_int(epp):
            Trivial.validate_int_range(epp, _EPP_MIN, _EPP_MAX, what="EPP")
        else:
            policies = self._get_cpu_epp_policies(cpu)
            policy = epp.lower()
            if policy not in policies:
                policy_names = ", ".join(self.get_cpu_epp_policies(cpu))
                raise Error(f"EPP policy '{epp}' is not supported{self._pman.hostmsg}, please "
                            f"provide one of the following EPP policy names: {policy_names}")

        if self._set_cpu_epp_via_sysfs(epp, cpu) == epp:
            # EPP was successfully set via syfs.
            return

        # Could not set EPP via sysfs because the running Linux kernel does not support it. Try to
        # set it via the MSR.
        hwpreq = self._get_hwpreq()
        if hwpreq.is_cpu_feature_supported("pkg_control", cpu) and \
           hwpreq.is_cpu_feature_enabled("pkg_control", cpu):
            # Override package control by setting the "EPP valid" bit.
            hwpreq.write_cpu_feature("epp_valid", "on", cpu)
        hwpreq.write_cpu_feature("epp", epp, cpu)

    def set_epp(self, epp, cpus="all"):
        """
        Set EPP for CPUs in 'cpus'. The arguments are as follows.
          * epp - the EPP value to set. Can be an integer, a string representing an integer, or one
                  of the EPP policy names.
          * cpus - list of CPUs and CPU ranges. This can be either a list or a string containing a
                   comma-separated list. For example, "0-4,7,8,10-12" would mean CPUs 0 to 4, CPUs
                   7, 8, and 10 to 12. 'None' and 'all' mean "all CPUs" (default).
        """

        for cpu in self._cpuinfo.normalize_cpus(cpus):
            self._set_cpu_epp(epp, cpu)

    def set_cpu_epp(self, epp, cpu):
        """Similar to 'set_epp()', but for a single CPU 'cpu'."""

        cpu = self._cpuinfo.normalize_cpu(cpu)
        self._set_cpu_epp(epp, cpu)

    def __init__(self, pman=None, cpuinfo=None, msr=None):
        """
        The class constructor. The argument are as follows.
          * pman - the process manager object that defines the host to manage EPP for.
          * cpuinfo - CPU information object generated by 'CPUInfo.CPUInfo()'.
          * msr - an 'MSR.MSR()' object which should be used for accessing MSR registers.
        """

        self._pman = pman
        self._cpuinfo = cpuinfo
        self._msr = msr

        self._close_pman = pman is None
        self._close_cpuinfo = cpuinfo is None
        self._close_msr = msr is None

        self._hwpreq = None
        self._hwpreq_pkg = None
        self._epp_rmap = {code:name for name, code in _EPP_POLICIES.items()}

        # The per-CPU cache for read-only data, such as policies list.
        self._cache = {}

        sysfs_base = "/sys/devices/system/cpu/cpufreq/policy%d"
        self._sysfs_epp_path = sysfs_base + "/energy_performance_preference"
        self._sysfs_epp_policies_path = sysfs_base + "/energy_performance_available_preferences"

        if not self._pman:
            self._pman = LocalProcessManager.LocalProcessManager()

        if not self._cpuinfo:
            self._cpuinfo = CPUInfo.CPUInfo(pman=self._pman)

        if self._cpuinfo.info["vendor"] != "GenuineIntel":
            raise ErrorNotSupported(f"unsupported vendor {cpuinfo.info['vendor']}{pman.hostmsg}. "
                                    f"Only Intel CPUs are supported.")

    def close(self):
        """Uninitialize the class object."""

        for attr in ("_hwpreq", "_hwpreq_pkg", "_msr", "_cpuinfo", "_pman"):
            obj = getattr(self, attr, None)
            if obj:
                if hasattr(self, f"_close{attr}"):
                    if getattr(self, f"_close{attr}"):
                        getattr(obj, "close")()
                else:
                    getattr(obj, "close")()
                setattr(self, attr, None)

    def __enter__(self):
        """Enter the runtime context."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context."""
        self.close()
