# vim: set et sw=4 sts=4 fileencoding=utf-8:
#
# Tardis: A Backup System
# Copyright 2013-2014, Eric Koldinger, All Rights Reserved.
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

import os
import os.path
import types
import sys
import argparse
import socket
import TardisDB
import TardisCrypto
import CacheDir
import Util
import CompressedBuffer
import logging
import subprocess
import time
import base64

from rdiff_backup import librsync
import tempfile
import shutil
import parsedatetime as pdt

version = "0.1"

database = "./tardisDB"
logger = None

class RegenerateException(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

class Regenerator:
    def __init__(self, cache, db, crypt=None, tempdir="/tmp"):
        self.logger = logging.getLogger("Regenerator")
        self.cacheDir = cache
        self.db = db
        self.tempdir = tempdir
        self.crypt = crypt

    def decryptFile(self, filename, size, iv):
        if self.crypt == None:
            raise Exception("Encrypted file.  No password specified")
        cipher = self.crypt.getContentCipher(base64.b64decode(iv))
        outfile = tempfile.TemporaryFile()
        infile = self.cacheDir.open(filename, 'rb')
        outfile.write(cipher.decrypt(infile.read()))
        outfile.truncate(size)
        outfile.seek(0)
        return outfile

    def recoverChecksum(self, cksum):
        self.logger.debug("Recovering checksum: %s", cksum)
        cksInfo = self.db.getChecksumInfo(cksum)
        if cksInfo is None:
            self.logger.error("Checksum %s not found", cksum)
            return None

        #self.logger.debug(" %s: %s", cksum, str(cksInfo))

        try:
            if cksInfo['basis']:
                basis = self.recoverChecksum(cksInfo['basis'])
                # UGLY.  Put the basis into an actual file for librsync
                if type(basis) is not types.FileType:
                    temp = tempfile.TemporaryFile()
                    shutil.copyfileobj(basis, temp)
                    basis = temp
                #librsync.patch(basis, self.cacheDir.open(cksum, "rb"), output)
                if cksInfo['iv']:
                    patchfile = self.decryptFile(cksum, cksInfo['deltasize'], cksInfo['iv'])
                else:
                    patchfile = self.cacheDir.open(cksum, 'rb')

                if cksInfo['compressed']:
                    self.logger.debug("Decompressing %s", cksum)
                    temp = tempfile.TemporaryFile()
                    buf = CompressedBuffer.UncompressedBufferedReader(patchfile)
                    shutil.copyfileobj(buf, temp)
                    temp.seek(0)
                    patchfile = temp
                try:
                    output = librsync.PatchedFile(basis, patchfile)
                except librsyncError as e:
                    self.logger.error("Recovering checksum: {} : {}".format(cksum, e))
                    raise RegenerateException("Checksum: {}: Error: {}".format(chksum, e))

                #output.seek(0)
                return output
            else:
                if cksInfo['iv']:
                    output =  self.decryptFile(cksum, cksInfo['size'], cksInfo['iv'])
                else:
                    output =  self.cacheDir.open(cksum, "rb")

                if cksInfo['compressed']:
                    self.logger.debug("Decompressing %s", cksum)
                    temp = tempfile.TemporaryFile()
                    buf = CompressedBuffer.UncompressedBufferedReader(output)
                    shutil.copyfileobj(buf, temp)
                    temp.seek(0)
                    output = temp

                return output

        except Exception as e:
            self.logger.error("Unable to recover checksum %s: %s", cksum, e)
            self.logger.exception(e)
            raise RegenerateException("Checksum: {}: Error: {}".format(cksum, e))

    def recoverFile(self, filename, bset=False, nameEncrypted=False):
        self.logger.debug("Recovering file: {}".format(filename))
        name = filename
        if self.crypt and not nameEncrypted:
            name = self.crypt.encryptPath(filename)
        cksum = self.db.getChecksumByPath(name, bset)
        if cksum:
            try:
                return self.recoverChecksum(cksum)
            except RegenerateException:
                raise
            except:
                raise RegenerateException("Error recovering file: {}".format(filename))
        else:
            self.logger.error("Could not locate file {}".format(filename))
            return None


def findDirInRoot(tardis, bset, path):
    comps = path.split(os.sep)
    comps.pop(0)
    for i in range(0, len(comps)):
        name = comps[i]
        logger.debug("Looking for root directory %s (%d)", name, i)
        info = tardis.getFileInfoByName(name, (0, 0), bset)
        if info and info['dir'] == 1:
            return i
    return None

def computePath(tardis, bset, path, reduce):
    logger.debug("Computing path for %s (%d)", path, reduce)
    path = os.path.abspath(path)
    if reduce == sys.maxint:
        reduce = findDirInRoot(tardis, bset, path)
    if reduce:
        logger.debug("Reducing path by %d entries: %s", reduce, path)
        comps = path.split(os.sep)
        if reduce > len(comps):
            logger.error("Path reduction value (%d) greater than path length (%d) for %s.  Skipping.", reduce, len(comps), path)
            return None
        tmp = os.path.join(os.sep, *comps[reduce + 1:])
        logger.info("Reduced path %s to %s", path, tmp)
        path = tmp
    return path

def mkOutputDir(name):
    if os.path.isdir(name):
        return name
    elif os.path.exists(name):
        self.logger.error("%s is not a directory")
    else:
        os.mkdir(name)
        return name


def parseArgs():
    parser = argparse.ArgumentParser(sys.argv[0], description="Regenerate a Tardis backed file")

    parser.add_argument("--output", "-o",   dest="output", help="Output file", default=None)
    parser.add_argument("--database", "-d", help="Path to database directory", dest="database", default=database)
    parser.add_argument("--host", "-H", help="Host to process for", dest='host', default=socket.gethostname())
    parser.add_argument("--checksum", "-c", help="Use checksum instead of filename", dest='cksum', action='store_true', default=False)

    bsetgroup = parser.add_mutually_exclusive_group()
    bsetgroup.add_argument("--backup", "-b", help="backup set to use", dest='backup', default=None)
    bsetgroup.add_argument("--date", "-D",   help="Regenerate as of date", dest='date', default=None)

    pwgroup = parser.add_mutually_exclusive_group()
    pwgroup.add_argument('--password',      dest='password', default=None,          help='Encrypt files with this password')
    pwgroup.add_argument('--password-file', dest='passwordfile', default=None,      help='Read password from file')
    pwgroup.add_argument('--password-url',  dest='passwordurl', default=None,       help='Retrieve password from the specified URL')

    parser.add_argument('--reduce-path', '-R',  dest='reduce',  default=0, const=sys.maxint, type=int, nargs='?',   metavar='N', help='Reduce path by N directories.  No value for "smart" reduction')
    parser.add_argument('--set-times', '-T',    dest='settime', default=False, action='store_true', help='Set file times to match original file')
    parser.add_argument('--set-perms', '-P',    dest='setperm', default=False, action='store_true', help='Set file owner and permisions to match original file')

    parser.add_argument('--verbose', '-v', action='count', dest='verbose', help='Increase the verbosity')
    parser.add_argument('--version', action='version', version='%(prog)s ' + version, help='Show the version')
    parser.add_argument('files', nargs='+', default=None, help="List of files to regenerate")

    args = parser.parse_args()

    return args

def setupLogging(args):
    FORMAT = "%(levelname)s : %(name)s : %(message)s"
    #formatter = logging.Formatter("%(levelname)s : %(name)s : %(message)s")
    #handler = logging.StreamHandler(stream=sys.stderr)
    #handler.setFormatter(formatter)
    #logger.addHandler(handler)
    logging.basicConfig(stream=sys.stderr, format=FORMAT)
    logger = logging.getLogger("")
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    logging.getLogger("parsedatetime").setLevel(logging.WARNING)

    return logger

def main():
    global logger
    args = parseArgs()
    logger = setupLogging(args)

    baseDir = os.path.join(args.database, args.host)
    dbName = os.path.join(baseDir, "tardis.db")
    tardis = TardisDB.TardisDB(dbName, backup=False)
    cache = CacheDir.CacheDir(baseDir)

    crypt = None

    password = Util.getPassword(args.password, args.passwordfile, args.passwordurl)
    args.password = None
    if password:
        crypt = TardisCrypto.TardisCrypto(password)
    password = None

    r = Regenerator(cache, tardis, crypt=crypt)

    token = None
    if crypt:
        token = crypt.encryptFilename(args.host)
        if not tardis.checkToken(token):
            logger.critical("Login failed.  Password does not match")
            sys.exit(1)

    bset = False

    if args.date:
        cal = pdt.Calendar()
        (then, success) = cal.parse(args.date)
        if success:
            timestamp = time.mktime(then)
            logger.info("Using time: %s", time.asctime(then))
            bsetInfo = tardis.getBackupSetInfoForTime(timestamp)
            if bsetInfo and bsetInfo['backupset'] != 1:
                bset = bsetInfo['backupset']
                logger.debug("Using backupset: %s %d", bsetInfo['name'], bsetInfo['backupset'])
            else:
                logger.critical("No backupset at date: %s (%s)", args.date, time.asctime(then))
                sys.exit(1)
        else:
            logger.critical("Could not parse date string: %s", args.date)
            sys.exit(1)
    elif args.backup:
        bsetInfo = tardis.getBackupSetInfo(args.backup)
        if bsetInfo:
            bset = bsetInfo['backupset']
        else:
            logger.critical("No backupset at for name: %s", args.backup)
            sys.exit(1)

    outputdir = None
    output = sys.stdout
    if args.output:
        if len(args.files) > 1:
            outputdir = mkOutputDir(args.output)
        elif os.path.isdir(args.output):
            outputdir = args.output
        else:
            output = file(args.output, "wb")

    # do the work here
    for i in args.files:
        f = None
        outname = None
        if args.cksum:
            f = r.recoverChecksum(i)
        else:
            path = computePath(tardis, bset, i, args.reduce)
            if not path:
                continue
            f = r.recoverFile(path, bset)

        if f != None:
            if outputdir:
                (d, n) = os.path.split(i)
                outname = os.path.join(outputdir, n)
                logger.debug("Writing output from %s to %s", path, outname)
                output = file(outname,  "wb")
            try:
                x = f.read(16 * 1024)
                while x:
                    output.write(x)
                    x = f.read(16 * 1024)
            except Exception as e:
                logger.error("Unable to read file: {}: {}".format(i, repr(e)))
            finally:
                f.close()
                if outputdir:
                    output.close()
                if output is not None:
                    if args.settime or args.setperm:
                        info = tardis.getFileInfoByPath(path, bset)
                        if info:
                            if args.settime:
                                os.utime(outname, (info['mtime'], info['mtime']))
                            if args.setperm:
                                os.chmod(outname, info['mode'])
                                os.chown(outname, info['uid'], info['gid'])


if __name__ == "__main__":
    sys.exit(main())
