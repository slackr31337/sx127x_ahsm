#!/usr/bin/env python3
"""
Copyright 2017 Dean Hall.  See LICENSE for details.

Physical Layer State Machine for GPIO operations on the RasPi device
- detects GPIO input pin changes
- publishes events (with timestamp) when these pin changes occur

NOTE: Uses GPIO.BCM pin numbering.
"""


import farc

try:
    import RPi.GPIO as GPIO
except:
    from . import mock_gpio as GPIO


# The RPi.GPIO module responds to external I/O in a separate thread.
# State machine processing should not happen in that thread.
# So in the following GPIO handler, we publish a unique event for each GPIO.
# The separate thread will publish the event and exit quickly
# back to the main thread; the event will be processed there.
def _gpio_input_handler(sig):
    """Emits the given signal upon a pin change.
    The event's value is the current time.
    """
    time = farc.Framework._event_loop.time()
    evt = farc.Event(sig, time)
    farc.Framework.publish(evt)


class GpioAhsm(farc.Ahsm):
    def __init__(self,):
        super().__init__()
        GPIO.setmode(GPIO.BCM)
        self._out_pins = []


    @farc.Hsm.state
    def _initial(me, event):
        """Pseudostate: GpioAhsm:_initial
        """
        return me.tran(me, GpioAhsm._running)


    @farc.Hsm.state
    def _running(me, event):
        """State: GpioAhsm:_running
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:
            return me.handled(me, event)

        elif sig == farc.Signal.SIGTERM:
            return me.tran(me, me._exiting)

        return me.super(me, me.top)


    @farc.Hsm.state
    def _exiting(me, event):
        """State: GpioAhsm:_exiting
        For each pin registered as an output,
        sets the pin to an input (a safe condition).
        """
        sig = event.signal
        if sig == farc.Signal.ENTRY:
            for pin_nmbr in self._out_pins:
                GPIO.setup(pin_nmbr, GPIO.IN)
            GPIO.cleanup()
            return me.handled(me, event)

        return me.super(me, me.top)


    def register_pin_in(self, pin_nmbr, pin_edge, sig_name):
        """Registers a signal to be published when the input pin edge is detected.
        """
        sig_num = farc.Signal.register(sig_name)
        GPIO.setup(pin_nmbr, GPIO.IN)
        GPIO.add_event_detect(pin_nmbr, edge=pin_edge, callback=lambda x: _gpio_input_handler(sig_num))


    def register_pin_out(self, pin_nmbr, pin_initial):
        """Registers an output pin to be set with an initial value.
        """
        GPIO.setup(pin_nmbr, GPIO.OUT, initial=pin_initial)
        self._out_pins.append(pin_nmbr)
