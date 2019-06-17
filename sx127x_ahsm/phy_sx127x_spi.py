#!/usr/bin/env python3
"""
Copyright 2016 Dean Hall.  See LICENSE for details.
"""


import collections, logging, time

try:
    import spidev
except:
    from . import mock_spidev as spidev

from . import phy_sx127x_stngs


SPI_CLK_MAX = 20000000
OSC_FREQ = 32e6
INV_OSC_FREQ = 1.0 / OSC_FREQ

# The SX127x's Version register value
CHIP_VERSION = 18

# Set the MSb of the register address to write to it
WRITE_BIT = 0x80

# Radio register addresses
REG_FIFO = 0x00
REG_OP_MODE = 0x01
REG_CARRIER_FREQ = 0x06
REG_PA_CFG = 0x09
REG_FIFO_PTR = 0x0D
REG_FIFO_TX_BASE_PTR = 0x0E
REG_FIFO_RX_BASE_PTR = 0x0F
REG_RX_CURRENT_ADDR = 0x10
REG_IRQ_MASK = 0x11
REG_IRQ_FLAGS = 0x12
REG_RX_NUM_BYTES = 0x13
REG_RX_HDR_CNT = 0x14
REG_PKT_SNR = 0x19
REG_PKT_RSSI = 0x1A
REG_MODEM_CFG_1 = 0x1D
REG_MODEM_CFG_2 = 0x1E
REG_PREAMBLE_LEN = 0x20 # MSB (0x21 is LSB)
REG_PAYLD_LEN = 0x22
REG_PAYLD_MAX = 0x23
REG_RX_BYTE_ADDR=0x25
REG_MODEM_CFG_3 = 0x26
REG_TEMP = 0x3C
REG_SYNC_WORD = 0x39
REG_DIO_MAPPING1 = 0x40
REG_DIO_MAPPING2 = 0x41
REG_VERSION = 0x42

# REG_IRQ_FLAGS bit definitions
IRQFLAGS_RXTIMEOUT_MASK          = 0x80
IRQFLAGS_RXDONE_MASK             = 0x40
IRQFLAGS_PAYLOADCRCERROR_MASK    = 0x20
IRQFLAGS_VALIDHEADER_MASK        = 0x10
IRQFLAGS_TXDONE_MASK             = 0x08
IRQFLAGS_CADDONE_MASK            = 0x04
IRQFLAGS_FHSSCHANGEDCHANNEL_MASK = 0x02
IRQFLAGS_CADDETECTED_MASK        = 0x01


class SX127xSpi(object):
    """Offers methods that drive the SPI bus to control the Semtech SX127x.
    """

    def __init__(self, spi_port=0, spi_cs=0, spi_clk_max=SPI_CLK_MAX, max_pkt_size=256):
        """Initializes and configures the SPI peripheral
        with the given bus and chip select.
        The default values are 0,0 for (SPI0, CS0) which is the convention
        for the first SX127x device.  0,1 (SPI0, CS1) is the convention for
        a second device.

        The SX127x has a physical maximum packet size of 256 bytes.
        This constructor allows you to set max_pkt_size to 256 or 128.
        The actual packet size may be smaller than or equal to max_pkt_size.
        The reason to set max_pkt_size to 128 is if you want to divide
        the radio's 256-byte FIFO into non-overlapping regions for Tx and Rx
        for implementation reasons.
        """
        # Validate arguments, open and configure SPI peripheral
        assert spi_port in (0,1)
        assert spi_cs in (0,1)

        self.spi = spidev.SpiDev()
        self.spi.open(spi_port, spi_cs)
        self.spi.max_speed_hz = spi_clk_max
        self.spi.mode = 0 # phase=0 and polarity=0

        # Use max packet size to set FIFO base pointers
        assert max_pkt_size in (128,256), "Packet size must be full (256) or half (128)"
        self.max_pkt_size = max_pkt_size
        if max_pkt_size == 128:
            self._tx_base_ptr = 0x80
        else:
            self._tx_base_ptr = 0


    def __del__(self,):
        self.spi.close()


## SPI helper methods

    def _read(self, reg_addr, nbytes=1):
        """Reads a byte (or more) from the register.
        Returns list of bytes (even if there is only one).
        """
        assert type(nbytes) is int
        assert nbytes > 0
        b = [reg_addr,]
        b.extend([0,] * nbytes)
        return self.spi.xfer2(b)[1:]


    def _write(self, reg_addr, data):
        """Writes one or more bytes to the register.
        Returns list of bytes (even if there is only one).
        """
        assert type(data) == int or isinstance(data, collections.Sequence)

        # Set the write bit (MSb)
        reg_addr |= WRITE_BIT

        # Build the list of bytes to write
        if type(data) == int:
            data &= 0xff
            b = [reg_addr, data]
        else:
            b = [reg_addr,]
            b.extend(data)

        return self.spi.xfer2(b)[1:]


## SX127x general methods

    def check_chip_ver(self,):
        """Returns True if the Semtech SX127x returns the proper value
        from the Version register.  This proves the chip and the SPI bus
        are operating.
        """
        ver = self._read(REG_VERSION)[0]
        if ver == CHIP_VERSION:
            logging.info("SPI to SX127x: PASS")
            return True
        else:
            logging.info("SPI to SX127x: FAIL (version : %d)" % ver)
            return False


    def get_dio(self,):
        """Reads the current DIO mapping from the device and
        stores it so we can modify individual DIOs later.
        Returns nothing.
        """
        map1, map2 = self._read(REG_DIO_MAPPING1, 2)
        dio = bytearray()
        dio.append((map1 >> 6) & 0b11) # DIO0
        dio.append((map1 >> 4) & 0b11) # DIO1
        dio.append((map1 >> 2) & 0b11) # DIO2
        dio.append((map1 >> 0) & 0b11) # DIO3
        dio.append((map2 >> 6) & 0b11) # DIO4
        dio.append((map2 >> 4) & 0b11) # DIO5
        self.dio_mapping = dio


    def get_op_mode(self,):
        """Gets the device mode field of the Op Mode register
        and returns a string representation of the mode.
        """
        mode_lut = ("sleep", "stdby", "fstx", "tx", "fsrx", "rxcont", "rx", "cad")
        d = self._read(REG_OP_MODE)
        self.mode = mode_lut[d[0] & 0b111]
        return self.mode


    def get_regs(self,):
        """Reads in all registers from the SX127x.
        This function is meant to be used at startup
        to gather the state of the SX127x.
        """
        pass


    def set_dio_mapping(self, **dio_args):
        """Writes the DIO mapping registers.
        dio_args is a kwarg of the form {dio<x>=<int>, ...}
        where x is a value 0..5
        and <int> is an integer in the range 0..3
        """
        # If DIO mapping has not been read from device
        # create a mapping of all zeros
        if not hasattr(self, "dio_mapping"):
            self.dio_mapping = [0,] * 6

        dio_seq = self.dio_mapping

        # put any kwargs into the sequence
        for k,v in dio_args.items():
            assert k.startswith("dio"), "dio_args has a bad key"
            dio_int = int(k[-1])
            assert dio_int in (0,1,2,3,4,5), "dio_args key out of range"
            assert v in (0,1,2,3), "dio_args has a bad value"
            dio_seq[dio_int] = v

        # build the register values from the sequence
        map_reg1 = (dio_seq[0] & 0x03) << 6 \
                 | (dio_seq[1] & 0x03) << 4 \
                 | (dio_seq[2] & 0x03) << 2 \
                 | (dio_seq[3] & 0x03)
        map_reg2 = (dio_seq[4] & 0x03) << 6 \
                 | (dio_seq[5] & 0x03) << 4
        self._write(REG_DIO_MAPPING1, [map_reg1, map_reg2])


    def set_modem(self, modem):
        """Enters sleep mode and sets the modem to use.
        Modem is one of: "lora", "fsk" or "ook"
        """
        assert modem in ("lora", "fsk", "ook")

        self.set_op_mode("sleep")
        d = self._read(REG_OP_MODE)
        d &= 0b00011111
        if modem == "lora":
            d |= 0b10000000
        elif modem == "ook":
            d |= 0b00100000
        # if modem == "fsk" is three 0s (nothing to do)
        self._write(REG_OP_MODE, d)
        self.set_op_mode("stdby")


## SX127x RF block methods

    def get_rf_freq(self,):
        """Reads the frequency registers
        and returns the calculated frequency.
        WARNING: The frequency registers will contain an offset
        if the radio's last operation was receive
        (but some bandwidths have 0.0 offset).
        """
        hi,med,low = self._read(REG_CARRIER_FREQ, 3)
        val = hi << 16 | med << 8 | low
        return int(round(val * OSC_FREQ / 2**19))


    def set_rf(self, **rf_stngs):
        """Sets the RF parameters of the SX127x.
        rf_stngs is one or more of the following parameters:
            "freq", "pa_sel", "max_pwr", "out_pwr", "ocp_on", "ocp_trim",
            "lna_gain", "lna_boost_lf", "lna_boost_hf"
        These parameters correspond to fieds in regs 0x01 .. 0x0C.
        """
        keys = rf_stngs.keys()
        for key in keys:
            assert key in ("freq", "pa_sel", "max_pwr",
                           "out_pwr", "ocp_on", "ocp_trim",
                           "lna_gain", "lna_boost_lf", "lna_boost_hf")

        regs = self._read(REG_OP_MODE, 9)
        # TODO: iterate through settings, rmw regs


    def set_pwr_cfg(self, pwr=0xf, max=0x4, boost=True):
        """Sets the power, max power and use-pa-boost fields of the
        PA_CONFIG register (0x09).
        The use-pa-boost field selects the chip pin to output the RF signal.
        boost=True selects PA_BOOST as the output; whereas, False selects RFO.
        Most modules connect PA_BOOST to the antenna output, but a few use RFO.
        https://github.com/PaulStoffregen/RadioHead/blob/master/RH_RF95.h#L649
        """
        r = pwr & 0xF | (max & 0x7) << 4
        if boost:
            r |= 0b10000000
        else:
            r &= ~0b10000000
        self._write(REG_PA_CFG, r)



## LoRa modem methods

    def clear_lora_counts(self,):
        """Clears the valid header count and valid packet count regs.
        """
        self._write(REG_RX_HDR_CNT, [0,0,0,0])


    def clear_lora_irqs(self, irq_bits=None):
        """Clears interrupt flags.
        If an argument is given, it is a byte with a bit set
        for each IRQ flag to clear.
        If no argument is given, all IRQ flags are cleared.
        """
        if irq_bits:
            d = irq_bits
        else:
            d = 0xFF
        self._write(REG_IRQ_FLAGS, d)


    def enable_lora_irqs(self, irq_bits=None):
        """Enables one or more IRQs.
        If an argument is given, it is a byte
        with a bit set for each IRQ to enable.
        IRQs are enabled by writing a zero
        to the bit in the mask register.
        """
        if irq_bits:
            d = ~irq_bits
        else:
            d = 0x00
        self._write(REG_IRQ_MASK, d)


    def disable_lora_irqs(self, irq_bits=None):
        """Disables one or more IRQs.
        If an argument is given, it is a byte
        with a bit set for each IRQ to disable.
        If no argument is given, all IRQs are disabled.
        IRQs are disabled by writing a one
        to the bit in the mask register.
        """
        if irq_bits:
            d = irq_bits
        else:
            d = 0xFF
        self._write(REG_IRQ_MASK, d)


    def check_lora_rx_flags(self,):
        """Checks post-receive status, clears rx-related IRQs.
        Returns True if a valid packet was received, else False.
        """
        # Get the IRQ flags
        flags = self._read(REG_IRQ_FLAGS)[0]

        # Clear rx-related IRQ flags in the reg
        flags &= ( IRQFLAGS_RXTIMEOUT_MASK
                 | IRQFLAGS_RXDONE_MASK
                 | IRQFLAGS_PAYLOADCRCERROR_MASK
                 | IRQFLAGS_VALIDHEADER_MASK )
        self._write(REG_IRQ_FLAGS, flags)

        result = bool(flags & IRQFLAGS_RXDONE_MASK)
        if flags & ( IRQFLAGS_RXTIMEOUT_MASK
                   | IRQFLAGS_PAYLOADCRCERROR_MASK):
            result = False
        return result


    def get_lora_rxd(self,):
        """Returns the most recently received payload and rf channel data.
        Assumes caller has already determined check_lora_rx_flags() is True.
        Returns a tuple of: (payld, rssi, snr)
        payld is a list of integers.
        rssi is an integer [dBm].
        snr is a float [dB].
        """
        # Get the index into the FIFO of where the pkt starts
        # and the length of the data received
        pkt_start, _, _, nbytes = self._read(REG_RX_CURRENT_ADDR, 4)

        # Error checking (that pkt started at 0)
#        if pkt_start != 0: "pkt_start was %d" % pkt_start # TODO: logging

        # Read the payload
        self._write(REG_FIFO_PTR, pkt_start)
        payld = self._read(REG_FIFO, nbytes)

        # Get the packet SNR and RSSI (2 consecutive regs)
        # and calculate RSSI [dBm] and SNR [dB]
        snr, rssi = self._read(REG_PKT_SNR, 2)
        rssi = -157 + rssi
        snr = snr / 4.0

        return (payld, rssi, snr)


    def get_lora_status(self,):
        """Gets status fields.
        Returns a dict of status fields.
        """
        d = self._read(REG_RX_HDR_CNT, 5)
        s = {}
        s["rx_hdr_cnt"] = d[0] << 8 | d[1]
        s["rx_pkt_cnt"] = d[2] << 8 | d[3]
        s["rx_code_rate"] = d[4] >> 5
        s["modem_clr"] = (d[4] & 0x10) != 0
        s["hdr_info_valid"] = (d[4] & 0x08) != 0
        s["rx_busy"] = (d[4] & 0x04) != 0
        s["sig_sync"] = (d[4] & 0x01) != 0
        s["sig_detected"] = (d[4] & 0x01) != 0
        return s


    def set_config(self, cfg):
        """Writes configuration values to the appropriate registers
        """
        assert isinstance(cfg, phy_sx127x_stngs.SX127xSettings)

        # Save cfg
        self.cfg = cfg

        self.bandwidth_idx = cfg.bandwidth_idx

        # Transition to sleep mode to write configuration
        mode_bkup = self.get_op_mode()
        if mode_bkup != "sleep":
            self.set_op_mode("sleep")

        # Concat bandwidth | code_rate | implicit header mode
        reg_cfg1 = cfg.bandwidth_idx << 4 \
            | cfg.code_rate_idx << 1 \
            | int(cfg.implct_hdr_mode)
        # Concat spread_factor | tx_cont | upper 2 bits of symbol count
        reg_cfg2 = cfg.spread_factor_idx << 4 \
            | int(cfg.tx_cont) << 3 \
            | int(cfg.en_crc) << 2 \
            | cfg.symbol_count >> 8
        # Lower 8 bits of symbol count go in reg(0x1F)
        reg_sym_to = cfg.symbol_count & 0xff
        # Write 3 contiguous regs at once
        self._write(REG_MODEM_CFG_1, [reg_cfg1, reg_cfg2, reg_sym_to])

        # Write preamble register
        reg_preamble_len = [cfg.preamble_len >> 8, cfg.preamble_len & 0xff]
        self._write(REG_PREAMBLE_LEN, reg_preamble_len)

        # Write Cfg3 reg
        reg_cfg3 = int(cfg.en_ldr) << 3 | int(cfg.agc_auto) << 2
        self._write(REG_MODEM_CFG_3, reg_cfg3)

        # Write Sync word
        self._write(REG_SYNC_WORD, cfg.sync_word)

        # Restore previous operating mode
        if mode_bkup != "sleep":
            self.set_op_mode(mode_bkup)


    def set_fifo(self, data, offset=None):
        """Writes the data to the FIFO.
        Data is either an int or a sequence of bytes
        If the offset is given, data is written there;
        otherwise, data is written at the current FIFO pointer.
        """
        if offset is not None: #TODO: and modem is "lora"
            self.set_lora_fifo_ptr(offset)

        self._write(REG_FIFO, data)


    def set_lora_fifo_ptr(self, offset=0):
        """Sets the FIFO address pointer.
        """
        assert type(offset) == int
        assert 0 <= offset <= 255
        self._write(REG_FIFO_PTR, offset)


    def set_tx_data(self, data):
        """Sets the FIFO pointers, the transmit data
        and the payload length register
        in preparation for transmit.
        """
        self._write(REG_PAYLD_LEN, len(data))
        self._write(REG_FIFO_PTR, [self._tx_base_ptr, self._tx_base_ptr])
        self.set_fifo(data)


    def set_tx_freq(self, freq):
        """Sets the radio carrier frequency for transmit operation.
        This is isolated from the receive operation to allow
        a defined offset to improve packet rejection (Errata 2.3).
        """
        assert 137e6 < freq < 1020e6
        self._write_freq(freq)


    def _write_freq(self, f, offset=0.0):
        """Writes the given frequency (with any offset) to the registers.
        The offset is to improve Rx packet rejection (Errata 2.3).
        """
        freq = f + offset
        freq = int(round(freq * 2**19 * INV_OSC_FREQ))
        d = [(freq>>16) & 0xff, (freq>>8) & 0xff, freq&0xff]
        self._write(REG_CARRIER_FREQ, d)


    def set_op_mode(self, mode="stdby"):
        """Sets the device mode in the operating mode register to one of
        these strings: sleep, stdby, fstx, tx, fsrx, rxcont, rx, cad
        """
        # validate mode argument
        mode_lut = {"sleep": 0b000,
                    "stdby": 0b001,
                    "standby": 0b001, # repeat for convenience
                    "fstx": 0b010,
                    "tx": 0b011,
                    "fsrx": 0b100,
                    "rxcont": 0b101,
                    "rx": 0b110, # same as rxonce
                    "rxonce": 0b110, # repeat for convenience
                    "cad": 0b111}
        mode_options = list(mode_lut.keys())
        mode_options.sort()
        assert mode in mode_options, "mode must be one of: " + str(mode_options)
        d = self._read(REG_OP_MODE)
        d &= 0b11111000
        d |= mode_lut[mode]
        self._write(REG_OP_MODE)


    def set_lora(self, **lora_stngs):
        """Applies the given LoRa register settings.
        Settings is one or more of the following strings: TBD.
        Low-frequency mode is determined directly from the given frequency.
        """
        keys = lora_stngs.keys()
        for key in keys:
            assert key in () # TODO


    def set_lora_rx_fifo(self, offset=0):
        """Sets the RX base pointer and FIFO pointer
        to the given offset (defaults to zero).
        """
        self._write(REG_FIFO_RX_BASE_PTR, offset)
        self._write(REG_FIFO_PTR, offset)


    def set_lora_rx_freq(self, freq):
        """Sets the frequency register to achieve the desired freq.
        Implements Semtech ERRATA 2.3 for improved RX packet rejection.
        """
        # Save parameters for improved Rx packet rejection (Errata 2.3)
        rx_rejection_offset_lut = (7810.0, 10420.0, 15620.0, 20830.0, 31250.0, 41670.0, 0.0, 0.0, 0.0, 0.0)
        rx_offset = rx_rejection_offset_lut[self.bandwidth_idx]

        # ERRATA 2.3: offset rx freq
        self._write_freq(freq, rx_offset)

        # ERRATA 2.3: set bit 7 at 0x31 to the correct value
        r = self._read(0x31)[0]
        if self.bandwidth_idx == 0b1001:
            r |= 0b10000000
            self._write(0x31, r)
        else:
            r &= ~0b10000000
            self._write(0x31, r)

            # ERRATA 2.3 set values at 0x2F and 0x30
            val_2f_lut = (0x48, 0x44, 0x44, 0x44, 0x44, 0x44, 0x40, 0x40, 0x40,)
            self._write(0x2F, [val_2f_lut[self.bandwidth_idx], 0])


    def set_lora_rx_timeout(self, symbol_count):
        """Sets the RX symbol count to achieve the desired timeout.
        """
        # TODO argument should be time, then do math to get symbol count
        assert 4 <= symbol_count <= 1023
        r1, r2 = self._read(REG_MODEM_CFG_2, 2)
        r1 &= 0xFC
        r1 |= (symbol_count >> 8)
        r2 = (symbol_count & 0xFF)
        self._write(REG_MODEM_CFG_2, [r1,r2])


## FSK/OOK modem methods

    def get_fsk_temperature(self,):
        """Returns the temperature.
        """
        # TODO: See PDF p89 for procedure & calibration
        # TODO: find way to safely [re]store FSK access to be able to read temp
        t = self._read(REG_TEMP)
        # TODO: convert t to degrees C
        return t
