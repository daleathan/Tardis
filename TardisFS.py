#!/usr/bin/python

import fuse

fuse.fuse_python_api = (0, 2)

from time import time

import stat    # for file properties
import os      # for filesystem modes (O_RDONLY, etc)
import errno   # for error number codes (ENOENT, etc)
               # - note: these must be returned as negatives
import sys
import argparse

sys.path.append("server")
import TardisDB
import CacheDir
import regenerate

# For profiling
import cProfile
import StringIO
import pstats


line = "--------------------------------------------------------------------------------------------------------------"

def dirFromList(list):
    """
    Return a properly formatted list of items suitable to a directory listing.
    [['a', 'b', 'c']] => [[('a', 0), ('b', 0), ('c', 0)]]
    """
    return [[(x, 0) for x in list]]

def getDepth(path):
    """
    Return the depth of a given path, zero-based from root ('/')
    """
    if path ==  '/':
        return 0
    else:
        return path.count('/')

def getParts(path):
    """
    Return the slash-separated parts of a given path as a list
    """
    if path == '/':
        return [['/']]
    else:
        return path.strip("/").split('/', 1)

class TardisFS(fuse.Fuse):
    """
    """
    profiler = None

    def __init__(self, *args, **kw):
        fuse.Fuse.__init__(self, *args, **kw)
        self.db="."

        self.parser.add_option(mountopt="db", help="Hi mom")
        self.parse(values=self, errex=1)
        print "DB: " , self.db
        print 'Init complete.'

        self.tardis = TardisDB.TardisDB(self.db)

    def fsinit(self):
        print "FSINIT()"

    def getattr(self, path):
        """
        - st_mode (protection bits)
        - st_ino (inode number)
        - st_dev (device)
        - st_nlink (number of hard links)
        - st_uid (user ID of owner)
        - st_gid (group ID of owner)
        - st_size (size of file, in bytes)
        - st_atime (time of most recent access)
        - st_mtime (time of most recent content modification)
        - st_ctime (platform dependent; time of most recent metadata change on Unix,
                    or the time of creation on Windows).
        """

        depth = getDepth(path) # depth of path, zero-based from root
        print line
        print '*** getattr', path, depth

        if depth == 0:
            # Fake the root
            st = fuse.Stat()
            st.st_mode = stat.S_IFDIR | 0755
            st.st_ino = 0
            st.st_dev = 0
            st.st_nlink = 32
            st.st_uid = 0
            st.st_gid = 0
            st.st_size = 4096
            st.st_atime = int(time())
            st.st_mtime = st.st_atime
            st.st_ctime = st.st_atime
            return st
        elif depth == 1:
            # Root directory contents
            lead = getParts(path)
            f = self.tardis.getBackupSetInfo(lead[0])
            if f:
                st = fuse.Stat()
                timestamp = float(f[1])
                st.st_mode = stat.S_IFDIR | 0755
                st.st_ino = 0
                st.st_dev = 0
                st.st_nlink = 4
                st.st_uid = 0
                st.st_gid = 0
                st.st_size = 4096
                st.st_atime = timestamp
                st.st_mtime = timestamp
                st.st_ctime = timestamp
                return st
        else:
            parts = getParts(path)
            (bset, timestamp) = self.tardis.getBackupSetInfo(parts[0])
            f = self.tardis.getFileInfoByPath(parts[1], bset)
            if f:
                st = fuse.Stat()
                st.st_mode = f["mode"]
                st.st_ino = f["inode"]
                st.st_dev = 0
                st.st_nlink = f["nlinks"]
                st.st_uid = f["uid"]
                st.st_gid = f["gid"]
                st.st_size = f["size"]
                st.st_atime = f["mtime"]
                st.st_mtime = f["mtime"]
                st.st_ctime = f["ctime"]
                return st
        return -errno.ENOENT


    def getdir(self, path):
        """
        return: [[('file1', 0), ('file2', 0), ... ]]
        """
        print line
        print '*** getdir', path
        return -errno.ENOSYS

    def readdir(self, path, offset):
        print line
        print '*** readdir', path, offset
        dirents = ['.', '..']

        depth = getDepth(path)
        if depth == 0:
            entries = self.tardis.listBackupSets()
        else:
            parts = getParts(path)
            (bset, timestamp) = self.tardis.getBackupSetInfo(parts[0])
            if depth == 1:
                entries = self.tardis.readDirectory(0, bset)
            else:
                parent = self.tardis.getFileInfoByPath(parts[1], bset)
                entries = self.tardis.readDirectory(parent["inode"], bset)

        dirents.extend([str(y["name"]) for y in entries])

        for e in dirents:
            yield fuse.Direntry(e)

    def mythread ( self ):
        print line
        print '*** mythread'
        return -errno.ENOSYS

    def chmod ( self, path, mode ):
        print '*** chmod', path, oct(mode)
        return -errno.ENOSYS

    def chown ( self, path, uid, gid ):
        print '*** chown', path, uid, gid
        return -errno.ENOSYS

    def fsync ( self, path, isFsyncFile ):
        print '*** fsync', path, isFsyncFile
        return -errno.ENOSYS

    def link ( self, targetPath, linkPath ):
        print '*** link', targetPath, linkPath
        return -errno.ENOSYS

    def mkdir ( self, path, mode ):
        print '*** mkdir', path, oct(mode)
        return -errno.ENOSYS

    def mknod ( self, path, mode, dev ):
        print '*** mknod', path, oct(mode), dev
        return -errno.ENOSYS

    def open ( self, path, flags ):
        print line
        print '*** open', path, flags
        depth = getDepth(path) # depth of path, zero-based from root

        if (depth < 2):
            return -errno.ENOENT

        parts = getParts(path)
        (bset, timestamp) = self.tardis.getBackupSetInfo(parts[0])
        if bset:
            f = self.tardis.getFileInfoByPath(parts[1], bset)
            if f:
                print f
                return 0

        return -errno.ENOENT


    def read ( self, path, length, offset ):
        print line
        print '*** read', path, length, offset
        return -errno.ENOSYS

    def readlink ( self, path ):
        print '*** readlink', path
        return -errno.ENOSYS

    def release ( self, path, flags ):
        print '*** release', path, flags
        return -errno.ENOSYS

    def rename ( self, oldPath, newPath ):
        print '*** rename', oldPath, newPath
        return -errno.ENOSYS

    def rmdir ( self, path ):
        print '*** rmdir', path
        return -errno.ENOSYS

    def statfs ( self ):
        print '*** statfs'
        return -errno.ENOSYS

    def symlink ( self, targetPath, linkPath ):
        print '*** symlink', targetPath, linkPath
        return -errno.ENOSYS

    def truncate ( self, path, size ):
        print '*** truncate', path, size
        return -errno.ENOSYS

    def unlink ( self, path ):
        print '*** unlink', path
        return -errno.ENOSYS

    def utime ( self, path, times ):
        print '*** utime', path, times
        return -errno.ENOSYS

    def write ( self, path, buf, offset ):
        print '*** write', path, buf, offset
        return -errno.ENOSYS

    def listxattr ( self, path, size ):
        print line
        print '*** listxattr', path, " :: ", size
        return 'checksum\0'

    def getxattr (self, path, attr, foobar ):
        print line
        print '*** getxattr', path, " :: ", attr, foobar
        return -errno.ENOSYS

if __name__ == "__main__":
    profiler = None

    profiler = cProfile.Profile()
    if profiler:
        profiler.enable()

    fs = TardisFS()
    fs.flags = 0
    fs.multithreaded = 0
    fs.main()

    if profiler:
        profiler.disable()
        s = StringIO.StringIO()
        sortby = 'cumulative'
        ps = pstats.Stats(profiler, stream=s).sort_stats(sortby)
        ps.print_stats()
        print s.getvalue()