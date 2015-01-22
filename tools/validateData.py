#! /usr/bin/python 

import hashlib
import os, os.path
import sys
import xattr
import hashlib
import sqlite3
import time
import logging

from Tardis import Regenerate, TardisDB, CacheDir, TardisCrypto

import progressbar as pb

checked = {}

try:
    for x in file("valid", "r"):
        x = x.rstrip()
        checked[x] = 1
except:
    pass

print "Loaded %d checksums." % (len(checked))

output = file('output', 'a')
valid = file('valid', 'a')

def validate(root, client, dbname, password):
    crypto = None
    token = None
    base = os.path.join(root, client)
    cache = CacheDir.CacheDir(base)
    if password:
        crypto = TardisCrypto.TardisCrypto(password, hostname=client)
        token = crypto.encryptFilename(client)
    db = TardisDB.TardisDB(os.path.join(base, dbname), token=token, backup=False)
    regen = Regenerate.Regenerator(cache, db, crypto)

    conn = db.conn

    cur = conn.execute("SELECT count(*) FROM CheckSums")
    row = cur.fetchone()
    num = row[0]
    print "Checksums: %d" % (num)

    cur = conn.execute("SELECT Checksum FROM CheckSums ORDER BY Size ASC, Checksum ASC");
    pbar = pb.ProgressBar(widgets=[pb.Percentage(), ' ', pb.Counter(), ' ', pb.Bar(), ' ', pb.ETA(), ' ', pb.Timer() ], maxval=num)
    pbar.start()

    row = cur.fetchone()
    i = 1
    while row is not None:
        pbar.update(i)
        i += 1
        try:
            checksum = row['Checksum']
            try:
                if not checksum in checked:
                    f = regen.recoverChecksum(checksum)
                    if f:
                        m = hashlib.md5()
                        d = f.read(128 * 1024)
                        while d:
                            m.update(d)
                            d = f.read(128 * 1024)
                        res = m.hexdigest()
                        if res != checksum:
                            print "Checksums don't match.  Expected: %s, result %s" % (checksum, res)
                            checked[checksum] = 0
                            output.write(checksum + '\n')
                            output.flush()
                        else:
                            checked[checksum] = 1
                            valid.write(checksum + "\n")
            except Exception as e:
                print "Caught exception processing %s: %s" % (checksum, str(e))
                output.write(checksum + '\n')
                output.flush()

            row = cur.fetchone()
        except sqlite3.OperationalError as e:
            print "Caught operational error.  DB is probably locked.  Sleeping for a bit"
            time.sleep(90)
    pbar.finish()

if __name__ == "__main__":
    root = "/nfs/test"
    client = "linux.koldware.com"
    dbname = "tardis.db"
    password = None

    logging.basicConfig(level=logging.INFO)

    validate(root, client, dbname, password)