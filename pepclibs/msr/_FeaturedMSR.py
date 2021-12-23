# -*- coding: utf-8 -*-
# vim: ts=4 sw=4 tw=100 et ai si
#
# Copyright (C) 2020-2021 Intel Corporation
# SPDX-License-Identifier: BSD-3-Clause
#
# Authors: Antti Laakso <antti.laakso@linux.intel.com>
#          Artem Bityutskiy <artem.bityutskiy@linux.intel.com>

"""
This module provides implements the base class for "featured" MSRs, such as
'MSR_PKG_CST_CONFIG_CONTROL'.
"""

import copy
import logging
from pepclibs.helperlibs import Procs, Human
from pepclibs.helperlibs.Exceptions import Error, ErrorNotSupported
from pepclibs import CPUInfo
from pepclibs.msr import MSR

_LOG = logging.getLogger()

class FeaturedMSR:
    """
    This is the base class for featured MSRs, such as 'MSR_PKG_CST_CONFIG_CONTROL'.
    """

    def _check_feature_support(self, fname):
        """
        Check if CPU model of host 'self._proc' supports the feature 'fname'. Raises
        'ErrorNotSupported' if the feature is not supported.
        """

        if fname not in self.features:
            features_str = ", ".join(set(self.features))
            raise Error(f"unknown feature '{fname}', known features are: {features_str}")

        finfo = self.features[fname]
        if finfo["supported"]:
            return

        raise ErrorNotSupported(f"the '{finfo['name']}' feature is not supported on "
                                f"{self._cpuinfo.cpudescr}{self._proc.hostmsg}")

    def _normalize_feature_value(self, feature, val):
        """
        Check that 'val' is a valid value fore feature 'feature' and converts it to a value suitable
        for writing the MSR register.
        """

        finfo = self.features[feature]

        if not finfo.get("vals"):
            return val

        val = str(val).lower()

        if "aliases" in finfo and val in finfo["aliases"]:
            val = finfo["aliases"][val]

        if val in finfo["vals"]:
            return finfo["vals"][val]

        vals = list(finfo["vals"]) + list(finfo.get("aliases", {}))
        vals_str = ", ".join(vals)
        raise Error(f"bad value '{val}' for the '{finfo['name']}' feature.\nUse one of: {vals_str}")

    def feature_supported(self, fname):
        """
        Returns 'True' if feature 'fname' is supported by the platform, returns 'False' otherwise.
        """

        try:
            self._check_feature_support(fname)
            return True
        except ErrorNotSupported:
            return False

    def set_feature(self, fname, val, cpus="all"):
        """
        Set feature 'fname' value to 'val' for CPUs 'cpus'. The arguments are as follows.
          * fname - name of the feature to set.
          * val - value to set the feature to.
          * cpus - the CPUs to set the feature for (same as in 'CPUIdle.get_cstates_info()').
        """

        _LOG.debug("set feature '%s' to value %s on CPU(s) %s%s", fname, val,
                   Human.rangify(self._cpuinfo.normalize_cpus(cpus)), self._proc.hostmsg)

        self._check_feature_support(fname)
        val = self._normalize_feature_value(fname, val)

        finfo = self.features[fname]

        if not finfo["writable"]:
            raise Error(f"'{fname}' is can not be modified, it is read-only")

        set_method = getattr(self, f"_set_{fname}", None)
        if set_method:
            set_method(val, cpus=cpus)
        else:
            self._msr.write_bits(self.regaddr, finfo["bits"], val, cpus=cpus)

    def get_feature(self, fname, cpu):
        """
        Returns value of feature 'fname' for CPU 'cpu'. The arguments are as follows.
          * fname - name of the feature to get.
          * cpus - CPU number to get the feature for.

        In case of a boolean "on/off" type of feature, return 'True' if the feature is enabled, and
        'False' otherwise.
        """

        self._check_feature_support(fname)

        get_method = getattr(self, f"_get_{fname}", None)
        if get_method:
            return get_method(cpu)

        val = self._msr.read_bits(self.regaddr, self.features[fname]["bits"], cpu=cpu)
        if "rvals" in self.features[fname]:
            val = self.features[fname]["rvals"][val]
        return val

    def feature_enabled(self, fname, cpu):
        """
        Just a limited version of 'get_feature()', accepts only boolean features, returns 'True' if
        the feature is enabled, returns 'False' otherwise. This method exists only because for some
        users this method name a bit more self-documenting. Indeed, compare:
          * if msr_reg.feature_enabled(): do_something()
          * if msr_reg.get_feature(): do_something()
        """

        if self.features[fname]["type"] == "bool":
            val = self.get_feature(fname, cpu)
            return val in {"on", "enabled"}

        raise Error(f"feature '{fname}' is not boolean, use 'get_feature()' instead")

    def _init_features_dict_supported(self):
        """
        Intitialize the 'supported' flag for all features in the 'self.featrues' dictionary by
        comparing current CPU model against the list of CPU models that support the feature.
        """

        for finfo in self.features.values():
            if not "cpumodels" in finfo:
                # No CPU models list, assumed the feature is supported.
                finfo["supported"] = True
            else:
                cpumodel = self._cpuinfo.info["model"]
                finfo["supported"] = cpumodel in finfo["cpumodels"]

    def _init_features_dict_defaults(self):
        """
        Walk through each feature in the 'self.featrues' dictionary and make sure that all the
        necessary keys are presint. Set the missing keys to their default values.
          * writable - a flag inidcating whether this feature can be modified. Default is 'True'.
        """

        for finfo in self.features.values():
            if not finfo["supported"]:
                continue

            if "writable" not in finfo:
                finfo["writable"] = True

            if "vals" in finfo:
                # Build the reverse dictionary for 'vals'.
                finfo["rvals"] = {}
                for name, code in finfo["vals"].items():
                    finfo["rvals"][code] = name

    def _init_features_dict(self):
        """
        Intitialize the 'features' dictionary with platform-specific information. The sub-classes
        can re-define this method and call inidividual '_init_features_dict_*()' methods.
       """

        self._init_features_dict_supported()
        self._init_features_dict_defaults()

    def _set_baseclass_attributes(self):
        """
        This method must be provided by the sub-class and it must initialized the following
        attributes:
          * self.features - the features dictionary.
          * self.regaddr - the featured MSR address.
          * self.regname = the featured MSR name.
        """

    def __init__(self, proc=None, cpuinfo=None, msr=None):
        """
        The class constructor. The argument are as follows.
          * proc - the 'Proc' or 'SSH' object that defines the host to run the measurements on.
          * cpuinfo - CPU information object generated by 'CPUInfo.CPUInfo()'.
          * msr - the 'MSR.MSR()' object to use for writing to the MSR register.
        """

        if not proc:
            proc = Procs.Proc()

        self._proc = proc
        self._cpuinfo = cpuinfo
        self._msr = msr

        self._close_proc = proc is None
        self._close_cpuinfo = cpuinfo is None
        self._close_msr = msr is None

        self.features = None
        self.regaddr = None
        self.regname = None

        self._set_baseclass_attributes()

        if not self._proc:
            self._proc = Procs.Proc()

        if not self._cpuinfo:
            self._cpuinfo = CPUInfo.CPUInfo(proc=self._proc)

        if not self._msr:
            self._msr = MSR.MSR(proc=self._proc)

        if self._cpuinfo.info["vendor"] != "GenuineIntel":
            raise ErrorNotSupported(f"unsupported {self._cpuinfo.descr}{self._proc.hostmsg}, "
                                    f"model-specific register {self.regaddr:#x} ({self.regname}) "
                                    f"is available only on Intel CPUs.")

        self.features = copy.deepcopy(self.features)
        self._init_features_dict()

    def close(self):
        """Uninitialize the class object."""

        for attr in ("_msr", "_cpuinfo", "_proc"):
            obj = getattr(self, attr, None)
            if obj:
                if getattr(self, f"_close_{attr}", False):
                    getattr(obj, "close")()
                setattr(self, attr, None)

    def __enter__(self):
        """Enter the runtime context."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context."""
        self.close()
