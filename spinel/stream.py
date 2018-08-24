#
#  Copyright (c) 2016-2017, The OpenThread Authors.
#  All rights reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""
Module providing a generic stream interface.
Also includes adapter implementations for serial, socket, and pipes.
"""

from __future__ import print_function

import sys
import logging
import os
import traceback

import subprocess
import socket
import serial
import struct
import threading
import time

import spinel.util
import spinel.config as CONFIG


class IStream(object):
    """ Abstract base class for a generic Stream Interface. """

    def read(self, size):
        """ Read an array of byte integers of the given size from the stream. """
        pass

    def write(self, data):
        """ Write the given packed data to the stream. """
        pass

    def close(self):
        """ Close the stream cleanly as needed. """
        pass


class StreamSerial(IStream):
    """ An IStream interface implementation for serial devices. """

    def __init__(self, dev, baudrate=115200):
        try:
            self.serial = serial.Serial(dev, baudrate)
        except:
            logging.error("Couldn't open " + dev)
            traceback.print_exc()

    def write(self, data):
        self.serial.write(data)
        if CONFIG.DEBUG_STREAM_TX:
            logging.debug("TX Raw: " + str(map(spinel.util.hexify_chr, data)))

    def read(self, size=1):
        pkt = self.serial.read(size)
        if CONFIG.DEBUG_STREAM_RX:
            logging.debug("RX Raw: " + str(map(spinel.util.hexify_chr, pkt)))
        return map(ord, pkt)[0]


class StreamSocket(IStream):
    """ An IStream interface implementation over an internet socket. """

    def __init__(self, hostname, port):
        # Open socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((hostname, port))

    def write(self, data):
        self.sock.send(data)
        if CONFIG.DEBUG_STREAM_TX:
            logging.debug("TX Raw: " + str(map(spinel.util.hexify_chr, data)))

    def read(self, size=1):
        pkt = self.sock.recv(size)
        if CONFIG.DEBUG_STREAM_RX:
            logging.debug("RX Raw: " + str(map(spinel.util.hexify_chr, pkt)))
        return map(ord, pkt)[0]


class StreamPipe(IStream):
    """ An IStream interface implementation to stdin/out of a piped process. """

    def __init__(self, filename):
        """ Create a stream object from a piped system call """
        try:
            self.pipe = subprocess.Popen(filename, shell=True,
                                         stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         stderr=sys.stdout.fileno())
        except:
            logging.error("Couldn't open " + filename)
            traceback.print_exc()

    def write(self, data):
        if CONFIG.DEBUG_STREAM_TX:
            logging.debug("TX Raw: (%d) %s",
                          len(data), spinel.util.hexify_bytes(data))
        self.pipe.stdin.write(data)

    def read(self, size=1):
        """ Blocking read on stream object """
        pkt = self.pipe.stdout.read(size)
        if CONFIG.DEBUG_STREAM_RX:
            logging.debug("RX Raw: " + str(map(spinel.util.hexify_chr, pkt)))
        return map(ord, pkt)[0]

    def close(self):
        if self.pipe:
            self.pipe.kill()
            self.pipe = None


class StreamVirtualTime(IStream):
    """ An IStream interface implementation to virtual time simulator. """

    BASE_PORT = 9000
    """ Base UDP port of POSIX simulation. """

    MAX_NODES = 34
    """ Max number of simulation nodes. """

    MAX_MESSAGE = 1536
    """ Max bytes of message. """

    PORT_OFFSET = int(os.getenv('PORT_OFFSET', "0"))
    """ Offset of simulations. """

    OT_SIM_EVENT_UART_INPUT = 2
    """ Event of UART data input for NCP. """

    OT_SIM_EVENT_UART_OUTPUT = 3
    """ Event of UART data output from NCP. """

    OT_SIM_EVENT_UART_ACK = 4
    """ Event of UART data is received by simulator. """

    OT_SIM_EVENT_PRECMD = 5
    """ Event of UART data is fully handled by me. """

    OT_SIM_EVENT_POSTCMD = 6
    """ Event of UART data is fully handled by me. """

    def __init__(self, filename):
        """ Create a stream object from a piped system call """
        try:
            self.pipe = subprocess.Popen('exec ' + filename, shell=True,
                                         stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         stderr=sys.stdout.fileno())
            self._ack = threading.Event()
            self._done = 0
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            node_id = int(filename.split(' ')[1])
            self.port = self.BASE_PORT * 2 +  (self.PORT_OFFSET * self.MAX_NODES) + node_id
            self.sock.bind(('127.0.0.1', self.port,))
            self.simulator_addr = ('127.0.0.1', self.BASE_PORT + (self.PORT_OFFSET * self.MAX_NODES))
            self.buffer = []
        except:
            logging.error("Couldn't open " + filename)
            traceback.print_exc()
            raise

    def write(self, data):
        """ Write the given packed data to the stream. """
        if CONFIG.DEBUG_STREAM_TX:
            logging.debug("TX Raw: (%d) %s",
                          len(data), spinel.util.hexify_bytes(data))

        self._ack.clear()
        message = struct.pack('=QBH', 0, self.OT_SIM_EVENT_UART_INPUT, len(data))
        message += data

        self._send_message(message)

        while not self._ack.wait(1):
            pass

    def _send_message(self, message):
        while True:
            try:
                sent = self.sock.sendto(message, self.simulator_addr)
            except socket.error:
                traceback.print_exc()
                time.sleep(0)
            else:
                break

        assert sent == len(message)

    def _next_event(self):
        message, addr = self.sock.recvfrom(self.MAX_MESSAGE)
        delay, type, datalen = struct.unpack('=QBH', message[:11])
        data = message[11:]

        if type is self.OT_SIM_EVENT_UART_ACK:
            self._ack.set()
        elif type == self.OT_SIM_EVENT_UART_OUTPUT:
            if CONFIG.DEBUG_STREAM_RX:
                logging.debug("RX Raw: " + data)

            self.buffer = map(ord, data)
        else:
            assert False

    def send_precmd(self):
        message = struct.pack('=QBH', 0, self.OT_SIM_EVENT_PRECMD, 0)
        self._send_message(message)

    def send_postcmd(self):
        """ Send event to notify that UART data is handled. """
        message = struct.pack('=QBH', 0, self.OT_SIM_EVENT_POSTCMD, 0)
        self._send_message(message)

    def _send_ack(self):
        """ Send event to notify that UART data is handled. """
        message = struct.pack('=QBH', 0, self.OT_SIM_EVENT_UART_ACK, 0)
        self._send_message(message)

    def read(self, size=1):
        """ Blocking read on stream object """
        if self.buffer:
            return self.buffer.pop(0)

        if self.pipe:
            assert self.pipe.poll() is None

        if self._done:
            self._send_ack()

        self._done += 1
        # send done before blocking receiving

        while not self.buffer:
            self._next_event()

        return self.buffer.pop(0)

    def __del__(self):
        self.close()

    def close(self):
        if self.pipe:
            self.pipe.terminate()
            self.pipe = None
        if self.sock:
            self.sock.close()
            self.sock = None


def StreamOpen(stream_type, descriptor, verbose=True, baudrate=115200):
    """
    Factory function that creates and opens a stream connection.

    stream_type:
        'u' = uart (/dev/tty#)
        's' = socket (port #)
        'p' = pipe (stdin/stdout)

    descriptor:
        uart - filename of device (/dev/tty#)
        socket - port to open connection to on localhost
        pipe - filename of command to execute and bind via stdin/stdout
    """

    if stream_type == 'p':
        if verbose:
            print('Opening pipe to ' + str(descriptor))

        if int(os.getenv('VIRTUAL_TIME', '0')):
            return StreamVirtualTime(descriptor)
        else:
            return StreamPipe(descriptor)

    elif stream_type == 's':
        port = int(descriptor)
        hostname = "localhost"
        if verbose:
            print("Opening socket to " + hostname + ":" + str(port))
        return StreamSocket(hostname, port)

    elif stream_type == 'u':
        dev = str(descriptor)
        if verbose:
            print("Opening serial to " + dev + " @ " + str(baudrate))
        return StreamSerial(dev, baudrate)

    else:
        return None
