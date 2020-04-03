# vim: set et sw=4 sts=4 fileencoding=utf-8:
#
# Tardis: A Backup System
# Copyright 2013-2020, Eric Koldinger, All Rights Reserved.
# kolding@washington.edu
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the copyright holder nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import socket
import json
import time
import ssl

import Tardis
import Tardis.Messages as Messages

protocolVersion = "1.4"
headerString    = "TARDIS " + protocolVersion
sslHeaderString = headerString + "/SSL"

class ConnectionException(Exception):
    pass

class Connection(object):
    """ Root class for handling connections to the tardis server """
    def __init__(self, host, port, encoding, compress, timeout=None, validate=False):
        self.stats = { 'messagesRecvd': 0, 'messagesSent' : 0, 'bytesRecvd': 0, 'bytesSent': 0 }

        # Create and open the socket
        if host:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if timeout:
                sock.settimeout(timeout)
            sock.connect((host, int(port)))
            self.sock = sock
        else:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            if timeout:
                self.sock.settimeout(timeout)
            self.sock.connect(port)

        try:
            # Receive a string.  TARDIS proto=1.0
            message = str(self.sock.recv(32).strip(), 'utf8')
            if message == sslHeaderString:
                # Overwrite self.sock
                self.sock = ssl.wrap_socket(self.sock, server_side=False) #, cert_reqs=ssl.CERT_REQUIRED, ca_certs="/etc/ssl/certs/ca-bundle.crt")
                if validate:
                    pass        # TODO Check the certificate hostname.  Requires python 2.7.9 or higher.
            elif not message:
                raise Exception("No header string.")
            elif message != headerString:
                raise Exception("Unknown protocol: {}".format(message))
            resp = { 'encoding': encoding, 'compress': compress }
            self.put(bytes(json.dumps(resp), 'utf8'))

            message = self.sock.recv(256).strip()
            fields = json.loads(message)
            if fields['status'] != 'OK':
                raise ConnectionException("Unable to connect")
        except Exception:
            self.sock.close()
            raise

    def put(self, message):
        self.sock.sendall(message)
        self.stats['messagesSent'] += 1
        return

    def recv(self, n):
        msg = ''
        while len(msg) < n:
            chunk = self.sock.recv(n-len(msg))
            if chunk == '':
                raise RuntimeError("socket connection broken")
            msg = msg + chunk
        return msg

    def get(self, size):
        message = self.sock.recv(size).strip()
        self.stats['messagesRecvd'] += 1
        return message

    def close(self):
        self.sock.close()

    def getStats(self):
        return self.stats

class ProtocolConnection(Connection):
    sender = None
    def __init__(self, host, port, protocol, compress, timeout):
        Connection.__init__(self, host, port, protocol, compress)

    def send(self, message, compress=True):
        self.sender.sendMessage(message, compress)
        self.stats['messagesSent'] += 1

    def receive(self):
        message = self.sender.recvMessage()
        self.stats['messagesRecvd'] += 1
        return message

    def close(self):
        self.send({"message" : "BYE" })
        super(ProtocolConnection, self).close()

    def encode(self, string):
        return self.sender.encode(string)

    def decode(self, string):
        return self.sender.decode(string)

_defaultVersion = Tardis.__buildversion__  or Tardis.__version__

class JsonConnection(ProtocolConnection):
    """ Class to communicate with the Tardis server using a JSON based protocol """
    def __init__(self, host, port, compress, timeout):
        ProtocolConnection.__init__(self, host, port, 'JSON', False, timeout)
        # Really, cons this up in the connection, but it needs access to the sock parameter, so.....
        self.sender = Messages.JsonMessages(self.sock, stats=self.stats)

class BsonConnection(ProtocolConnection):
    def __init__(self, host, port, compress, timeout):
        ProtocolConnection.__init__(self, host, port, 'BSON', compress, timeout)
        # Really, cons this up in the connection, but it needs access to the sock parameter, so.....
        self.sender = Messages.BsonMessages(self.sock, stats=self.stats, compress=compress)

class MsgPackConnection(ProtocolConnection):
    def __init__(self, host, port, compress, timeout):
        ProtocolConnection.__init__(self, host, port, 'MSGP', compress, timeout)
        # Really, cons this up in the connection, but it needs access to the sock parameter, so.....
        self.sender = Messages.MsgPackMessages(self.sock, stats=self.stats, compress=compress)

if __name__ == "__main__":
    """
    Test Code
    """
    conn = JsonConnection("localhost", 9999, "HiMom")
    print(conn.getSessionId())
    conn.send({ 'x' : 1 })
    print(conn.receive())
    conn.send({ 'y' : 2 })
    print(conn.receive())
    conn.close()