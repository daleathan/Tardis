import sqlite3
import shutil
import logging
import os.path
import functools
#from datetime import datetime, time
import time


#CREATE TABLE IF NOT EXISTS Backups (
#    Name        CHARACTER UNIQUE,
#    Timestamp   CHARACTER,
#    Session     CHARACTER UNIQUE,
#    Completed   INTEGER,
#    BackupSet   INTEGER PRIMARY KEY AUTOINCREMENT
#);
#
#CREATE TABLE IF NOT EXISTS CheckSums (
#    Checksum    CHARACTER UNIQUE NOT NULL,
#    ChecksumId  INTEGER PRIMARY KEY AUTOINCREMENT,
#    Size        INTEGER,
#    Basis       CHARACTER,
#    FOREIGN KEY(Basis) REFERENCES CheckSums(Checksum)
#);
#
#CREATE INDEX IF NOT EXISTS CheckSumIndex ON CheckSums(Checksum);
#
#CREATE TABLE IF NOT EXISTS Files (
#    Name        CHARACTER NOT NULL,
#    BackupSet   INTEGER   NOT NULL,
#    Inode       INTEGER   NOT NULL,
#    Parent      INTEGER   NOT NULL,
#    ChecksumId  INTEGER,
#    Dir         INTEGER,
#    Size        INTEGER,
#    MTime       INTEGER,
#    CTime       INTEGER,
#    ATime       INTEGER,
#    Mode        INTEGER,
#    UID         INTEGER,
#    GID         INTEGER,
#    NLinks      INTEGER,
#    FOREIGN KEY(Checksum)  REFERENCES CheckSums(ID),
#    FOREIGN KEY(BackupSet) REFERENCES Backups(BackupSet)
#);
#
#CREATE INDEX IF NOT EXISTS FilesID ON Files(Parent ASC, Name ASC, BackupSet ASC);

def makeDict(cursor, row):
    if row != None and cursor != None and len(row) != 0:
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d
    else:
        return None

def addFields(x, y):
    return dict(y.items() + x)

def splitpath(path, maxdepth=20):
    (head, tail) = os.path.split(path)
    return splitpath(head, maxdepth - 1) + [ tail ] if maxdepth and head and head != path else [ head or tail ]

class TardisDB(object):
    logger = logging.getLogger("DB")
    conn = None
    cursor = None
    dbName = None
    currBackupSet = None

    def __init__(self, dbname, backup=True, prevSet=None):
        """ Initialize the connection to a per-machine Tardis Database"""
        self.logger.debug("Initializing connection to {}".format(dbname))
        self.dbName = dbname
        if backup:
            backup = dbname + ".bak"
            try:
                self.logger.debug("Backing up {}".format(dbname))
                shutil.copyfile(dbname, backup)
            except IOError:
                pass

        self.conn = sqlite3.connect(self.dbName)
        #self.conn.text_factory = str

        self.cursor = self.conn.cursor()
        if (prevSet):
            f = self.getBackupSetInfo(prevSet)
            if f:
                self.prevBackupName = f[0]
                self.prevBackupSet  = f[1]
            #self.cursor.execute = ("SELECT Name, BackupSet FROM Backups WHERE Name = :backup", {"backup": prevSet})
        else:
            (self.prevBackupName, self.prevBackupSet, self.prevBackupDate) = self.lastBackupSet()
            #self.cursor.execute("SELECT Name, BackupSet FROM Backups WHERE Completed = 1 ORDER BY BackupSet DESC LIMIT 1")

        #row = self.cursor.fetchone()
        #self.prevBackupName = row[0]
        #self.prevBackupSet = row[1]
        self.logger.info("Last Backup Set: {} {} ".format(self.prevBackupName, self.prevBackupSet))

        self.conn.execute("PRAGMA synchronous=false")

    def bset(self, current):
        if type(current) is bool:
            return self.currBackupSet if current else self.prevBackupSet
        else:
            return current

    def lastBackupSet(self):
        c = self.cursor.execute("SELECT Name as name, BackupSet as backupset, Timestamp as timestamp "
                                "FROM Backups WHERE Completed = 1 ORDER BY BackupSet DESC LIMIT 1")
        row = c.fetchone()
        if row:
            return row[0], row[1], row[2]
        else:
            return None, None, None

    def newBackupSet(self, name, session):
        """ Create a new backupset.  Set the current backup set to be that set. """
        c = self.cursor
        c.execute("INSERT INTO Backups (Name, Completed, Timestamp, Session) VALUES (:name, 0, :now, :session)",
                  {"name": name, "now": time.time(), "session": session})
        self.currBackupSet = c.lastrowid
        self.currBackupName = name
        self.conn.commit()
        self.logger.info("Created new backup set: {}: {} {}".format(self.currBackupSet, name, session))
        return self.currBackupSet

    def getFileInfoByName(self, name, parent, current=True):
        """ Lookup a file in a directory in the previous backup set"""
        backupset = self.bset(current)
        self.logger.debug("Looking up file by name {} {} {}".format(name, parent, self.prevBackupSet))
        c = self.cursor
        c.execute("SELECT "
                  "Name AS name, Inode AS inode, Dir AS dir, Parent AS parent, Files.Size AS size, "
                  "MTime AS mtime, CTime AS ctime, Mode AS mode, UID AS uid, GID AS gid, NLinks AS nlinks "
                  "FROM Files "
                  "JOIN Names ON Files.NameId = Names.NameId "
                  "WHERE Name = :name AND Parent = :parent AND BackupSet = :backup",
                  {"name": name, "parent": parent, "backup": backupset})
        return makeDict(c, c.fetchone())

    def getFileInfoByPath(self, path, current=False):
        """ Lookup a file by a full path. """
        ### TODO: Could be a LOT faster without the repeated calls to getFileInfoByName
        backupset = self.bset(current)
        self.logger.debug("Looking up file by path {} {}".format(path, backupset))
        parent = 0              # Root directory value
        info = None
        for name in splitpath(path):
            info = self.getFileInfoByName(name, parent, backupset)
            if info:
                parent = info["inode"]
            else:
                break
        return info

    def getFileInfoByInode(self, inode, current=False):
        backupset = self.bset(current)
        self.logger.debug("Looking up file by inode {} {}".format(inode, backupset))
        c = self.cursor
        c.execute("SELECT "
                  "Name AS name, Inode AS inode, Dir AS dir, Parent AS parent, Size AS size, "
                  "MTime AS mtime, CTime AS ctime, Mode AS mode, UID AS uid, GID AS gid, NLinks AS nlinks "
                  "FROM Files "
                  "JOIN Names ON Files.NameId = Names.NameId "
                  "WHERE Inode = :inode AND BackupSet = :backup",
                  {"inode": inode, "backup": backupset})
        return makeDict(c, c.fetchone())

    def getNewFileInfoByInode(self, inode):
        self.logger.debug("Looking up file by inode {} {}".format(inode, self.currBackupSet))
        c = self.cursor
        c.execute("SELECT "
                  "Name AS name, Inode AS inode, Dir AS dir, Parent AS parent, Files.Size AS size, "
                  "MTime AS mtime, CTime AS ctime, Mode AS mode, UID AS uid, GID AS gid, NLinks AS nlinks "
                  "FROM Files "
                  "JOIN Names ON Files.NameId = Names.NameId "
                  "WHERE Inode = :inode AND BackupSet = :backup",
                  {"inode": inode, "backup": self.currBackupSet})
        return makeDict(c, c.fetchone())

    def getFileInfoBySimilar(self, fileInfo, current=False):
        """ Find a file which is similar, namely the same size, inode, and mtime.  Identifies files which have moved. """
        backupset = self.bset(current)
        self.logger.debug("Looking up file for similar info")
        c = self.cursor
        temp = fileInfo.copy()
        temp["backup"] = backupset
        c.execute("SELECT "
                  "Name AS name, Inode AS inode, Dir AS dir, Parent AS parent, Size AS size, "
                  "MTime AS mtime, CTime AS ctime, Mode AS mode, UID AS uid, GID AS gid "
                  "FROM Files "
                  "JOIN Names ON Files.NameId = Names.NameId "
                  "WHERE Inode = :inode AND Mtime = :mtime AND Size = :size AND BackupSet >= :backup AND ChecksumId IS NOT NULL",
                  temp)
        return makeDict(c, c.fetchone())

    def copyChecksum(self, old_inode, new_inode):
        c = self.cursor
        c.execute("UPDATE Files SET ChecksumId = (SELECT CheckSumID FROM Files WHERE Inode = :oldInode AND BackupSet = :prev) "
                  "WHERE INode = :newInode AND BackupSet = :backup",
                  {"oldInode": old_inode, "newInode": new_inode, "prev": self.prevBackupSet, "backup": self.currBackupSet})

    def setChecksum(self, inode, checksum):
        self.cursor.execute("UPDATE Files SET ChecksumId = (SELECT ChecksumId FROM CheckSums WHERE CheckSum = :checksum) WHERE Inode = :inode AND BackupSet = :backup",
                  {"inode": inode, "checksum": checksum, "backup": self.currBackupSet})

    def getChecksumByInode(self, inode, current=True):
        backupset = self.bset(current)
        c = self.cursor.execute("SELECT "
                                "DISTINCT(Checksum) AS checksum "
                                "FROM Files JOIN CheckSums ON Files.ChecksumId = Checksums.ChecksumId "
                                "WHERE Files.INode = :inode AND Files.BackupSet = :backupset",
                                { "backupset" : backupset, "inode" : inode })
        row = c.fetchone()
        if row:
            return row["checksum"]
        else:
            return None

    def getChecksumByName(self, name, parent, current=False):
        backupset = self.bset(current)
        self.logger.debug("Looking up checksum for file {} {} {}".format(name, parent, backupset))
        c = self.conn.execute("SELECT CheckSums.CheckSum AS checksum "
                              "FROM Files "
                              "JOIN Names ON Files.NameID = Names.NameId "
                              "JOIN CheckSums ON Files.ChecksumId = CheckSums.ChecksumId "
                              "WHERE Names.Name = :name AND Files.Parent = :parent AND Files.BackupSet = :backup",
                              {"name": name, "parent": parent, "backup": backupset})
        row = c.fetchone()
        if row:
            return row[0]
        else:
            return None

    def getChecksumByPath(self, name, current=False):
        backupset = self.bset(current)
        self.logger.debug("Looking up checksum for path {} {}".format(name, backupset))
        f = self.getFileInfoByPath(name, current)
        if f:
            return self.getChecksumByName(f["name"], f["parent"], current)
        return None

    def insertFile(self, fileInfo, parent):
        self.logger.debug("Inserting file: {}".format(str(fileInfo)))
        temp = addFields({ "backup": self.currBackupSet, "parent": parent }, fileInfo)
        self.setNameId([temp])
        self.conn.execute("INSERT INTO Files "
                          "(NameId, BackupSet, Inode, Parent, Dir, Link, Size, MTime, CTime, ATime,  Mode, UID, GID, NLinks) "
                          "VALUES  "
                          "(:nameid, :backup, :inode, :parent, :dir, :link, :size, :mtime, :ctime, :atime, :mode, :uid, :gid, :nlinks)",
                  temp)

    def cloneDir(self, parent, new=True, old=False):
        newBSet = self.bset(new)
        oldBSet = self.bset(old)
        self.logger.debug("Cloning directory inode {} from {} to {}".format(parent, oldBSet, newBSet))
        self.cursor.execute("INSERT INTO Files "
                            "(NameId, BackupSet, Inode, Parent, Dir, Link, Size, MTime, CTime, ATime,  Mode, UID, GID, NLinks) "
                            "SELECT NameId, :new, Inode, Parent, Dir, Link, Size, MTime, CTime, ATime,  Mode, UID, GID, NLinks "
                            "FROM Files WHERE BackupSet = :old AND Parent = :parent",
                            {"new": newBSet, "old": oldBSet, "parent": parent})

    def cloneDirs(self, parents, new=True, old=False):
        newBSet = self.bset(new)
        oldBSet = self.bset(old)
        self.logger.debug("Cloning directory inodes {} from {} to {}".format(parents, oldBSet, newBSet))

        self.cursor.executemany("INSERT INTO Files "
                                "(NameId, BackupSet, Inode, Parent, Dir, Link, Size, MTime, CTime, ATime,  Mode, UID, GID, NLinks) "
                                "SELECT NameId, :new, Inode, Parent, Dir, Link, Size, MTime, CTime, ATime,  Mode, UID, GID, NLinks "
                                "FROM Files WHERE BackupSet = :old AND Parent = :parent",
                                map(lambda x:{"new": newBSet, "old": oldBSet, "parent": x}, parents))

    def setNameID(self, files):
        for f in files:
            c = self.cursor.execute("SELECT NameId FROM Names WHERE Name = :name", f)
            row = c.fetchone()
            if row:
                f["nameid"] = row[0]
            else:
                self.cursor.execute("INSERT INTO Names (Name) VALUES (:name)", f)
                f["nameid"] = self.cursor.lastrowid

    def insertFiles(self, files, parent):
        self.logger.debug("Inserting files: {}".format(len(files)))
        self.conn.execute("BEGIN")
        fields = {"backup": self.currBackupSet, "parent": parent}.items()
        f = functools.partial(addFields, fields)
        self.setNameID(files)
        
        self.conn.executemany("INSERT INTO Files "
                              "(NameId, BackupSet, Inode, Parent, Dir, Link, Size, MTime, CTime, ATime, Mode, UID, GID, NLinks) "
                              "VALUES "
                              "(:nameid, :backup, :inode, :parent, :dir, :link, :size, :mtime, :ctime, :atime, :mode, :uid, :gid, :nlinks)",
                              map(f, files))

    def insertChecksumFile(self, checksum, size=0, basis=None):
        self.logger.debug("Inserting checksum file: {}".format(checksum))
        c = self.cursor
        c.execute("INSERT INTO CheckSums (CheckSum, Size, Basis) "
                  "VALUES                (:checksum, :size, :basis)",
                  {"checksum": checksum, "size": size, "basis": basis })
        return c.lastrowid

    def getChecksumInfo(self, checksum):
        self.logger.debug("Getting checksum info on: {}".format(checksum))
        c = self.cursor
        c.execute("SELECT Checksum, Basis FROM Checksums WHERE CheckSum = :checksum", {"checksum": checksum})
        row = c.fetchone()
        return (row[0], row[1])

    def readDirectory(self, dirNode, current=False):
        backupset = self.bset(current)
        self.logger.debug("Reading directory values for {} {}".format(dirNode, backupset))
        c = self.cursor
        c.execute("SELECT "
                  "Name AS name, Inode AS inode, Dir AS dir, Parent AS parent, Size AS size, "
                  "MTime AS mtime, CTime AS ctime, Mode AS mode, UID AS uid, GID AS gid "
                  "FROM Files "
                  "JOIN Names ON Files.NameId = Names.NameId "
                  "WHERE Parent = :dirnode AND BackupSet = :backup",
                  {"dirnode": dirNode, "backup": backupset})
        for row in c.fetchall():
            yield makeDict(c, row)

    def getPathForFileByName(self, name, parent, current=False):
        backupSet = self.bset(current)
        self.logger.debug("Extracting path for file {} {} {}".format(name, parent, backupSet))
        return None

    def listBackupSets(self):
        c = self.conn.execute("SELECT "
                              "Name AS name, BackupSet AS backupset "
                              "FROM Backups")
        for row in c.fetchall():
            yield makeDict(c, row)

    def getBackupSetInfo(self, name):
        c = self.conn.execute("SELECT "
                              "BackupSet, Timestamp FROM Backups WHERE name = :name",
                              {"name": name})
        row = c.fetchone()
        if row:
            return row[0], row[1]
        else:
            return None

    def beginTransaction(self):
        self.cursor.execute("BEGIN")

    def completeBackup(self):
        self.cursor.execute("UPDATE Backups SET Completed = 1 WHERE BackupSet = :backup", {"backup": self.currBackupSet})
        self.commit()

    def commit(self):
        self.conn.commit()

    def __del__(self):
        self.logger.info("Closing DB: {}".format(self.dbName))
        if self.conn:
            if self.currBackupSet:
                self.conn.execute("UPDATE Backups SET EndTimestamp = :now WHERE BackupSet = :backup",
                                    { "now": time.time(), "backup": self.currBackupSet })
            self.conn.commit()
            self.conn.close()

if __name__ == "__main__":
    import sys
    import uuid
    x = TardisDB(sys.argv[1])
    x.newBackupSet(sys.argv[2], str(uuid.uuid1()))
    rec =  x.getFileInfoByName("File1", 1)
    print rec
    print x.getFileInfoByInode(2)
    info = {
        "name"  : "Dir",
        "inode" : 1,
        "dir"   : 0,
        "size"  : 1,
        "mtime" : 1111,
        "ctime" : 1111,
        "atime" : 1111,
        "mode"  : 666,
        "uid"   : 99,
        "gid"   : 100,
        "cksum" : None
        }
    x.insertFile(info)
    info = {
        "name"  : "File1",
        "inode" : 2,
        "dir"   : 1,
        "size"  : 1,
        "mtime" : 2222,
        "ctime" : 2222,
        "atime" : 2222,
        "mode"  : 444,
        "uid"   : 99,
        "gid"   : 100,
        "cksum" : None
        }
    x.insertFile(info)
    x.completeBackup()
    x.commit()
