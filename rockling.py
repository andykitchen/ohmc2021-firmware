#!/usr/bin/env python3
# This variable defines all the external programs that this module
# relies on.  lxbuildenv reads this variable in order to ensure
# the build will finish without exiting due to missing third-party
# programs.
LX_DEPENDENCIES = ["riscv", "icestorm", "yosys", "nextpnr-ice40"]

# Import lxbuildenv to integrate the deps/ directory
import lxbuildenv

# Disable pylint's E1101, which breaks completely on migen
#pylint:disable=E1101

from migen import Module, Signal, Instance, ClockDomain, If
from migen.fhdl.specials import TSTriple
from migen.fhdl.decorators import ClockDomainsRenamer
from migen.genlib.cdc import AsyncResetSynchronizer

from litex.build.lattice.platform import LatticePlatform
from litex.build.generic_platform import Pins, Subsignal
from litex.soc.integration.doc import AutoDoc, ModuleDoc
from litex.soc.integration.soc_core import SoCCore
from litex.soc.cores.cpu import CPUNone
from litex.soc.integration.builder import Builder
from litex.soc.interconnect import wishbone

from litex.soc.cores import spi_flash
from litex.soc.cores.ram.lattice_ice40 import Up5kSPRAM

from valentyusb.usbcore import io as usbio
from valentyusb.usbcore.cpu import epmem, unififo, epfifo, dummyusb, eptri
from valentyusb.usbcore.endpoint import EndpointType

import litex.soc.doc as lxsocdoc

import argparse
import os

#from rtl.version import Version
#from rtl.romgen import RandomFirmwareROM, FirmwareROM
#from rtl.fomutouch import TouchPads
#from rtl.sbled import SBLED
from rtl.sbwarmboot import SBWarmBoot
#from rtl.messible import Messible


class Platform(LatticePlatform):
    def __init__(self, revision=None, toolchain="icestorm"):
        self.revision = revision
        if revision == "evt":
            from litex_boards.platforms.fomu_evt import _io, _connectors
            LatticePlatform.__init__(self, "ice40-up5k-sg48", _io, _connectors, toolchain="icestorm")
            self.spi_size = 16 * 1024 * 1024
            self.spi_dummy = 6
        elif revision == "dvt":
            from litex_boards.platforms.fomu_pvt import _io, _connectors
            LatticePlatform.__init__(self, "ice40-up5k-uwg30", _io, _connectors, toolchain="icestorm")
            self.spi_size = 2 * 1024 * 1024
            self.spi_dummy = 6
        elif revision == "pvt":
            from litex_boards.platforms.fomu_pvt import _io, _connectors
            LatticePlatform.__init__(self, "ice40-up5k-uwg30", _io, _connectors, toolchain="icestorm")
            self.spi_size = 2 * 1024 * 1024
            self.spi_dummy = 6
        elif revision == "hacker":
            from litex_boards.platforms.fomu_hacker import _io, _connectors
            LatticePlatform.__init__(self, "ice40-up5k-uwg30", _io, _connectors, toolchain="icestorm")
            self.spi_size = 2 * 1024 * 1024
            self.spi_dummy = 4
        else:
            raise ValueError("Unrecognized revision: {}.  Known values: evt, dvt, pvt, hacker".format(revision))

    def create_programmer(self):
        raise ValueError("programming is not supported")


class _CRG(Module, AutoDoc):
    """Fomu Clock Resource Generator

    Fomu is a USB device, which means it must have a 12 MHz clock.  Valentyusb
    oversamples the clock by 4x, which drives the requirement for a 48 MHz clock.
    The ICE40UP5k is a relatively low speed grade of FPGA that is incapable of
    running the entire design at 48 MHz, so the majority of the logic is placed
    in the 12 MHz domain while only critical USB logic is placed in the fast
    48 MHz domain.

    Fomu has a 48 MHz crystal on it, which provides the raw clock input.  This
    signal is fed through the ICE40 PLL in order to divide it down into a 12 MHz
    signal and keep the clocks within 1ns of phase.  Earlier designs used a simple
    flop, however this proved unreliable when the FPGA became very full.

    The following clock domains are available on this design:

    +---------+------------+---------------------------------+
    | Name    | Frequency  | Description                     |
    +=========+============+=================================+
    | usb_48  | 48 MHz     | Raw USB signals and pulse logic |
    +---------+------------+---------------------------------+
    | usb_12  | 12 MHz     | USB control logic               |
    +---------+------------+---------------------------------+
    | sys     | 12 MHz     | System CPU and wishbone bus     |
    +---------+------------+---------------------------------+
    """
    def __init__(self, platform):
        clk48_raw = platform.request("clk48")
        clk12 = Signal()

        reset_delay = Signal(12, reset=4095)
        self.clock_domains.cd_por = ClockDomain()
        self.reset = Signal()

        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_usb_12 = ClockDomain()
        self.clock_domains.cd_usb_48 = ClockDomain()

        platform.add_period_constraint(self.cd_usb_48.clk, 1e9/48e6)
        platform.add_period_constraint(self.cd_sys.clk, 1e9/12e6)
        platform.add_period_constraint(self.cd_usb_12.clk, 1e9/12e6)
        platform.add_period_constraint(clk48_raw, 1e9/48e6)

        # POR reset logic- POR generated from sys clk, POR logic feeds sys clk
        # reset.
        self.comb += [
            self.cd_por.clk.eq(self.cd_sys.clk),
            self.cd_sys.rst.eq(reset_delay != 0),
            self.cd_usb_12.rst.eq(reset_delay != 0),
        ]

        # POR reset logic- POR generated from sys clk, POR logic feeds sys clk
        # reset.
        self.comb += [
            self.cd_usb_48.rst.eq(reset_delay != 0),
        ]

        self.comb += self.cd_usb_48.clk.eq(clk48_raw)

        self.specials += Instance(
            "SB_PLL40_CORE",
            # Parameters
            p_DIVR = 0,
            p_DIVF = 15,
            p_DIVQ = 5,
            p_FILTER_RANGE = 1,
            p_FEEDBACK_PATH = "SIMPLE",
            p_DELAY_ADJUSTMENT_MODE_FEEDBACK = "FIXED",
            p_FDA_FEEDBACK = 15,
            p_DELAY_ADJUSTMENT_MODE_RELATIVE = "FIXED",
            p_FDA_RELATIVE = 0,
            p_SHIFTREG_DIV_MODE = 1,
            p_PLLOUT_SELECT = "GENCLK_HALF",
            p_ENABLE_ICEGATE = 0,
            # IO
            i_REFERENCECLK = clk48_raw,
            o_PLLOUTCORE = clk12,
            # o_PLLOUTGLOBAL = clk12,
            #i_EXTFEEDBACK,
            #i_DYNAMICDELAY,
            #o_LOCK,
            i_BYPASS = 0,
            i_RESETB = 1,
            #i_LATCHINPUTVALUE,
            #o_SDO,
            #i_SDI,
        )

        self.comb += self.cd_sys.clk.eq(clk12)
        self.comb += self.cd_usb_12.clk.eq(clk12)

        self.sync.por += \
            If(reset_delay != 0,
                reset_delay.eq(reset_delay - 1)
            )
        self.specials += AsyncResetSynchronizer(self.cd_por, self.reset)


class BaseSoC(SoCCore, AutoDoc):
    def __init__(self, platform, pnr_seed=0xFADE, usb_debug=True, **kwargs):
        clk_freq = int(12e6)
        self.submodules.crg = _CRG(platform)

        SoCCore.__init__(self, platform, clk_freq, with_uart=False, csr_data_width=32, **kwargs)

        usb_pads = platform.request("usb")
        usb_iobuf = usbio.IoBuf(usb_pads.d_p, usb_pads.d_n, usb_pads.pullup)

        self.submodules.usb = dummyusb.DummyUsb(usb_iobuf, debug=usb_debug, relax_timing=True, product="Rockling Theremin Debug", manufacturer="OHMC2022")

        if usb_debug:
            self.add_wb_master(self.usb.debug_bridge.wishbone)

        self.submodules.reboot = SBWarmBoot(self, offsets=None)

        # Override default LiteX's yosys/build templates
        assert hasattr(platform.toolchain, "yosys_template")
        assert hasattr(platform.toolchain, "build_template")
        platform.toolchain.yosys_template = [
            "{read_files}",
            "attrmap -tocase keep -imap keep=\"true\" keep=1 -imap keep=\"false\" keep=0 -remove keep=0",
            "synth_ice40 -json {build_name}.json -top {build_name}",
        ]
        platform.toolchain.build_template = [
            "yosys -q -l {build_name}.rpt {build_name}.ys",
            "nextpnr-ice40 --json {build_name}.json --pcf {build_name}.pcf --asc {build_name}.txt \
            --pre-pack {build_name}_pre_pack.py --{architecture} --package {package}",
            "icepack {build_name}.txt {build_name}.bin"
        ]

        # Add "-relut -dffe_min_ce_use 4" to the synth_ice40 command.
        # The "-reult" adds an additional LUT pass to pack more stuff in,
        # and the "-dffe_min_ce_use 4" flag prevents Yosys from generating a
        # Clock Enable signal for a LUT that has fewer than 4 flip-flops.
        # This increases density, and lets us use the FPGA more efficiently.
        platform.toolchain.yosys_template[2] += " -relut -abc2 -dffe_min_ce_use 4 -relut"

        # Disable final deep-sleep power down so firmware words are loaded
        # onto softcore's address bus.
        platform.toolchain.build_template[2] = "icepack -s {build_name}.txt {build_name}.bin"

        # Allow us to set the nextpnr seed
        platform.toolchain.build_template[1] += " --seed " + str(pnr_seed)

        placer = "heap"
        platform.toolchain.build_template[1] += " --placer {}".format(placer)


def main():
    parser = argparse.ArgumentParser("Build Rockling Gateware")
    parser.add_argument("--seed", default=0xFADE, type=int, help="seed to use in nextpnr")
    args = parser.parse_args()

    platform = Platform(revision="hacker")
    soc = BaseSoC(platform, pnr_seed=args.seed, cpu_type=None, usb_debug=True)
    builder = Builder(soc)
    soc.do_exit(builder.build())


if __name__ == "__main__":
    main()