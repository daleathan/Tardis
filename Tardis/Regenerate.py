# vim: set et sw=4 sts=4 fileencoding=utf-8:
#
# Tardis: A Backup System
# Copyright 2013-2015, Eric Koldinger, All Rights Reserved.
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
import stat
import types
import sys
import argparse
import socket
import TardisDB
import TardisCrypto
import CacheDir
import RemoteDB
import Util
import CompressedBuffer
import Defaults

import logging
import subprocess
import time
import base64
import urlparse

import librsync
import tempfile
import shutil
import parsedatetime

import Tardis

logger  = None
crypt = None
OW_NEVER = 0
OW_ALWAYS = 1
OW_NEWER = 2
OW_OLDER = 3

overwriteNames = { 'never': OW_NEVER, 'always': OW_ALWAYS, 'newer': OW_NEWER, 'older': OW_OLDER }
owMode = OW_NEVER

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
        self.logger.debug("Decrypting %s", filename)
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
                    self.logger.debug("Checksum %s is not a file.  Creating a tempfile", cksum)
                    temp = tempfile.TemporaryFile()
                    shutil.copyfileobj(basis, temp)
                    basis = temp
                #librsync.patch(basis, self.cacheDir.open(cksum, "rb"), output)
                if cksInfo['iv']:
                    patchfile = self.decryptFile(cksum, cksInfo['deltasize'], cksInfo['iv'])
                else:
                    patchfile = self.cacheDir.open(cksum, 'rb')

                if cksInfo['compressed']:
                    self.logger.debug("Uncompressing %s", cksum)
                    temp = tempfile.TemporaryFile()
                    buf = CompressedBuffer.UncompressedBufferedReader(patchfile)
                    shutil.copyfileobj(buf, temp)
                    temp.seek(0)
                    patchfile = temp
                try:
                    output = librsync.patch(basis, patchfile)
                except librsync.LibrsyncError as e:
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
                    self.logger.debug("Uncompressing %s", cksum)
                    temp = tempfile.TemporaryFile()
                    buf = CompressedBuffer.UncompressedBufferedReader(output)
                    shutil.copyfileobj(buf, temp)
                    temp.seek(0)
                    output = temp

                return output

        except Exception as e:
            self.logger.error("Unable to recover checksum %s: %s", cksum, e)
            #self.logger.exception(e)
            raise RegenerateException("Checksum: {}: Error: {}".format(cksum, e))

    def recoverFile(self, filename, bset=False, nameEncrypted=False, permchecker=None):
        self.logger.info("Recovering file: {}".format(filename))
        name = filename
        if self.crypt and not nameEncrypted:
            name = self.crypt.encryptPath(filename)
        try:
            cksum = self.db.getChecksumByPath(name, bset, permchecker=permchecker)
            if cksum:
                return self.recoverChecksum(cksum)
            else:
                self.logger.error("Could not locate file: %s ", filename)
                return None
        except RegenerateException as e:
            self.logger.error("Could not regenerate file: %s: %s", filename, str(e))
            return None
        except Exception as e:
            #logger.exception(e)
            self.logger.error("Error recovering file: %s: %s", filename, str(e))
            return None
            #raise RegenerateException("Error recovering file: {}".format(filename))


def checkOverwrite(name, info):
    if os.path.exists(name):
        if owMode == OW_NEVER:
            return False
        elif owMode == OW_ALWAYS:
            return True
        else:
            stat = os.lstat(name)
            if stat.st_mtime < info['mtime']:
                # Current version is older
                return True if owMode == OW_NEWER else False
            else:
                # Current version is newer
                return True if owMode == OW_OLDER else False
    else:
        return True

def recoverObject(regenerator, info, bset, outputdir, path):
    """
    Main recovery routine.  Recover an object, based on the info object, and put it in outputdir.
    Note that path is for debugging only.
    """
    retCode = 0
    outname = None
    skip = False
    try:
        logger.info("Recovering object %s", path)
        if info:
            realname = info['name']
            if crypt:
                realname = crypt.decryptFilename(realname)
            if outputdir:
                outname = os.path.join(outputdir, realname)
                if not checkOverwrite(outname, info):
                    skip = True
                    logger.info("Skipping existing file: %s", path)

            if info['dir']:
                contents = tardis.readDirectory((info['inode'], info['device']), bset)
                if not outname:
                    logger.error("Cannot regenerate directory %s without outputdir specified", path)
                    raise Exception("Cannot regenerate directory %s without outputdir specified" % (path))
                if not os.path.exists(outname):
                    os.mkdir(outname)
                dirInode = (info['inode'], info['device'])
                for i in contents:
                    name = i['name']
                    childInfo = tardis.getFileInfoByName(name, dirInode, bset)
                    if crypt:
                        name = crypt.decryptFilename(name)
                    if childInfo:
                        recoverObject(regenerator, childInfo, bset, outname, os.path.join(path, name))
                    else:
                        retCode += 1
            elif not skip:
                checksum = info['checksum']
                i = regenerator.recoverChecksum(checksum)
                if i:
                    if info['link']:
                        # read and make a link
                        x = i.read(16 * 1024)
                        os.symlink(x, outname)
                        pass
                    else:
                        if outputdir:
                            # Generate an output name
                            logger.debug("Writing output to %s", outname)
                            output = file(outname,  "wb")
                        else:
                            output = sys.stdout
                        try:
                            x = i.read(16 * 1024)
                            while x:
                                output.write(x)
                                x = i.read(16 * 1024)
                        except Exception as e:
                            logger.error("Unable to read file: {}: {}".format(i, repr(e)))
                            raise
                        finally:
                            i.close()
                            if output is not sys.stdout:
                                output.close()

            if outname and args.setperm:
                try:
                    os.chmod(outname, info['mode'])
                except Exception as e:
                    logger.warning("Unable to set permissions for %s", outname)
                try:
                    # Change the group, then the owner.
                    # Change the group first, as only root can change owner, and that might fail.
                    os.chown(outname, -1, info['gid'])
                    os.chown(outname, info['uid'], -1)
                except Exception as e:
                    logger.warning("Unable to set owner and group of %s", outname)

    except Exception as e:
        #logger.exception(e)
        retCode += 1

    return retCode

def setupPermissionChecks():
    uid = os.getuid()
    gid = os.getgid()
    groups = os.getgroups()

    if (uid == 0):
        return None     # If super-user, return None.  Causes no checking to happen.

    # Otherwise, create a closure function which can be used to do checking for each file.
    def checkPermission(pUid, pGid, mode):
        if stat.S_ISDIR(mode):
            if (uid == pUid) and (stat.S_IRUSR & mode) and (stat.S_IXUSR & mode):
                return True
            elif (pGid in groups) and (stat.S_IRGRP & mode) and (stat.S_IXGRP & mode):
                return True
            elif (stat.S_IROTH & mode) and (stat.S_IXOTH & mode):
                return True
        else:
            if (uid == pUid) and (stat.S_IRUSR & mode):
                return True
            elif (pGid in groups) and (stat.S_IRGRP & mode):
                return True
            elif (stat.S_IROTH & mode):
                return True
        return False

    # And return the function.
    return checkPermission

def findDirInRoot(tardis, bset, path):
    comps = path.split(os.sep)
    comps.pop(0)
    for i in range(0, len(comps)):
        name = comps[i]
        logger.debug("Looking for root directory %s (%d)", name, i)
        if crypt:
            name = crypt.encryptFilename(name)
        info = tardis.getFileInfoByName(name, (0, 0), bset)
        if info and info['dir'] == 1:
            return i
    return None

def computePath(tardis, bset, path, reduce):
    logger.debug("Computing path for %s in %d (%d)", path, bset, reduce)
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
        #logger.info("Reduced path %s to %s", path, tmp)
        path = tmp
    return path

def findLastPath(tardis, path, reduce):
    logger.debug("findLastPath: %s", path)
    # Search all the sets in backwards order
    bsets = list(tardis.listBackupSets())
    for bset in reversed(bsets):
        logger.debug("Checking for path %s in %s (%d)", path, bset['name'], bset['backupset'])
        tmp = computePath(tardis, bset['backupset'], path, reduce)
        tmp2 = tmp
        if crypt:
            tmp2 = crypt.encryptPath(tmp)
        info = tardis.getFileInfoByPath(tmp2, bset['backupset'])
        if info:
            logger.debug("Found %s in backupset %s: %s", path, bset['name'], tmp)
            return bset['backupset'], tmp, bset['name']
    return (None, None, None)

def mkOutputDir(name):
    if os.path.isdir(name):
        return name
    elif os.path.exists(name):
        self.logger.error("%s is not a directory")
    else:
        os.mkdir(name)
        return name

def parseArgs():
    database = Defaults.getDefault('TARDIS_DB')
    hostname = Defaults.getDefault('TARDIS_CLIENT')
    dbname   = Defaults.getDefault('TARDIS_DBNAME')

    parser = argparse.ArgumentParser(description="Regenerate a Tardis backed file", formatter_class=Util.HelpFormatter)

    parser.add_argument("--output", "-o",   dest="output", help="Output file", default=None)
    parser.add_argument("--checksum", "-c", help="Use checksum instead of filename", dest='cksum', action='store_true', default=False)

    parser.add_argument("--database", "-d", help="Path to database directory (Default: %(default)s)", dest="database", default=database)
    parser.add_argument("--dbname", "-N",   help="Name of the database file (Default: %(default)s)", dest="dbname", default=dbname)
    parser.add_argument("--client", "-C",   help="Client to process for (Default: %(default)s)", dest='client', default=hostname)

    bsetgroup = parser.add_mutually_exclusive_group()
    bsetgroup.add_argument("--backup", "-b", help="Backup set to use", dest='backup', default=None)
    bsetgroup.add_argument("--date", "-D",   help="Regenerate as of date", dest='date', default=None)
    bsetgroup.add_argument("--last", "-l",   dest='last', default=False, action='store_true', help="Regenerate the most recent version of the file"), 

    pwgroup = parser.add_mutually_exclusive_group()
    pwgroup.add_argument('--password',      dest='password', default=None, nargs='?', const=True,   help='Encrypt files with this password')
    pwgroup.add_argument('--password-file', dest='passwordfile', default=None,      help='Read password from file')
    pwgroup.add_argument('--password-url',  dest='passwordurl', default=None,       help='Retrieve password from the specified URL')
    pwgroup.add_argument('--password-prog', dest='passwordprog', default=None,      help='Use the specified command to generate the password on stdout')

    parser.add_argument('--crypt',          dest='crypt', default=True, action=Util.StoreBoolean, help='Are files encyrpted, if password is specified. Default: %(default)s')

    parser.add_argument('--reduce-path', '-R',  dest='reduce',  default=0, const=sys.maxint, type=int, nargs='?',   metavar='N',
                        help='Reduce path by N directories.  No value for "smart" reduction')
    parser.add_argument('--set-times', dest='settime', default=True, action=Util.StoreBoolean, help='Set file times to match original file')
    parser.add_argument('--set-perms', dest='setperm', default=True, action=Util.StoreBoolean, help='Set file owner and permisions to match original file')
    parser.add_argument('--overwrite-mode', '-M', dest='overwrite', default='never', choices=['always', 'newer', 'older', 'never'], help='Mode for handling existing files')

    parser.add_argument('--verbose', '-v', action='count', dest='verbose', help='Increase the verbosity')
    parser.add_argument('--version', action='version', version='%(prog)s ' + Tardis.__version__, help='Show the version')
    parser.add_argument('files', nargs='+', default=None, help="List of files to regenerate")

    args = parser.parse_args()

    return args

def setupLogging(args):
    #FORMAT = "%(levelname)s : %(name)s : %(message)s"
    FORMAT = "%(levelname)s : %(message)s"
    logging.basicConfig(stream=sys.stderr, format=FORMAT)
    logger = logging.getLogger("")
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    logging.getLogger("parsedatetime").setLevel(logging.WARNING)

    return logger

def main():
    global logger, crypt, tardis, args, owMode
    args = parseArgs()
    logger = setupLogging(args)

    password = Util.getPassword(args.password, args.passwordfile, args.passwordurl, args.passwordprog)
    args.password = None
    if password:
        crypt = TardisCrypto.TardisCrypto(password)
    password = None

    token = None
    if crypt:
        token = crypt.createToken()

    owMode = overwriteNames[args.overwrite]

    try:
        loc = urlparse.urlparse(args.database)
        if (loc.scheme == 'http') or (loc.scheme == 'https'):
            tardis = RemoteDB.RemoteDB(args.database, args.client, token=token)
            cache = tardis
        else:
            print args.database, loc.path, args.client
            baseDir = os.path.join(loc.path, args.client)
            cache = CacheDir.CacheDir(baseDir, create=False)
            dbPath = os.path.join(baseDir, args.dbname)
            tardis = TardisDB.TardisDB(dbPath, token=token)
    except Exception as e:
        logger.critical("Unable to connect to database: %s", str(e))
        logger.exception(e)
        sys.exit(1)

    if not args.crypt:
        crypt = None

    r = Regenerator(cache, tardis, crypt=crypt)

    bset = False

    if args.date:
        cal = parsedatetime.Calendar()
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
    output    = sys.stdout
    outname   = None

    if args.output:
        if len(args.files) > 1:
            outputdir = mkOutputDir(args.output)
        elif os.path.isdir(args.output):
            outputdir = args.output
        else:
            outname = args.output
            output = file(args.output, "wb")
    logger.debug("Outputdir: %s  Outname: %s", outputdir, outname)

    #if args.cksum and (args.settime or args.setperm):
        #logger.warning("Unable to set time or permissions on files specified by checksum.")

    permChecker = setupPermissionChecks()

    retcode = 0
    # do the work here
    if args.cksum:
        for i in args.files:
            try:
                f = r.recoverChecksum(i)
                if f:
                # Generate an output name
                    if outputdir:
                        outname = os.path.join(outputdir, i)
                        logger.debug("Writing output to %s", outname)
                        output = file(outname,  "wb")
                try:
                    x = f.read(16 * 1024)
                    while x:
                        output.write(x)
                        x = f.read(16 * 1024)
                except Exception as e:
                    logger.error("Unable to read file: {}: {}".format(i, repr(e)))
                    raise
                finally:
                    f.close()
                    if output is not sys.stdout:
                        output.close()
            except Exception as e:
                #logger.exception(e)
                retcode += 1
    else:
        for i in args.files:
            i = os.path.abspath(i)
            logger.info("Processing %s", i)
            path = None
            f = None
            if args.last:
                (bset, path, name) = findLastPath(tardis, i, args.reduce)
                if bset is None:
                    logger.error("Unable to find a latest version of %s", i)
                    raise Exception("Unable to find a latest version of " + i)
                logger.info("Found %s in backup set %s", i, name)
            elif args.reduce:
                path = computePath(tardis, bset, i, args.reduce)
                logger.debug("Reduced path %s to %s", path, tmp)
                if not path:
                    logger.error("Unable to find a compute path for %s", i)
                    raise Exception("Unable to compute path for " + i)
            else:
                path = i

            if crypt:
                actualPath = crypt.encryptPath(path)
            else:
                actualPath = path
            info = tardis.getFileInfoByPath(actualPath, bset)
            if info:
                retcode += recoverObject(r, info, bset, outputdir, path)
            else:
                logger.error("Could not recover info for %s", i)
                retcode += 1

    return retcode

if __name__ == "__main__":
    sys.exit(main())
