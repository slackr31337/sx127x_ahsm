#!/usr/bin/env python3
"""
Copyright 2017 Dean Hall.  See LICENSE for details.

Physical Layer State Machine for SX127x device
- models desired SX127x device behavior
- SX127x device control via SPI bus
- establishes Transmit and Receive sequences
- responds to a handful of events (expected from Layer 2 (MAC))
- responds to GPIO events from the SX127x chip's DIOx pins
"""

import logging
import time

import farc

from . import phy_sx127x_spi


class SX127xSpiAhsm(farc.Ahsm):
    # Maximum amount of time to perform blocking sleep (seconds).
    # If a sleep time longer than this is requested,
    # the sleep time becomes this value.
    MAX_BLOCKING_TIME = 0.050 # secs

    # Margin time added to transmit time
    # to allow other nodes to enable their receiver
    TX_MARGIN = 0.005 # secs


    def __init__(self, spi_stngs, dflt_modem_stngs):
        super().__init__()
        self.spi_stngs = spi_stngs
        self.dflt_modem_stngs = dflt_modem_stngs


    @farc.Hsm.state
    def _initial(me, event):
        """Pseudostate: SX127xSpiAhsm:_initial
        """
        # self-signaling
        farc.Signal.register("_ALWAYS")

        # Outgoing
        farc.Signal.register("PHY_RXD_DATA")
        farc.Signal.register("PHY_TX_DONE")

        # Incoming
        farc.Signal.register("PHY_STDBY")
        farc.Signal.register("PHY_SET_LORA")

        # Incoming from higher layer
        farc.Framework.subscribe("PHY_SLEEP", me)
        farc.Framework.subscribe("PHY_CAD", me)
        farc.Framework.subscribe("PHY_RECEIVE", me)
        farc.Framework.subscribe("PHY_TRANSMIT", me)

        # Incoming from GPIO (SX127x's DIO pins)
        farc.Framework.subscribe("PHY_DIO0", me)
        farc.Framework.subscribe("PHY_DIO1", me)
        farc.Framework.subscribe("PHY_DIO2", me)
        farc.Framework.subscribe("PHY_DIO3", me)
        farc.Framework.subscribe("PHY_DIO4", me)
        farc.Framework.subscribe("PHY_DIO5", me)

        # A time event used for setting timeouts
        me.tm_evt = farc.TimeEvent("_PHY_SPI_TMOUT")

        return me.tran(me, SX127xSpiAhsm._initializing)


    @farc.Hsm.state
    def _initializing(me, event):
        """State: SX127xSpiAhsm:_initializing
        Opens, verifies and inits the SPI driver.
        Transitions to the _idling state if all is good;
        otherwise transitions to the _exiting state
        so the SPI driver is closed.
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:
            me.sx127x = phy_sx127x_spi.SX127xSpi(me.spi_stngs)
            me.tm_evt.postIn(me, 0.0)
            return me.handled(me, event)

        elif sig == farc.Signal._PHY_SPI_TMOUT:
            if me.sx127x.check_chip_ver():
                me.sx127x.init(me.dflt_modem_stngs)
                me.sx127x.set_pwr_cfg(boost=True)
                return me.tran(me, SX127xSpiAhsm._idling)

            logging.info("_initializing: no SX127x or SPI")
            me.tm_evt.postIn(me, 1.0)
            return me.handled(me, event)

        elif sig == farc.Signal.EXIT:
            me.tm_evt.disarm()
            return me.handled(me, event)

        return me.super(me, me.top)


    @farc.Hsm.state
    def _idling(me, event):
        """State: SX127xSpiAhsm:_idling
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:
            return me.handled(me, event)

        elif sig == farc.Signal.PHY_SLEEP:
            return me.tran(me, me.sleeping)

        elif sig == farc.Signal.PHY_SET_LORA:
            # TODO
            return me.handled(me, event)

        elif sig == farc.Signal.PHY_RECEIVE:
            me.rx_time = event.value[0]
            me.rx_freq = event.value[1]
            return me.tran(me, me._rx_prepping)

        elif sig == farc.Signal.PHY_TRANSMIT:
            me.tx_time = event.value[0]
            me.tx_freq = event.value[1]
            me.tx_data = event.value[2]
            return me.tran(me, me._tx_prepping)

        elif sig == farc.Signal.PHY_CAD:
            return me.tran(me, me.cad_ing)

        return me.super(me, me.top)


    @farc.Hsm.state
    def _working(me, event):
        """State SX127xSpiAhsm:_working
        This state provides a PHY_STDBY handler that returns the radio to stdby.
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:
            return me.handled(me, event)

        elif sig == farc.Signal.PHY_STDBY:
            me.sx127x.set_op_mode("stdby")
            return me.tran(me, me._idling)

        return me.super(me, me.top)


#### Receive chain
    @farc.Hsm.state
    def _rx_prepping(me, event):
        """State: SX127xSpiAhsm:_idling:_rx_prepping
        While still in radio's standby mode, get regs and FIFO ready for RX.
        If a positive rx_time is given, sleep (blocking) for a tiny amount.
        If rx_time is zero or less, receive immediately.
        Always transfer to the Receiving state.
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:

            # Enable only the RX interrupts (disable all others)
            me.sx127x.disable_lora_irqs()
            me.sx127x.enable_lora_irqs(phy_sx127x_spi.IRQFLAGS_RXTIMEOUT_MASK
                | phy_sx127x_spi.IRQFLAGS_RXDONE_MASK
                | phy_sx127x_spi.IRQFLAGS_PAYLOADCRCERROR_MASK
                | phy_sx127x_spi.IRQFLAGS_VALIDHEADER_MASK)

            # Prepare DIO0,1 to cause RxDone, RxTimeout, ValidHeader interrupts
            me.sx127x.set_dio_mapping(dio0=0, dio1=0, dio3=1)
            me.sx127x.set_lora_rx_fifo(me.dflt_modem_stngs["modulation_stngs"]["rx_base_ptr"])
            me.sx127x.set_lora_rx_freq(me.rx_freq)

            # Reminder pattern
            me.postFIFO(farc.Event(farc.Signal._ALWAYS, None))
            return me.handled(me, event)

        elif sig == farc.Signal._ALWAYS:
            if me.rx_time > 0:
                tiny_sleep = me.rx_time - farc.Framework._event_loop.time()
                if 0.0 < tiny_sleep < SX127xSpiAhsm.MAX_BLOCKING_TIME:
                    time.sleep(tiny_sleep)
            return me.tran(me, SX127xSpiAhsm._listening)

        return me.super(me, me._idling)


    @farc.Hsm.state
    def _listening(me, event):
        """State SX127xSpiAhsm:_working:_listening
        If the rx_time is less than zero, listen continuously;
        the caller must establish a way to end the continuous mode.
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:
            me.hdr_time = 0
            if me.rx_time < 0:
                me.sx127x.set_op_mode("rxcont")
            else:
                me.sx127x.set_op_mode("rxonce")
            return me.handled(me, event)

        elif sig == farc.Signal.PHY_DIO0: # RX_DONE
            if me.sx127x.check_lora_rx_flags():
                payld, rssi, snr = me.sx127x.get_lora_rxd()
                pkt_data = (me.hdr_time, payld, rssi, snr)
                farc.Framework.publish(farc.Event(farc.Signal.PHY_RXD_DATA, pkt_data))
            else:
                # TODO: crc error stats
                logging.info("rx CRC error")

            return me.tran(me, SX127xSpiAhsm._idling)

        elif sig == farc.Signal.PHY_DIO1: # RX_TIMEOUT
            me.sx127x.clear_lora_irqs(phy_sx127x_spi.IRQFLAGS_RXTIMEOUT_MASK)
            return me.tran(me, SX127xSpiAhsm._idling)

        elif sig == farc.Signal.PHY_DIO3: # ValidHeader
            me.hdr_time = event.value
            me.sx127x.clear_lora_irqs(phy_sx127x_spi.IRQFLAGS_VALIDHEADER_MASK)
            return me.tran(me, SX127xSpiAhsm._receiving)

        # We haven't received anything yet
        # and a request to Transmit arrives,
        # cancel the listening and go do the Transmit
        elif sig == farc.Signal.PHY_TRANSMIT:
            me.sx127x.set_op_mode("stdby")
            me.tx_time = event.value[0]
            me.tx_freq = event.value[1]
            me.tx_data = event.value[2]
            return me.tran(me, me._tx_prepping)

        return me.super(me, me._working)


    @farc.Hsm.state
    def _receiving(me, event):
        """State SX127xSpiAhsm:_working:_listening:_receiving
        NOTE: This state should not be confused with
        the MAC layer state of the same name
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:
            pass

        elif sig == farc.Signal.PHY_TRANSMIT:
            # TODO: put the pkt in the tx queue
            pass

        return me.super(me, me._listening)


#### Transmit chain
    @farc.Hsm.state
    def _tx_prepping(me, event):
        """State: SX127xSpiAhsm:_idling:_tx_prepping
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:

            # Enable only the TX interrupts (disable all others)
            me.sx127x.disable_lora_irqs()
            me.sx127x.enable_lora_irqs(phy_sx127x_spi.IRQFLAGS_TXDONE_MASK)
            me.sx127x.clear_lora_irqs(phy_sx127x_spi.IRQFLAGS_TXDONE_MASK)

            # Set DIO, TX/FIFO_PTR, FIFO and freq in prep for transmit
            me.sx127x.set_dio_mapping(dio0=1)
            me.sx127x.set_lora_fifo_ptr()
            me.sx127x.set_tx_data(me.tx_data)
            me.sx127x.set_tx_freq(me.tx_freq)

            # Reminder pattern
            me.postFIFO(farc.Event(farc.Signal._ALWAYS, None))
            return me.handled(me, event)

        elif sig == farc.Signal._ALWAYS:
            if me.tx_time > 0:
                # Calculate precise sleep time and apply a TX margin
                # to allow receivers time to get ready
                tiny_sleep = me.tx_time - farc.Framework._event_loop.time()
                tiny_sleep += SX127xSpiAhsm.TX_MARGIN

                # If TX time has passed, don't sleep
                # Else use sleep to get ~1ms precision
                # Cap sleep at 50ms so we don't block for too long
                if 0.0 < tiny_sleep: # because MAC layer uses 40ms PREP time
                    if tiny_sleep > SX127xSpiAhsm.MAX_BLOCKING_TIME:
                        tiny_sleep = SX127xSpiAhsm.MAX_BLOCKING_TIME
                    time.sleep(tiny_sleep)
            return me.tran(me, SX127xSpiAhsm._transmitting)

        return me.super(me, me._idling)


    @farc.Hsm.state
    def _transmitting(me, event):
        """State: SX127xSpiAhsm:_working:_transmitting
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:
            logging.info("tx             %f", farc.Framework._event_loop.time())
            me.sx127x.set_op_mode("tx")
            me.tm_evt.postIn(me, 1.0) # TODO: make time scale with datarate
            return me.handled(me, event)

        elif sig == farc.Signal.PHY_DIO0: # TX_DONE
            return me.tran(me, SX127xSpiAhsm._idling)

        elif sig == farc.Signal._PHY_SPI_TMOUT: # software timeout
            me.sx127x.set_op_mode("stdby")
            return me.tran(me, SX127xSpiAhsm._idling)

        elif sig == farc.Signal.EXIT:
            me.tm_evt.disarm()
            farc.Framework.publish(farc.Event(farc.Signal.PHY_TX_DONE, None))
            return me.handled(me, event)

        return me.super(me, me._working)


    @farc.Hsm.state
    def _exiting(me, event):
        """State: SX127xSpiAhsm:_exiting
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:
            logging.info("_exiting")
            me.sx127x.close()
            return me.handled(me, event)

        return me.super(me, me.top)
