import sqlite3
import sys
import os.path
from Tardis import CacheDir

version = 7

if len(sys.argv) > 1:
    db = sys.argv[1]
else:
    db = "tardis.db"

conn = sqlite3.connect(db)

s = conn.execute('SELECT Value FROM Config WHERE Key = "SchemaVersion"')
t = s.fetchone()
if int(t[0]) != version:
    print("Invalid database schema version: {}".format(t[0]))
    sys.exit(1)

conn.execute("ALTER TABLE Backups ADD COLUMN FilesFull INTEGER")
conn.execute("ALTER TABLE Backups ADD COLUMN FilesDelta INTEGER")
conn.execute("ALTER TABLE Backups ADD COLUMN BytesReceived INTEGER")

conn.execute("ALTER TABLE CheckSums ADD COLUMN Encrypted INTEGER")

conn.execute("UPDATE CheckSums SET Encrypted = 1 WHERE InitVector IS NOT NULL")
conn.execute("UPDATE CheckSums SET Encrypted = 0 WHERE InitVector IS NULL")

conn.execute('INSERT OR REPLACE INTO Config (Key, Value) VALUES ("SchemaVersion", ?)', str(version + 1))

conn.commit()