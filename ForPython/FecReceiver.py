# -*- coding: utf-8 -*-

#**********************************************************************************************************************#
#                   OPTIMIZED AND CROSS PLATFORM SMPTE 2022-1 FEC LIBRARY IN C, JAVA, PYTHON, +TESTBENCH
#
#  Description    : SMPTE 2022-1 FEC Library
#  Main Developer : David Fischer (david.fischer.ch@gmail.com)
#  Copyright      : Copyright (c) 2008-2013 smpte2022lib Team. All rights reserved.
#  Sponsoring     : Developed for a HES-SO CTI Ra&D project called GaVi
#                   Haute école du paysage, d'ingénierie et d'architecture @ Genève
#                   Telecommunications Laboratory
#
#**********************************************************************************************************************#
#
# This file is part of smpte2022lib.
#
# This project is free software: you can redistribute it and/or modify it under the terms of the EUPL v. 1.1 as provided
# by the European Commission. This project is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See the European Union Public License for more details.
#
# You should have received a copy of the EUPL General Public License along with this project.
# If not, see he EUPL licence v1.1 is available in 22 languages:
#     22-07-2013, <https://joinup.ec.europa.eu/software/page/eupl/licence-eupl>
#
# Retrieved from https://github.com/davidfischer-ch/smpte2022lib.git

from FecPacket import FecPacket
from IPSocket import IPSocket
from RtpPacket import RtpPacket
from pyutils.py_unicode import to_bytes


class FecReceiver(object):
    u"""
    TODO

    **Example usage**

    Media packets are sorted by the buffer, so, it's time to test this feature:

    >>> import random
    >>> from StringIO import StringIO
    >>> output = StringIO()
    >>> receiver = FecReceiver(output)
    >>> receiver.setDelay(1024, FecReceiver.PACKETS)
    >>> source = range(1024)
    >>> random.shuffle(source)
    >>> for i in source:
    ...     receiver.putMedia(RtpPacket.create(i, i * 100, RtpPacket.MP2T_PT, str(i)), True)
    >>> receiver.flush()
    >>> assert(output.getvalue() == u''.join(str(i) for i in range(1024)))

    Testing fec algorithm correctness:

    >>> import random
    >>> from io import BytesIO
    >>> from os import urandom
    >>> from random import randint
    >>> from FecPacket import FecPacket
    >>> output = BytesIO()
    >>> receiver = FecReceiver(output)
    >>> receiver.setDelay(1024, FecReceiver.PACKETS)
    >>> L, D = 4, 5
    >>> # Generate a [D][L] matrix of randomly generated RTP packets
    >>> matrix = [[RtpPacket.create(L * j + i, (L * j + i) * 100 + randint(0, 50), \
                  RtpPacket.MP2T_PT, bytearray(urandom(randint(50, 100)))) \
                  for i in range(L)] for j in range(D)]
    >>> assert(len(matrix) == D and len(matrix[0]) == L)
    >>> # Retrieve the first column of the matrix
    >>> for column in matrix:
    ...     for media in column:
    ...         if media.sequence != 0:
    ...             receiver.putMedia(media, True)
    >>> fec = FecPacket.compute(1, FecPacket.XOR, FecPacket.COL, L, D, [p[0] for p in matrix[0:]])
    >>> print u'dir={0} snbase={1} offset={2} na={3}'.format(fec.direction, fec.snbase, fec.offset, fec.na)
    dir=0 snbase=0 offset=4 na=5
    >>> receiver.putFec(fec)
    >>> print receiver
    Name  Received Buffered Maximum Dropped
    Media       19       20      20
    Col          1        0       1       0
    Row          0        0       0       0
    Cross                 0       1
    FEC statistics, media packets :
    Recovered Aborted Overwritten Missing
            1       0           0       0
    Current position (media sequence) : 0
    Current delay (can be set) : 20 packets
    FEC matrix size (LxD) : 4x5 = 20 packets
    >>> receiver.flush()

    The output does begin by recovered first packet of the matrix:

    >>> print matrix[0][1].payload == output.getvalue()[:len(matrix[0][1].payload)]
    False
    >>> print matrix[0][0].payload == output.getvalue()[:len(matrix[0][0].payload)]
    True
    """

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Constants >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

    ER_DELAY_UNITS = u"Unknown delay units '{0}'"
    ER_DIRECTION = u'FEC packet direction is neither COL nor ROW : {0}'
    ER_FLUSHING = u'Currently flushing buffers'
    ER_MISSING_COUNT = u'They are {0} missing media packet, expected one (1)'
    ER_COL_MISMATCH = u'Column FEC packet n°{0}, expected n°{1}'
    ER_COL_OVERWRITE = u'Another column FEC packet is already registered to protect media packet n°{0}'
    ER_ROW_MISMATCH = u'Row FEC packet n°{0}, expected n°{1}'
    ER_ROW_OVERWRITE = u'Another row FEC packet is already registered to protect media packet n°{0}'
    ER_GET_COL_CASCADE = u'Column FEC cascade : Unable to compute sequence # of the media packet to recover\n{0}\n'
    ER_GET_ROW_CASCADE = u'Row FEC cascade : Unable to compute sequence # of the media packet to recover\n{0}\n'
    ER_NULL_COL_CASCADE = u'Column FEC cascade : Unable to find linked entry in crosses buffer'
    ER_NULL_ROW_CASCADE = u'Row FEC cascade : Unable to find linked entry in crosses buffer'
    ER_STARTUP = u'Current position still not initialized (startup state)'
    ER_VALID_RTP_MP2TS = u'packet is not valid (expected RTP packet + MPEG2-TS payload)'
    ER_VALID_RTP = u'packet is not valid (expected RTP packet)'

    DELAY_NAMES = [u'packets', u'seconds']
    DELAY_RANGE = range(len(DELAY_NAMES))
    PACKETS, SECONDS = DELAY_RANGE

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Constructors >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

    def __init__(self, output):
        u"""
        Construct a new FecReceiver and register ``output``.

        :param output: Where to output payload of the recovered stream.
        :type output: IOBase

        **Example usage**

        Not yet an output:

        >>> FecReceiver(None)
        Traceback (most recent call last):
            ...
        ValueError: output is None

        >>> from StringIO import StringIO
        >>> output = StringIO()
        >>> receiver = FecReceiver(output)
        """
        if not output:
            raise ValueError(to_bytes(u'output is None'))
        # Media packets storage, medias[media seq] = media pkt
        self.medias = {}
        self.startup = True    # Indicate that actual position must be initialized
        self.flushing = False  # Indicate that a flush operation is actually running
        self.position = 0      # Actual position (sequence number) in the medias buffer
        # Link media packets to fec packets able to recover it, crosses[mediaseq] = {colseq, rowseq}
        self.crosses = {}
        # Fec packets + related informations storage, col[sequence] = { fec pkt + infos }
        self.cols = {}
        self.rows = {}
        self.matrixL = 0  # Detected FEC matrix size (number of columns)
        self.matrixD = 0  # Detected FEC matrix size (number of rows)
        # Output
        self.output = output  # Registered output
        # Settings
        self.delay_value = 100                  # RTP buffer delay value
        self.delay_units = FecReceiver.PACKETS  # RTP buffer delay units
        # Statistics about media (buffers and packets)
        self.media_received = 0          # Received media packets counter
        self.media_recovered = 0         # Recovered media packets counter
        self.media_aborted_recovery = 0  # Aborted media packet recovery counter
        self.media_overwritten = 0       # Overwritten media packets counter
        self.media_missing = 0           # Missing media packets counter
        self.max_media = 0               # Largest amount of stored elements in the medias buffer
        # Statistics about fec (buffers and packets)
        self.col_received = 0  # Received column fec packets counter
        self.row_received = 0  # Received row fec packets counter
        self.col_dropped = 0   # Dropped column fec packets counter
        self.row_dropped = 0   # Dropped row fec packets counter
        self.max_cross = 0     # Largest amount of stored elements in the crosses buffer
        self.max_col = 0       # Largest amount of stored elements in the columns buffer
        self.max_row = 0       # Largest amount of stored elements in the rows buffer
        self.lostogram = {}    # Statistics about lost medias

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Properties >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

    @property
    def current_delay(self):
        u"""TODO"""
        if len(self.medias) == 0:
            return 0
        if self.delay_units == FecReceiver.PACKETS:
            return len(self.medias)
        elif self.delay_units == FecReceiver.SECONDS:
            raise NotImplementedError()
        raise ValueError(to_bytes(FecReceiver.ER_DELAY_UNITS.format(self.delay_units)))
            #return medias.lastEntry().getValue().getTime() - medias.firstEntry().getValue().getTime()

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Functions >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

    def setDelay(self, value, units):
        u"""Set desired size for the internal media buffer."""
        if units == FecReceiver.PACKETS:
            self.delay_value = value
            self.delay_units = units
        elif units == FecReceiver.SECONDS:
            raise NotImplementedError()
        else:
            raise ValueError(to_bytes(FecReceiver.ER_DELAY_UNITS.format(units)))

    def putMedia(self, media, onlyMP2TS):
        u"""Put an incoming media packet."""
        if self.flushing:
            raise ValueError(to_bytes(FecReceiver.ER_FLUSHING))
        if onlyMP2TS:
            if not media.validMP2T:
                raise ValueError(to_bytes(FecReceiver.ER_VALID_RTP_MP2TS))
        else:
            if not media.valid:
                raise ValueError(to_bytes(FecReceiver.ER_VALID_RTP))

        # Put the media packet into medias buffer
        if media.sequence in self.medias:
            self.media_overwritten += 1
        self.medias[media.sequence] = media
        if len(self.medias) > self.max_media:
            self.max_media = len(self.medias)
        self.media_received += 1

        cross = self.crosses.get(media.sequence)
        if cross:
            # Simulate the recovery of a media packet to update buffers and potentially start
            self.recoverMediaPacket(media.sequence, cross, None)  # a recovery cascade !

        self.out()  # FIXME maybe better to call it from another thread

    def putFec(self, fec):
        u"""
        Put an incoming FEC packet, the algorithm will do the following according to these scenarios:

        1. The fec packet is useless if none of the protected media packets is missing
        2. Only on media packet missing, fec packet is able to recover it now !
        3. More than one media packet is missing, fec packet stored for future recovery
        """
        if self.flushing:
            raise ValueError(to_bytes(FecReceiver.ER_FLUSHING))
        if not fec.valid:
            raise ValueError(to_bytes(u'Invalid FEC packet'))

        if fec.direction == FecPacket.COL:
            self.col_received += 1
        elif fec.direction == FecPacket.ROW:
            self.row_received += 1
        else:
            raise ValueError(FecReceiver.ER_DIRECTION.format(fec.direction))
        cross = None
        media_lost = 0
        media_max = (fec.snbase + fec.na * fec.offset) & RtpPacket.S_MASK
        media_test = fec.snbase
        while media_test != media_max:
            # If media packet is not in the medias buffer (is missing)
            if not media_test in self.medias:
                media_lost = media_test
                # TODO
                cross = self.crosses.get(media_test)
                if not cross:
                    cross = {u'col_sequence': None, u'row_sequence': None}
                    self.crosses[media_test] = cross
                    if len(self.crosses) > self.max_cross:
                        self.max_cross = len(self.crosses)
                # Register the fec packet able to recover the missing media packet
                if fec.direction == FecPacket.COL:
                    if cross[u'col_sequence']:
                        raise ValueError(to_bytes(FecReceiver.ER_COL_OVERWRITE.format(media_lost)))
                    cross[u'col_sequence'] = fec.sequence
                elif fec.direction == FecPacket.ROW:
                    if cross[u'row_sequence']:
                        raise ValueError(to_bytes(FecReceiver.ER_ROW_OVERWRITE.format(media_lost)))
                    cross[u'row_sequence'] = fec.sequence
                else:
                    raise ValueError(to_bytes(FecReceiver.ER_FEC_DIRECTION.format(fec.direction)))
                fec.setMissing(media_test)
            media_test = (media_test + fec.offset) & RtpPacket.S_MASK
        if fec.L != 0:
            self.matrixL = fec.L
        if fec.D != 0:
            self.matrixD = fec.D
        # [1] The fec packet is useless if none of the protected media packets is missing
        if len(fec.missing) == 0:
            return
        # FIXME check if 10*delay_value is a good way to avoid removing early fec packets !
        # The fec packet is useless if it needs an already output'ed media packet to do recovery
        drop = not FecReceiver.ValidityWindow(fec.snbase, self.position,
                                              (self.position + 10 * self.delay_value) & RtpPacket.S_MASK)
        if fec.direction == FecPacket.COL:
            if drop:
                self.col_dropped += 1
                return
            self.cols[fec.sequence] = fec
            if len(self.cols) > self.max_col:
                self.max_col = len(self.cols)
        if fec.direction == FecPacket.ROW:
            if drop:
                self.row_dropped += 1
                return
            self.rows[fec.sequence] = fec
            if len(self.rows) > self.max_row:
                self.max_row = len(self.rows)
        # [2] Only on media packet missing, fec packet is able to recover it now !
        if len(fec.missing) == 1:
            self.recoverMediaPacket(media_lost, cross, fec)
            self.out()  # FIXME maybe better to call it from another thread
        # [3] More than one media packet is missing, fec packet stored for future recovery

    def flush(self):
        u"""Flush all buffers and output media packets to registered output (``self.output``)."""
        try:
            self.flushing = True
            self.out()
            self.output.flush()
        finally:
            self.flushing = False

    def cleanup(self):
        u"""Remove FEC packets that are stored / waiting but useless."""
        if self.flushing:
            raise ValueError(to_bytes(FecReceiver.ER_FLUSHING))
        if self.startup:
            raise ValueError(to_bytes(FecReceiver.ER_STARTUP))
        if self.delay_units == FecReceiver.PACKETS:
            start, end = self.position, (self.position + self.delay_value) & RtpPacket.S_MASK
            for media_sequence in self.crosses.keys:
                if not self.ValidityWindow(media_sequence, start, end):
                    cross = self.crosses[media_sequence]
                    del self.cols[cross[u'col_sequence']]
                    del self.rows[cross[u'row_sequence']]
                    del self.crosses[media_sequence]
        elif self.delay_units == FecReceiver.SECONDS:
            raise NotImplementedError()
        raise ValueError(to_bytes(FecReceiver.ER_DELAY_UNITS.format(self.delay_units)))

    def recoverMediaPacket(self, media_sequence, cross, fec):
        u"""Recover a missing media packet helped by a FEC packet, this method is also called to register an incoming
        media packet if it is registered as missing."""

        recovered_by_fec = not fec is None

        # Read and remove "cross" it from the buffer
        col_sequence = cross[u'col_sequence']
        row_sequence = cross[u'row_sequence']
        del self.crosses[media_sequence]

        # Recover the missing media packet and remove any useless linked fec packet
        if recovered_by_fec:
            if len(fec.missing) != 1:
                raise NotImplementedError(FecReceiver.ER_MISSING_COUNT.format(len(fec.missing)))
            if fec.direction == FecPacket.COL and fec.sequence != col_sequence:
                raise NotImplementedError(FecReceiver.ER_COL_MISMATCH.format(fec.sequence, col_sequence))
            if fec.direction == FecPacket.ROW and fec.sequence != row_sequence:
                raise NotImplementedError(FecReceiver.ER_ROW_MISMATCH.format(fec.sequence, row_sequence))

            # Media packet recovery
            # > Copy fec packet fields into the media packet
            media = RtpPacket.create(media_sequence, fec.timestamp_recovery, fec.payload_type_recovery,
                                     fec.payload_recovery)
            payload_size = fec.length_recovery

            # > recovered payload ^= all media packets linked to the fec packet
            aborted = False
            media_max = (fec.snbase + fec.na * fec.offset) & RtpPacket.S_MASK
            media_test = fec.snbase
            while media_test != media_max:
                if media_test == media_sequence:
                    media_test = (media_test + fec.offset) & RtpPacket.S_MASK
                    continue
                friend = self.medias[media_test]
                # Unable to recover the media packet if any of the friend media packets is missing
                if not friend:
                    self.media_aborted_recovery += 1
                    aborted = True
                    break
                media.payload_type ^= friend.payload_type
                media.timestamp ^= friend.timestamp
                payload_size ^= friend.payload_size
                # FIXME FIXME FIXME FIXME FIXME OPTIMIZATION FIXME FIXME FIXME FIXME
                for no in range(min(len(media.payload), len(friend.payload))):
                    media.payload[no] ^= friend.payload[no]
                media_test = (media_test + fec.offset) & RtpPacket.S_MASK

            # If the media packet is successfully recovered
            if not aborted:
                media.payload = media.payload[0:payload_size]
                self.media_recovered += 1
                if media.sequence in self.medias:
                    self.media_overwritten += 1
                self.medias[media.sequence] = media
                if len(self.medias) > self.max_media:
                    self.max_media = len(self.medias)
                if fec.direction == FecPacket.COL:
                    del self.cols[fec.sequence]
                else:
                    del self.rows[fec.sequence]

        # Check if a cascade effect happens ...
        fec_col = self.cols.get(col_sequence) if col_sequence else None
        fec_row = self.rows.get(row_sequence) if row_sequence else None

        if fec_col:
            fec_col.setRecovered(media_sequence)
        if fec_row:
            fec_row.setRecovered(media_sequence)

        if fec_col:
            if len(fec_col.missing) == 1:
                # Cascade !
                cascade_media_sequence = fec_col.missing[0]
                if cascade_media_sequence:
                    cascade_cross = self.crosses.get(cascade_media_sequence)
                    if cascade_cross:
                        self.recoverMediaPacket(cascade_media_sequence, cascade_cross, fec_col)
                    else:
                        raise NotImplementedError(
                            to_bytes(u'recoverMediaPacket({0}, {1}, {2}):\n{3}\nmedia sequence : {4}\n{5}\n'.format(
                            media_sequence, cross, fec, FecReceiver.ER_NULL_COL_CASCADE, cascade_media_sequence,
                            fec_col)))
                else:
                    raise NotImplementedError(FecReceiver.ER_GET_COL_CASCADE.format(fec_col))

        if fec_row:
            if len(fec_row.missing) == 1:
                # Cascade !
                cascade_media_sequence = fec_row.missing[0]
                if cascade_media_sequence:
                    cascade_cross = self.crosses.get(cascade_media_sequence)
                    if cascade_cross:
                        self.recoverMediaPacket(cascade_media_sequence, cascade_cross, fec_row)
                    else:
                        raise NotImplementedError(
                            to_bytes(u'{0}\nrecoverMediaPacket({1}, {2}, {3}):\nmedia sequence : {4}\n{5}\n'.format(
                            FecReceiver.ER_NULL_ROW_CASCADE, media_sequence, cross, fec, cascade_media_sequence,
                            fec_row)))
                else:
                    raise NotImplementedError(to_bytes(FecReceiver.ER_GET_ROW_CASCADE.format(fec_row)))

    def out(self):
        u"""TODO"""
        units = FecReceiver.PACKETS if self.flushing else self.delay_units
        value = 0 if self.flushing else self.delay_value
        missing_count = 0
        # Extract packets to output in order to keep a 'certain' amount of them in the buffer
        if units == FecReceiver.PACKETS:  # based on buffer size
            while len(self.medias) > value:
                # Initialize or increment actual position (expected sequence number)
                self.position = (self.medias.keys()[0] if self.startup else (self.position + 1)) & RtpPacket.S_MASK
                self.startup = False
                media = self.medias.get(self.position)
                if media:
                    del self.medias[media.sequence]
                    if self.output:
                        self.output.write(media.payload)
                else:
                    self.media_missing += 1
                    missing_count += 1
                # Remove any fec packet linked to current media packet
                cross = self.crosses.get(self.position)
                if cross:
                    del self.crosses[self.position]
                    if cross[u'col_sequence']:
                        del self.cols[cross[u'col_sequence']]
                    if cross[u'row_sequence']:
                        del self.rows[cross[u'row_sequence']]
        # Extract packets to output in order to keep a 'certain' amount of them in the buffer
        elif units == FecReceiver.SECONDS:  # based on time stamps
            raise NotImplementedError()
        else:
            raise ValueError(FecReceiver.ER_DELAY_UNITS.format(units))
        if missing_count in self.lostogram:
            self.lostogram[missing_count] = self.lostogram[missing_count] + 1
        else:
            self.lostogram[missing_count] = 1

    def __str__(self):
        u"""
        TODO

        **Example usage**

        >>> print FecReceiver(u'salut')
        Name  Received Buffered Maximum Dropped
        Media        0        0       0
        Col          0        0       0       0
        Row          0        0       0       0
        Cross                 0       0
        FEC statistics, media packets :
        Recovered Aborted Overwritten Missing
                0       0           0       0
        Current position (media sequence) : 0
        Current delay (can be set) : 0 packets
        FEC matrix size (LxD) : 0x0 = 0 packets
        """
        delayFormat = u'%.0f' if self.delay_units == FecReceiver.PACKETS else u'%.2f'
        mDelay = (delayFormat % self.current_delay) + ' ' + FecReceiver.DELAY_NAMES[self.delay_units]
        return (u"Name  Received Buffered Maximum Dropped\n"
                "Media %8d%9d%8d\n"
                "Col   %8d%9d%8d%8d\n"
                "Row   %8d%9d%8d%8d\n"
                "Cross         %9d%8d\n"
                "FEC statistics, media packets :\n"
                "Recovered Aborted Overwritten Missing\n"
                "%9d%8d%12d%8d\n"
                "Current position (media sequence) : %s\n"
                "Current delay (can be set) : %s\n"
                "FEC matrix size (LxD) : %sx%s = %s packets" %
                (self.media_received, len(self.medias), self.max_media,
                 self.col_received, len(self.cols), self.max_col, self.col_dropped,
                 self.row_received, len(self.rows), self.max_row, self.row_dropped,
                 len(self.crosses), self.max_cross, self.media_recovered,
                 self.media_aborted_recovery, self.media_overwritten, self.media_missing,
                 self.position, mDelay, self.matrixL, self.matrixD, self.matrixL * self.matrixD))

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Static >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

    @staticmethod
    def computeColAddress(media_socket):
        u"""
        Compute column FEC socket based on media stream socket (port +2).

        **Example usage**

        >>> print FecReceiver.computeColAddress(u'192.168.50.100:8000')
        {u'ip': u'192.168.50.100', u'port': 8002}
        >>> print FecReceiver.computeColAddress(IPSocket(u'50.0.0.7:4000'))
        {u'ip': u'50.0.0.7', u'port': 4002}
        >>> print FecReceiver.computeColAddress('salut')
        Traceback (most recent call last):
            ....
        ValueError: salut is not a valid IP socket.
        """
        if not isinstance(media_socket, dict):
            media_socket = IPSocket(media_socket)
        media_socket[u'port'] += 2
        return media_socket

    @staticmethod
    def computeRowAddress(media_socket):
        u"""
        Compute column FEC socket based on media stream socket (port +4).

        **Example usage**

        >>> print FecReceiver.computeRowAddress(u'192.168.50.100:8000')
        {u'ip': u'192.168.50.100', u'port': 8004}
        >>> print FecReceiver.computeRowAddress(IPSocket(u'50.0.0.7:4000'))
        {u'ip': u'50.0.0.7', u'port': 4004}
        >>> print FecReceiver.computeRowAddress(u'salut')
        Traceback (most recent call last):
            ....
        ValueError: salut is not a valid IP socket.
        """
        if not isinstance(media_socket, dict):
            media_socket = IPSocket(media_socket)
        media_socket[u'port'] += 4
        return media_socket

    @staticmethod
    def ValidityWindow(current, start, end):
        u"""
        Returns True if ``current`` is in the validity window bounded by ``start`` and ``end``.

        This method is circular-buffer aware and they are 2 cases (validity window ``[====]``)::

            1) start=     6 end=9 :   0   1  2 3 4 5 [=======] 10 ... 65'533  65'534 65'535
            2) start=65'534 end=1 :  ======] 2 3 4 5  6 7 8 9  10 ... 65'533 [=============

        **Example usage**

        Testing validity window condition:

        >>> assert(not FecReceiver.ValidityWindow(    0,     5, 10))
        >>> assert(    FecReceiver.ValidityWindow(    5,     5, 10))
        >>> assert(    FecReceiver.ValidityWindow(    8,     5, 10))
        >>> assert(    FecReceiver.ValidityWindow(   10,     5, 10))
        >>> assert(not FecReceiver.ValidityWindow(   15,     5, 10))
        >>> assert(    FecReceiver.ValidityWindow(    0, 65534,  2))
        >>> assert(    FecReceiver.ValidityWindow(    2, 65534,  2))
        >>> assert(not FecReceiver.ValidityWindow(    5, 65534,  2))
        >>> assert(    FecReceiver.ValidityWindow(65534, 65534,  2))
        >>> assert(    FecReceiver.ValidityWindow(65535, 65534,  2))
        """
        if end > start:
            return current >= start and current <= end
        else:
            return current <= end or current >= start
