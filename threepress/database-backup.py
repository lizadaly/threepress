#!/usr/bin/env python

import sys
import os.path
import os
import logging
from datetime import datetime
from settings import DIR_ROOT, DATABASE_NAME, DATABASE_USER, DATABASE_PASSWORD

logging.basicConfig(level=logging.WARN)

LOG_DIR = "%s/logs" % DIR_ROOT
MYSQL_CMD = 'mysqldump'
ZIP_CMD = 'zip'

def _setup():
    if not os.path.exists(LOG_DIR):
        logging.debug("Created log directory %s" % LOG_DIR)
        os.mkdir(LOG_DIR)
    else:
        logging.debug("Using log directory %s" % LOG_DIR)
    
def _backup_name():
    now = datetime.now()
    day_name = now.strftime("%A")
    file_name = "%s.sql" % day_name.lower() 
    logging.debug("Setting backup name for day name %s as %s" % (day_name, file_name))
    return file_name

def _run_backup(file_name):
    cmd = "%(mysqldump)s -u %(user)s --password=%(password)s %(database)s > %(log_dir)s/%(file)s" % {
        'mysqldump' : MYSQL_CMD,
        'user' : DATABASE_USER,
        'password' : DATABASE_PASSWORD,
        'database' : DATABASE_NAME,
        'log_dir' : LOG_DIR,
        'file': file_name}
    logging.debug("Backing up with command %s " % cmd)
    return os.system(cmd)

def _zip_backup(file_name):
    backup = "%s/%s" % (LOG_DIR, file_name)
    zipfile_name = "%s.zip" % (backup)

    if os.path.exists(zipfile_name):
        logging.debug("Removing previous zip archive %s" % zipfile_name)
        os.remove(zipfile_name)
    zip_cmds = {'zip' : ZIP_CMD, 'zipfile' : zipfile_name, 'file' : backup }

    # Create the backup
    logging.debug("Making backup as %s " % zipfile_name)
    os.system("%(zip)s -q -9 %(zipfile)s %(file)s" % zip_cmds)

    # Test our archive
    logging.debug("Testing zip archive")
    return os.system("%(zip)s -T -q %(zipfile)s" % zip_cmds)

def main(*args):
    _setup()
    file_name = _backup_name()
    _run_backup(file_name)
    return(_zip_backup(file_name))
        
    
if __name__ == '__main__':
    sys.exit(main(*sys.argv))    