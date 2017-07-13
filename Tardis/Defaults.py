# vim: set et sw=4 sts=4 fileencoding=utf-8:
#
# Tardis: A Backup System
# Copyright 2013-2016, Eric Koldinger, All Rights Reserved.
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
import ConfigParser
import os

SECTION = 'Tardis'

_defaults = {
    'TARDIS_DB'             : '/srv/tardis',
    'TARDIS_DBDIR'          : '',
    'TARDIS_DBNAME'         : 'tardis.db',
    'TARDIS_CLIENT'         : socket.gethostname(),
    'TARDIS_SERVER'         : 'localhost',
    'TARDIS_EXCLUDES'       : '.tardis-excludes',
    'TARDIS_LOCAL_EXCLUDES' : '.tardis-local-excludes',
    'TARDIS_GLOBAL_EXCLUDES': '/etc/tardis/excludes',
    'TARDIS_SKIP'           : '.tardis-skip',
    'TARDIS_PORT'           : '7420',
    'TARDIS_TIMEOUT'        : '300',
    'TARDIS_DAEMON_CONFIG'  : '/etc/tardis/tardisd.cfg',
    'TARDIS_LOCAL_CONFIG'   : '/etc/tardis/tardisd.local.cfg',
    'TARDIS_PIDFILE'        : '/var/run/tardisd.pid',
    'TARDIS_JOURNAL'        : 'tardis.journal',
    'TARDIS_SCHEMA'         : 'schema/tardis.sql',
    'TARDIS_SEND_CONFIG'    : 'true',
    'TARDIS_REMOTE_PORT'    : '7430',
    'TARDIS_REMOTE_CONFIG'  : '/etc/tardis/tardisremote.cfg',
    'TARDIS_REMOTE_PIDFILE' : '/var/run/tardisremote.pid',
    'TARDIS_LS_COLORS'      : "gone=yellow:changed=cyan:full=cyan,,bold:moved=blue:header=green:name=white:error=red,,bold:default=white",
    'TARDIS_NOCOMPRESS'     : None,
    'TARDIS_RECENT_SET'     : 'Current',
    'TARDIS_PW_STRENGTH'    : '0.75',
    'TARDIS_DEFAULTS'       : '/etc/tardis/system.defaults'
}

try:
    _default_file = os.environ['TARDIS_DEFAULTS']
except KeyError:
    _default_file = _defaults['TARDIS_DEFAULTS']

_parser = ConfigParser.ConfigParser(_defaults)
_parser.add_section(SECTION)                       # Keep it happy later.
_parser.read(_default_file)

def getDefault(var):
    if var in os.environ:
        return os.environ[var]
    else:
        try:
            return _parser.get(SECTION, var)
        except ConfigParser.Error:
            return None

if __name__ == "__main__":
    print _default_file
    for i in _defaults:
        print "%-24s: %s" % (i, getDefault(i))
