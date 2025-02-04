#!/usr/bin/python3
#
# -*- coding: utf-8 -*-
# vim: ts=4 sw=4 tw=100 et ai si
#
# Copyright (C) 2020-2021 Intel Corporation
# SPDX-License-Identifier: BSD-3-Clause
#
# Author: Artem Bityutskiy <artem.bityutskiy@linux.intel.com>

"""
This module includes the "aspm" 'pepc' command implementation.
"""

import logging
from pepclibs.helperlibs.Exceptions import Error
from pepclibs import ASPM

_LOG = logging.getLogger()

def aspm_info_command(_, pman):
    """Implements the 'aspm info'. command"""

    with ASPM.ASPM(pman=pman) as aspm:
        cur_policy = aspm.get_policy()
        _LOG.info("Active ASPM policy%s: %s", pman.hostmsg, cur_policy)
        available_policies = ", ".join(aspm.get_policies())
        _LOG.info("Available policies: %s", available_policies)

def aspm_config_command(args, pman):
    """Implements the 'aspm config' command."""

    with ASPM.ASPM(pman=pman) as aspm:
        old_policy = aspm.get_policy()
        if not args.policy:
            _LOG.info("Active ASPM policy%s: %s", pman.hostmsg, old_policy)
            return

        if args.policy == old_policy:
            _LOG.info("ASPM policy%s is already '%s', nothing to change", pman.hostmsg, args.policy)
        else:
            aspm.set_policy(args.policy)
            new_policy = aspm.get_policy()
            if args.policy != new_policy:
                raise Error(f"ASPM policy{pman.hostmsg} was set to '{args.policy}', but it became "
                            f"'{new_policy}' instead")
            _LOG.info("ASPM policy%s was changed from '%s' to '%s'",
                      pman.hostmsg, old_policy, args.policy)
