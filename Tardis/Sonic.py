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

import logging
import argparse
import os
import os.path
import sys
import time
import datetime
import pprint
import urlparse
import srp

import parsedatetime
import passwordmeter

import Tardis
import Tardis.Util as Util
import Tardis.Defaults as Defaults
import Tardis.TardisDB as TardisDB
import Tardis.TardisCrypto as TardisCrypto
import Tardis.CacheDir as CacheDir
import Tardis.RemoteDB as RemoteDB
import Tardis.Config as Config

current      = Defaults.getDefault('TARDIS_RECENT_SET')
pwStrMin     = Defaults.getDefault('TARDIS_PW_STRENGTH')

# Config keys which can be gotten or set.
configKeys = ['Formats', 'Priorities', 'KeepDays', 'ForceFull', 'SaveFull', 'MaxDeltaChain', 'MaxChangePercent', 'VacuumInterval', 'AutoPurge', 'Disabled', 'SaveConfig']
# Extra keys that we print when everything is requested
sysKeys    = ['ClientID', 'SchemaVersion', 'FilenameKey', 'ContentKey']

minPwStrength = 0
logger = None
args = None

def getDB(crypt, password, new=False, allowRemote=True):
    loc = urlparse.urlparse(args.database)
    # This is basically the same code as in Util.setupDataConnection().  Should consider moving to it.
    if (loc.scheme == 'http') or (loc.scheme == 'https'):
        if not allowRemote:
            raise Exception("This command cannot be executed remotely.  You must execute it on the server directly.")
        # If no port specified, insert the port
        if loc.port is None:
            netloc = loc.netloc + ":" + Defaults.getDefault('TARDIS_REMOTE_PORT')
            dbLoc = urlparse.urlunparse((loc.scheme, netloc, loc.path, loc.params, loc.query, loc.fragment))
        else:
            dbLoc = args.database
        tardisdb = RemoteDB.RemoteDB(dbLoc, args.client)
        cache = tardisdb
    else:
        basedir = os.path.join(args.database, args.client)
        if not args.dbdir:
            dbdir = os.path.join(args.database, args.client)
        else:
            dbdir = os.path.join(args.dbdir, args.client)
        dbfile = os.path.join(dbdir, args.dbname)
        if new and os.path.exists(dbfile):
            raise Exception("Database for client %s already exists." % (args.client))

        cache = CacheDir.CacheDir(basedir, 2, 2, create=new)
        schema = args.schema if new else None
        tardisdb = TardisDB.TardisDB(dbfile, backup=False, initialize=schema)

    if password:
        Util.authenticate(tardisdb, args.client, password)

    return (tardisdb, cache)

def createClient(crypt, password):
    try:
        (db, _) = getDB(None, None, True, allowRemote=False)
        if crypt:
            setPassword(crypt, password)
        return 0
    except Exception as e:
        logger.error(str(e))
        return 1

def setPassword(crypt, password):
    try:
        # Must be no token specified yet
        (db, _) = getDB(None, None)
        crypt.genKeys()
        (f, c) = crypt.getKeys()
        (salt, vkey) = srp.create_salted_verification_key(args.client, password)
        if args.keys:
            db.beginTransaction()
            db.setSrpValues(salt, vkey)
            Util.saveKeys(args.keys, db.getConfigValue('ClientID'), f, c)
            db.commit()
        else:
            db.setKeys(salt, vkey, f, c)
        return 0
    except TardisDB.NotAuthenticatedException:
        logger.error('Client %s already has a password', args.client)
    except Exception as e:
        logger.error(str(e))
        return 1

def changePassword(crypt, crypt2, oldpw, newpw):
    try:
        (db, _) = getDB(crypt, oldpw)

        # Load the keys, and insert them into the crypt object, to decyrpt them
        if args.keys:
            (f, c) = Util.loadKeys(args.keys, db.getConfigValue('ClientID'))
        else:
            (f, c) = db.getKeys()
        crypt.setKeys(f, c)

        # Grab the keys from one crypt object.
        # Need to do this because getKeys/setKeys assumes they're encrypted, and we need the raw
        # versions
        crypt2._filenameKey = crypt._filenameKey
        crypt2._contentKey  = crypt._contentKey
        # Now get the encrypted versions
        (f, c) = crypt2.getKeys()

        (salt, vkey) = srp.create_salted_verification_key(args.client, newpw)

        if args.keys:
            db.beginTransaction()
            db.setSrpValues(salt, vkey)
            Util.saveKeys(args.keys, db.getConfigValue('ClientID'), f, c)
            db.commit()
        else:
            db.setKeys(salt, vkey, f, c)
        return 0
    except Exception as e:
        logger.error(str(e))
        return 1

def moveKeys(db, crypt):
    try:
        if args.keys is None:
            logger.error("Must specify key file for key manipulation")
            return 1
        clientId = db.getConfigValue('ClientID')
        salt, vkey = db.getSrpValues()
        #(db, _) = getDB(crypt)
        if args.extract:
            (f, c) = db.getKeys()
            if not (f and c):
                raise Exception("Unable to retrieve keys from server.  Aborting.")
            Util.saveKeys(args.keys, clientId, f, c)
            if args.deleteKeys:
                db.setKeys(salt, vkey, None, None)
        elif args.insert:
            (f, c) = Util.loadKeys(args.keys, clientId)
            logger.info("Keys: F: %s C: %s", f, c)
            if not (f and c):
                raise Exception("Unable to retrieve keys from key database.  Aborting.")
            db.setKeys(salt, vkey, f, c)
            if args.deleteKeys:
                Util.saveKeys(args.keys, clientId, None, None)
        return 0
    except Exception as e:
        logger.error(e)
        logger.exception(e)
        return 1

def listBSets(db):
    try:
        last = db.lastBackupSet()
        for bset in db.listBackupSets():
            t = time.strftime("%d %b, %Y %I:%M:%S %p", time.localtime(float(bset['starttime'])))
            if bset['endtime'] is not None:
                duration = str(datetime.timedelta(seconds = (int(float(bset['endtime']) - float(bset['starttime'])))))
            else:
                duration = ''
            completed = 'Comp' if bset['completed'] else 'Incomp'
            full      = 'Full' if bset['full'] else 'Delta'
            isCurrent = current if bset['backupset'] == last['backupset'] else ''
            size = Util.fmtSize(bset['bytesreceived'], formats=['', 'KB', 'MB', 'GB', 'TB'])

            print "%-30s %-4d %-6s %3d  %-5s  %s  %-7s %6s %5s %8s  %s" % (bset['name'], bset['backupset'], completed, bset['priority'], full, t, duration, bset['filesfull'], bset['filesdelta'], size, isCurrent)
    except Exception as e:
        logger.error(e)
        logger.exception(e)
        return 1

# cache of paths we've already calculated.
# the root (0, 0,) is always prepopulated
_paths = {(0, 0): '/'}

def _decryptFilename(name, crypt):
    return crypt.decryptFilename(name) if crypt else name

def _path(db, crypt, bset, inode):
    global _paths
    if inode in _paths:
        return _paths[inode]
    else:
        fInfo = db.getFileInfoByInode(inode, bset)
        if fInfo:
            parent = (fInfo['parent'], fInfo['parentdev'])
            prefix = _path(db, crypt, bset, parent)

            name = _decryptFilename(fInfo['name'], crypt)
            path = os.path.join(prefix, name)
            _paths[inode] = path
            return path
        else:
            return ''

def listFiles(db, crypt):
    #print args
    info = getBackupSet(db, args.backup, args.date, defaultCurrent=True)
    #print info, info['backupset']
    lastDir = '/'
    lastDirInode = (-1, -1)
    bset = info['backupset']
    files = db.getNewFiles(info['backupset'], args.previous)
    for fInfo in files:
        name = _decryptFilename(fInfo['name'], crypt)
        
        if not args.dirs and fInfo['dir']:
            continue
        dirInode = (fInfo['parent'], fInfo['parentdev'])
        if dirInode == lastDirInode:
            path = lastDir
        else:
            path = _path(db, crypt, bset, dirInode)
            lastDirInode = dirInode
            lastDir = path
            if not args.fullname:
                print "%s:" % (path)
        if args.status:
            status = '[New]   ' if fInfo['chainlength'] == 0 else '[Delta] '
        else:
            status = ''
        if args.fullname:
            name = os.path.join(path, name)

        if args.long:
            mode  = Util.filemode(fInfo['mode'])
            group = Util.getGroupName(fInfo['gid'])
            owner = Util.getUserId(fInfo['uid'])
            mtime = Util.formatTime(fInfo['mtime'])
            if fInfo['size'] is not None:
                if args.human:
                    size = "%8s" % Util.fmtSize(fInfo['size'], formats=['','KB','MB','GB', 'TB', 'PB'])
                else:
                    size = "%8d" % int(fInfo['size'])
            else:
                size = ''           
            print'  %s%9s %-8s %-8s %8s %12s' % (status, mode, owner, group, size, mtime),
            if args.cksums:
                print ' %32s ' % (fInfo['checksum'] or ''),
            if args.chnlen:
                print ' %4s ' % (fInfo['chainlength']),
            if args.inode:
                print ' %-16s ' % ("(%s, %s)" % (fInfo['device'], fInfo['inode'])),

            print name
        else:
            print "    %s" % status,
            if args.cksums:
                print ' %32s ' % (fInfo['checksum'] or ''),
            if args.chnlen:
                print ' %4s ' % (fInfo['chainlength']),
            if args.inode:
                print ' %-16s ' % ("(%s, %s)" % (fInfo['device'], fInfo['inode'])),
            print name


def _bsetInfo(db, info):
    print "Backupset       : %s (%d)" % ((info['name']), info['backupset'])
    print "Completed       : %s" % ('True' if info['completed'] else 'False')
    t = time.strftime("%d %b, %Y %I:%M:%S %p", time.localtime(float(info['starttime'])))
    print "StartTime       : %s" % (t)
    if info['endtime'] is not None:
        t = time.strftime("%d %b, %Y %I:%M:%S %p", time.localtime(float(info['endtime'])))
        duration = str(datetime.timedelta(seconds = (int(float(info['endtime']) - float(info['starttime'])))))
        print "EndTime         : %s" % (t)
        print "Duration        : %s" % (duration)
    print "SW Versions     : C:%s S:%s" % (info['clientversion'], info['serverversion'])
    print "Client IP       : %s" % (info['clientip'])
    details = db.getBackupSetDetails(info['backupset'])
    (files, dirs, size, newInfo, endInfo) = details
    print "Files           : %d" % (files)
    print "Directories     : %d" % (dirs)
    print "Total Size      : %s" % (Util.fmtSize(size))

    print "New Files       : %d" % (newInfo[0])
    print "New File Size   : %s" % (Util.fmtSize(newInfo[1]))
    print "New File Space  : %s" % (Util.fmtSize(newInfo[2]))

    print "Purgeable Files : %d" % (endInfo[0])
    print "Purgeable Size  : %s" % (Util.fmtSize(endInfo[1]))
    print "Purgeable Space : %s" % (Util.fmtSize(endInfo[2]))

def bsetInfo(db):
    printed = False
    if args.backup or args.date:
        info = getBackupSet(db, args.backup, args.date)
        if info:
            _bsetInfo(db, info)
            printed = True
    else:
        first = True
        for info in db.listBackupSets():
            if not first:
                print "------------------------------------------------"
            _bsetInfo(db, info)
            first = False
            printed = True
    if printed:
        print "\n * Purgeable numbers are estimates only"

def confirm():
    if not args.confirm:
        return True
    else:
        print "Proceed (y/n): ",
        yesno = sys.stdin.readline().strip().upper()
        return yesno == 'YES' or yesno == 'Y'

def purge(db, cache):
    bset = getBackupSet(db, args.backup, args.date, True)
    if bset is None:
        logger.error("No backup set found")
        sys.exit(1)
    # List the sets we're going to delete
    if args.incomplete:
        pSets = db.listPurgeIncomplete(args.priority, bset['endtime'], bset['backupset'])
    else:
        pSets = db.listPurgeSets(args.priority, bset['endtime'], bset['backupset'])

    names = [x['name'] for x in pSets]
    logger.debug("Names: %s", names)
    if len(names) == 0:
        print "No matching sets"
        return

    print "Sets to be deleted:"
    pprint.pprint(names)

    if confirm():
        if args.incomplete:
            (filesDeleted, setsDeleted) = db.purgeIncomplete(args.priority, bset['endtime'], bset['backupset'])
        else:
            (filesDeleted, setsDeleted) = db.purgeSets(args.priority, bset['endtime'], bset['backupset'])
        print "Purged %d sets, containing %d files" % (setsDeleted, filesDeleted)
        removeOrphans(db, cache)

def deleteBsets(db, cache):
    if not args.backups:
        logger.error("No backup sets specified")
        sys.exit(0)
    bsets = []
    for i in args.backups:
        bset = getBackupSet(db, i, None)
        if bset is None:
            logger.error("No backup set found for %s", i)
            sys.exit(1)
        bsets.append(bset)

    names = [b['name'] for b in bsets]
    print "Sets to be deleted: %s" % (names)
    if confirm():
        filesDeleted = 0
        for bset in bsets:
            filesDeleted = filesDeleted + db.deleteBackupSet(bset['backupset'])
        print "Deleted %d files" % (filesDeleted)
        removeOrphans(db, cache)

def removeOrphans(db, cache):
    if hasattr(cache, 'removeOrphans'):
        r = cache.removeOrphans()
        logger.debug("Remove Orphans: %s %s", type(r), r)
        count = r['count']
        size = r['size']
        rounds = r['rounds']
    else:
        count, size, rounds = Util.removeOrphans(db, cache)
    print "Removed %d orphans, for %s, in %d rounds" % (count, Util.fmtSize(size), rounds)

def _printConfigKey(db, key):
    value = db.getConfigValue(key)
    print "%-18s: %s" % (key, value)


def getConfig(db):
    keys = args.configKeys
    if keys is None:
        keys = configKeys
        if args.sysKeys:
            keys = sysKeys + keys

    for i in keys:
        _printConfigKey(db, i)

def setConfig(db):
    print "Old Value: ",
    _printConfigKey(db, args.key)
    db.setConfigValue(args.key, args.value)

def parseArgs():
    global args, minPwStrength

    parser = argparse.ArgumentParser(description='Tardis Sonic Screwdriver Utility Program', fromfile_prefix_chars='@', formatter_class=Util.HelpFormatter, add_help=False)

    (args, remaining) = Config.parseConfigOptions(parser)
    c = Config.config
    t = args.job

    # Shared parser
    bsetParser = argparse.ArgumentParser(add_help=False)
    bsetgroup = bsetParser.add_mutually_exclusive_group()
    bsetgroup.add_argument("--backup", "-b", help="Backup set to use", dest='backup', default=None)
    bsetgroup.add_argument("--date", "-d",   help="Use last backupset before date", dest='date', default=None)

    purgeParser = argparse.ArgumentParser(add_help=False)
    purgeParser.add_argument('--priority',       dest='priority',   default=0, type=int,                   help='Maximum priority backupset to purge')
    purgeParser.add_argument('--incomplete',     dest='incomplete', default=False, action='store_true',    help='Purge only incomplete backup sets')
    bsetgroup = purgeParser.add_mutually_exclusive_group()
    bsetgroup.add_argument("--date", "-d",     dest='date',       default=None,                            help="Purge sets before this date")
    bsetgroup.add_argument("--backup", "-b",   dest='backup',     default=None,                            help="Purge sets before this set")

    deleteParser = argparse.ArgumentParser(add_help=False)
    #deleteParser.add_argument("--backup", "-b",  dest='backup',     default=None,                          help="Purge sets before this set")
    deleteParser.add_argument("backups", nargs="*", default=None, help="Backup sets to delete")

    cnfParser = argparse.ArgumentParser(add_help=False)
    cnfParser.add_argument('--confirm',          dest='confirm', action=Util.StoreBoolean, default=True,   help='Confirm deletes and purges')

    keyParser = argparse.ArgumentParser(add_help=False)
    keyGroup = keyParser.add_mutually_exclusive_group(required=True)
    keyGroup.add_argument('--extract',          dest='extract', default=False, action='store_true',         help='Extract keys from database')
    keyGroup.add_argument('--insert',           dest='insert', default=False, action='store_true',          help='Insert keys from database')
    keyParser.add_argument('--delete',          dest='deleteKeys', default=False, action=Util.StoreBoolean, help='Delete keys from server or database')

    filesParser = argparse.ArgumentParser(add_help=False)
    filesParser.add_argument('--long', '-l',    dest='long', default=False, action=Util.StoreBoolean,           help='Long format')
    filesParser.add_argument('--fullpath', '-f',    dest='fullname', default=False, action=Util.StoreBoolean,   help='Print full path name in names')
    filesParser.add_argument('--previous',      dest='previous', default=False, action=Util.StoreBoolean,       help="Include files that first appear in the set, but weren't added here")
    filesParser.add_argument('--dirs',          dest='dirs', default=False, action=Util.StoreBoolean,           help='Include directories in list')
    filesParser.add_argument('--status',        dest='status', default=False, action=Util.StoreBoolean,         help='Include status (new/delta) in list')
    filesParser.add_argument('--human', '-H',   dest='human', default=False, action=Util.StoreBoolean,          help='Print sizes in human readable form')
    filesParser.add_argument('--checksums', '-c', dest='cksums', default=False, action=Util.StoreBoolean,       help='Print checksums')
    filesParser.add_argument('--chainlen', '-L', dest='chnlen', default=False, action=Util.StoreBoolean,        help='Print chainlengths')
    filesParser.add_argument('--inode', '-i',   dest='inode', default=False, action=Util.StoreBoolean,          help='Print inodes')

    common = argparse.ArgumentParser(add_help=False)
    Config.addPasswordOptions(common)
    Config.addCommonOptions(common)

    create = argparse.ArgumentParser(add_help=False)
    create.add_argument('--schema',                 dest='schema',          default=c.get(t, 'Schema'), help='Path to the schema to use (Default: %(default)s)')

    newPassParser = argparse.ArgumentParser(add_help=False)
    newpassgrp = newPassParser.add_argument_group("New Password specification options")
    npwgroup = newpassgrp.add_mutually_exclusive_group()
    npwgroup.add_argument('--newpassword',      dest='newpw', default=None, nargs='?', const=True,  help='Change to this password')
    npwgroup.add_argument('--newpassword-file', dest='newpwf', default=None,                        help='Read new password from file')
    npwgroup.add_argument('--newpassword-prog', dest='newpwp', default=None,                        help='Use the specified command to generate the new password on stdout')

    configKeyParser = argparse.ArgumentParser(add_help=False)
    configKeyParser.add_argument('--key',       dest='configKeys', choices=configKeys, action='append',    help='Configuration key to retrieve.  None for all keys')
    configKeyParser.add_argument('--sys',       dest='sysKeys', default=False, action=Util.StoreBoolean,   help='List System Keys as well as configurable ones')

    configValueParser = argparse.ArgumentParser(add_help=False)
    configValueParser.add_argument('--key',     dest='key', choices=configKeys, required=True,      help='Configuration key to set')
    configValueParser.add_argument('--value',   dest='value', required=True,                        help='Configuration value to access')

    subs = parser.add_subparsers(help="Commands", dest='command')
    subs.add_parser('create',       parents=[common, create],                               help='Create a client database')
    subs.add_parser('setpass',      parents=[common],                                       help='Set a password')
    subs.add_parser('chpass',       parents=[common, newPassParser],                        help='Change a password')
    subs.add_parser('keys',         parents=[common, keyParser],                            help='Move keys to/from server and key file')
    subs.add_parser('list',         parents=[common],                                       help='List backup sets')
    subs.add_parser('files',        parents=[common, filesParser, bsetParser],              help='List new files in a backup set')
    subs.add_parser('info',         parents=[common, bsetParser],                           help='Print info on backup sets')
    subs.add_parser('purge',        parents=[common, purgeParser, cnfParser],               help='Purge old backup sets')
    subs.add_parser('delete',       parents=[common, deleteParser, cnfParser],              help='Delete a backup set')
    subs.add_parser('orphans',      parents=[common],                                       help='Delete orphan files')
    subs.add_parser('getconfig',    parents=[common, configKeyParser],                      help='Get Config Value')
    subs.add_parser('setconfig',    parents=[common, configValueParser],                    help='Set Config Value')

    parser.add_argument('--verbose', '-v',      dest='verbose', default=0, action='count', help='Be verbose.  Add before usb command')
    parser.add_argument('--version',            action='version', version='%(prog)s ' + Tardis.__versionstring__,    help='Show the version')
    parser.add_argument('--help', '-h',         action='help')

    args = parser.parse_args(remaining)

    # And load the required strength for new passwords.  NOT specifiable on the command line.
    #minPwStrength = c.getfloat(t, 'PwStrMin')
    return args

def getBackupSet(db, backup, date, defaultCurrent=False):
    bInfo = None
    if date:
        cal = parsedatetime.Calendar()
        (then, success) = cal.parse(date)
        if success:
            timestamp = time.mktime(then)
            logger.debug("Using time: %s", time.asctime(then))
            bInfo = db.getBackupSetInfoForTime(timestamp)
            if bInfo and bInfo['backupset'] != 1:
                bset = bInfo['backupset']
                logger.debug("Using backupset: %s %d", bInfo['name'], bInfo['backupset'])
            else:
                logger.critical("No backupset at date: %s (%s)", date, time.asctime(then))
                bInfo = None
        else:
            logger.critical("Could not parse date string: %s", date)
    elif backup:
        try:
            bset = int(backup)
            logger.debug("Using integer value: %d", bset)
            bInfo = db.getBackupSetInfoById(bset)
        except ValueError:
            logger.debug("Using string value: %s", backup)
            if backup == current:
                bInfo = db.lastBackupSet()
            else:
                bInfo = db.getBackupSetInfo(backup)
            if not bInfo:
                logger.critical("No backupset at for name: %s", backup)
    elif defaultCurrent:
        bInfo = db.lastBackupSet()
    return bInfo

def checkPasswordStrength(password):
    strength, improvements = passwordmeter.test(password)
    if strength < minPwStrength:
        logger.error("Password too weak: %f", strength)
        for i in improvements:
            logger.info("    %s", improvements[i])
        return False
    else:
        return True

def main():
    global logger
    parseArgs()
    logger = Util.setupLogging(args.verbose)

    # Commands which cannot be executed on remote databases
    allowRemote = args.command not in ['create']

    db      = None
    crypt   = None
    cache   = None
    try:
        confirm = args.command in ['setpass', 'create']
        allowNone = args.command != 'setpass'
        try:
            password = Util.getPassword(args.password, args.passwordfile, args.passwordprog, prompt="Password for %s: " % (args.client), allowNone=allowNone, confirm=confirm)
        except Exception as e:
            logger.critical(str(e))
            return -1
            
        if confirm and password and not checkPasswordStrength(password):
            return -1

        if password:
            crypt = TardisCrypto.TardisCrypto(password, args.client)
            args.password = None

        if args.command == 'create':
            return createClient(crypt, password)

        if args.command == 'setpass':
            if not crypt:
                logger.error("No password specified")
                return -1
            return setPassword(crypt, password)

        if args.command == 'chpass':
            try:
                newpw = Util.getPassword(args.newpw, args.newpwf, args.newpwp, prompt="New Password for %s: " % (args.client), allowNone=False, confirm=True)
            except Exception as e:
                logger.critical(str(e))
                return -1
            if password and not checkPasswordStrength(newpw):
                return -1

            crypt2 = TardisCrypto.TardisCrypto(newpw, args.client)
            return changePassword(crypt, crypt2, password, newpw)

        try:
            (db, cache) = getDB(crypt, password, allowRemote=allowRemote)
            if crypt:
                if args.keys:
                    (f, c) = Util.loadKeys(args.keys, db.getConfigValue('ClientID'))
                else:
                    (f, c) = db.getKeys()
                crypt.setKeys(f, c)
        except Exception as e:
            logger.critical("Unable to connect to database: %s", e)
            sys.exit(1)

        if args.command == 'keys':
            return moveKeys(db, crypt)
        elif args.command == 'list':
            return listBSets(db)
        elif args.command == 'files':
            return listFiles(db, crypt)
        elif args.command == 'info':
            return bsetInfo(db)
        elif args.command == 'purge':
            return purge(db, cache)
        elif args.command == 'delete':
            return deleteBsets(db, cache)
        elif args.command == 'getconfig':
            return getConfig(db)
        elif args.command == 'setconfig':
            return setConfig(db)
        elif args.command == 'orphans':
            return removeOrphans(db, cache)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error("Caught exception: %s", str(e))
        logger.exception(e)
    finally:
        if db:
            db.close()

if __name__ == "__main__":
    main()
