# SPDX-FileCopyrightText: 2026 Brad Barnett
#
# SPDX-License-Identifier: MIT
"""Under WSL, a COM port flashes with Windows esptool and a /dev/tty port with Linux's."""

import argparse
import unittest
from unittest import mock

from mcuctl import firmware


def _ns(device):
    return argparse.Namespace(esptool=None, device=device)


class WslEsptool(unittest.TestCase):
    def test_a_com_port_runs_windows_esptool(self):
        with mock.patch.object(firmware, "HOST", "wsl"):
            self.assertTrue(firmware._windows_esptool("COM7"))
            self.assertEqual(firmware._esptool_cmd(_ns("COM7"))[-2:], ["-m", "esptool"])
            self.assertTrue(firmware._esptool_cmd(_ns("COM7"))[0].endswith("python.exe"))

    def test_a_usbipd_tty_runs_linux_esptool(self):
        with mock.patch.object(firmware, "HOST", "wsl"):
            self.assertFalse(firmware._windows_esptool("/dev/ttyUSB0"))
            self.assertFalse(firmware._esptool_cmd(_ns("/dev/ttyUSB0"))[0].endswith("python.exe"))

    def test_off_wsl_nothing_is_windows(self):
        with mock.patch.object(firmware, "HOST", "linux"):
            self.assertFalse(firmware._windows_esptool("COM7"))


if __name__ == "__main__":
    unittest.main()
