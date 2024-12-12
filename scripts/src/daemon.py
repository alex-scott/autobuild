#!/usr/bin/python
"""
Autobuild: Easy and simple LOCAL CI tool

autobuild is a free software, licensed under GPL 3.0.

Copyright (C) 2024 Alexey Presnyakov
"""

import re
import subprocess
import time
import logging
import sys
import os
from inotify_simple import INotify, flags
from logging.handlers import RotatingFileHandler
import datetime

autobuildPath = os.path.realpath( os.path.dirname ( os.path.dirname(__file__) ) )

os.chdir(autobuildPath)

logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
mainLog = logging.getLogger("main")

daemon_log = os.getenv("DAEMON_LOG")
if daemon_log:
    if daemon_log[0] != '/': daemon_log = autobuildPath + "/" + daemon_log
    print("daemon log:", daemon_log)
    fh = RotatingFileHandler(daemon_log, 'a', maxBytes=16*1024,
                             backupCount=1)
else:
    fh = logging.NullHandler()

runLog = logging.getLogger("run")
runLog.addHandler(fh)
#print (  os.environ.get('GROUPID') )

webhook_directory = os.environ.get('WEBHOOK_DIRECTORY')
if not webhook_directory:
    raise FileNotFoundError( "WEBHOOK_DIRECTORY must be configured in .env" )
if webhook_directory[0] != '/': webhook_directory = autobuildPath + "/" + webhook_directory
webhook_directory = os.path.realpath(webhook_directory)
if not os.path.exists(webhook_directory) or not os.path.isdir(webhook_directory):
    time.sleep(1)
    raise FileExistsError("WEBHOOK_DIRECTORY must be exsits and be a directory")
if not os.access(webhook_directory, os.W_OK) or not os.access(webhook_directory, os.R_OK):
    raise FileExistsError("WEBHOOK_DIRECTORY must be writeable for user running daemon.py")

def run_file_task(nn : str):
    if not os.path.exists(autobuildPath + "/scripts/" + nn + ".yaml"):
        mainLog.error("unknown task,  no script found: %s", nn)
        runLog.error("unknown task,  no script found: %s", nn)
        return

    try:
        runLog.info("=============================================\n")
        runLog.info("           " + nn + "\n")
        runLog.info("=========== %s =============\n\n" % datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S") )
        ret = subprocess.run([ autobuildPath + '/autobuild.sh',  nn], text=True, capture_output=True)
        if ret.returncode == 0:
            runLog.info("\n##### Finished sucessfuly\n")
            runLog.info(ret.stderr)
            runLog.info(ret.stdout)
        else:
            runLog.error("\n#### Failed, exit code %d\n" % ret.returncode)
            runLog.error(ret.stderr)
            runLog.error(ret.stdout)
    except Exception as e:
        runLog.error("Error running script: %s", e)

def check_files():
    with os.scandir(webhook_directory) as it:
        for entry in it:
            mainLog.info("new watched file: %s", entry.name)
            os.remove(entry.path)
            if re.match("^[a-zA-Z0-9_.-]+$", entry.name):
                run_file_task(entry.name)


def update_html(): pass

def daemon_run():
    while True:
        while not os.path.exists(webhook_directory):
            time.sleep(1)
        lst = os.lstat(webhook_directory)
        dir_inode = lst.st_ino

        mainLog.debug("watching")

        inotify_files = INotify()
        watch_flags = flags.CREATE | flags.MODIFY | flags.CLOSE_WRITE | flags.DELETE_SELF | flags.DELETE
        inotify_files.add_watch(webhook_directory, watch_flags)

        while True:
            events = inotify_files.read(1000)
            if events:
                check_files()
            else:
                update_html()
                if not os.path.exists(webhook_directory):
                    mainLog.info("Directory removed")
                    break
                lst = os.lstat(webhook_directory)
                if lst.st_ino != dir_inode:
                    mainLog.info("Directory inode changed")
                    break



daemon_run()