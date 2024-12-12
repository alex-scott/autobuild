#!/usr/bin/python
"""
Autobuild: Easy and simple LOCAL CI tool

autobuild is a free software, licensed under GPL 3.0.

Copyright (C) 2024 Alexey Presnyakov
"""

import argparse
import pathlib
import re
import subprocess

from decouple import config

import os
import sys

import git
import yaml
import logging
import fasteners

import docker
import shutil

from dotenv import dotenv_values

from hashlib import sha256

BUILD_IMAGE = 'cgicentral/autobuild'


class _bgcolors_none:
    HEADER = ''
    OKBLUE = ''
    OKCYAN = ''
    OKGREEN = ''
    WARNING = ''
    FAIL = ''
    ENDC = ''
    BOLD = ''
    UNDERLINE = ''


class _bgcolors_tty:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


bgcolors = _bgcolors_tty() if sys.stdout.isatty() else _bgcolors_none()

logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
logging.getLogger('docker.auth').setLevel(logging.INFO)
logging.getLogger('docker.api.build').setLevel(logging.INFO)
logging.getLogger('docker.utils.config').setLevel(logging.INFO)
logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)

mainLog = logging.getLogger("main")

parser = argparse.ArgumentParser(description='Cloudless CI')
parser.add_argument('scripts',  type=str, nargs='*',
                    help='scripts to run (default - all)')
parser.add_argument('-f', '--force', action='store_true',
                    help='re-run scripts')
args = vars(parser.parse_args())
doForce : bool = args['force']

rootDir = pathlib.Path(os.path.dirname(sys.argv[0])).parent.absolute()

scriptsDir = pathlib.Path(config("SCRIPTS_DIR")).absolute()
workspaceDir = pathlib.Path(config("WORKSPACE_DIR")).absolute()
SKIP_STEPS  = config("SKIP_STEPS", default="", cast=str).split()


dclient = docker.from_env()

def emptydir(path):
    dclient.containers.run(
        image=BUILD_IMAGE,
        remove=True,  # after finish
        volumes=[
            path.__str__() + ':/opt/workspace',
        ],
        #command=['sh', '-c', 'rm -rf /opt/workspace/*'],
        command=['sh', '-c', 'find /opt/workspace/ -mindepth 1 -name . -o -prune -exec rm -rf -- {} +'],
    )
    shutil.rmtree(path)

class TaskBuilder:
    name: str
    wdir: pathlib.Path
    yml: dict
    log: logging
    git: git.Repo
    currentCommit: str

    def __init__(self, name: str, wdir: pathlib.Path, yml: dict):
        self.name = name
        self.wdir = wdir
        self.yml = yml
        self.log = logging.getLogger(self.name)

    def checkout_repo(self):
        rDir = self.wdir / 'repo'
        if not rDir.exists(): rDir.mkdir()
        rDirGit = rDir / 'HEAD'

        gitUrl = self.yml['git']
        if gitUrl in authYaml['git']:
            self.log.debug('adding ssh key from auth.yaml for repo %s', gitUrl)
            ret = subprocess.run(['ssh-add', '-'], text=True, input=authYaml['git'][gitUrl]['private'])
            ret.check_returncode()

        if not rDirGit.exists():
            git.Repo.clone_from(gitUrl, rDir, bare=True, mirror=True)
        self.git = git.Repo(rDir)
        self.git.git.fetch()

    def extract_source(self):
        sDir = self.wdir / 'source'
        if sDir.exists():
            emptydir(sDir)
        self.git.clone(sDir)
        git_repo = git.Repo(sDir)
        git_repo.git.checkout(self.yml['branch'])

    def run_container(self, image: str, cmds: str, volumes, env):
        try:
            container = dclient.containers.run(image,
                                               command=['/bin/sh', '-ex', '-c', '--', cmds],
                                               environment=env,
                                               working_dir='/opt/workspace',
                                               detach=True,
                                               network="host",
                                               # auto_remove=True,
                                               # remove=False,  # after finish
                                               volumes=volumes,
                                               )
            #
            apiclient = docker.APIClient(base_url='unix://var/run/docker.sock')
            llogs = apiclient.attach(container.id, stderr=True, stdout=True, stream=True)
            try:
                while True:
                    line = next(llogs).decode("utf-8")
                    # print(bgcolors.OKGREEN + line + bgcolors.ENDC)
                    self.log.info('DOCKER OUTPUT: %s', line)
            except StopIteration:
                pass

            ret = container.wait()
            if ret.get('Error') or ret['StatusCode'] != 0:
                self.log.error("Container failed, logs: %s", container.logs())
                raise Exception("Error running container %s" % ret)
        finally:
            container.remove();

    def build_image(self, step: str, yml: dict):
        tag = "autobuild__%s_%s" % (self.name, step)
        (image, logs) = dclient.images.build(
            path=yml['dockerfile'],
            labels={'autobuild_generated': 'true'},
        )
        image.tag(tag)
        for x in logs:
            if 'stream' in x:
                self.log.debug("BUILD IMAGE : %s", x['stream'])
        return tag

    def run_step(self, step: str, yml: dict):
        if 'dockerfile' in yml:
            yml['image'] = self.build_image(step, yml)

        if not 'image' in yml or not yml['image']:
            raise Exception("Either dockerfile or image must be specified in yml")

        if not 'script' in yml:
            raise Exception("Build script must be defined in yml")

        # https://docker-py.readthedocs.io/en/stable/containers.html

        cmds = "\n".join(yml['script'])

        volumes = [
            (self.wdir / 'source').__str__() + ':/opt/workspace',
        ]
        if 'cache' in yml:
            for p in yml['cache']['paths']:
                cache_dir = self.volPath('cache', p)
                p = pathlib.PurePosixPath(p)
                if not p.is_absolute():
                    p = pathlib.PurePosixPath('/opt/workspace/' + p.__str__())
                if not cache_dir.exists(): cache_dir.mkdir(0o755)
                volumes.append(cache_dir.__str__() + ':' + p.__str__())

        # volumes:
        # / github / workspace /:
        # local: output
        # create: true
        # empty: true
        if 'volumes' in yml:
            for mountTo in yml['volumes']:
                v = yml['volumes'][mountTo]
                if 'local' not in v:
                    v['local'] = mountTo
                localpath = pathlib.Path(v['local'])
                if not localpath.is_absolute():
                    localpath = pathlib.Path(self.wdir / v['local'])
                mountpath = pathlib.PurePosixPath(mountTo)
                if not mountpath.is_absolute():
                    mountpath = pathlib.PurePosixPath('/opt/workspace/' + mountTo)
                if 'create' in v and v['create']:
                    if not localpath.exists():
                        localpath.mkdir()
                if 'empty_local' in v and v['empty_local']:
                    if localpath.__str__().startswith(self.wdir.__str__()):
                        emptydir(localpath)
                    else:
                        raise Exception("Path %s is not relative to workspace dir" % localpath)
                volumes.append(localpath.__str__() + ':' + mountpath.__str__())
        self.log.debug('volumes mounts: %s' % volumes)

        #ev = "xx" for i in dotenv_values(rootDir / ".env").items()

        env = ["%s=%s" % (k,v) for k,v in dotenv_values(rootDir / ".env").items()]
        env += [
            "CI_PROJECT_DIR=/opt/workspace",
            "CI_COMMIT_SHA=" + self.currentCommit,
            "CI_BRANCH=" + self.git.active_branch.name.__str__(),
        ]

        if 'env' in yml:
            env += [ "%s=%s" % (k, v) for k, v in yml['env'].items() ]

        self.run_container(yml['image'], cmds=cmds, volumes=volumes, env=env)

    def volPath(self, type: str, name: str):
        cacheId = "%s__%s" % (type, re.sub('[^0-9a-zA-Z]', '__', name))
        return self.wdir / cacheId

    def run_build(self):
        self.extract_source()
        ll = self.log
        try:
            for step in self.yml['steps']:
                if step in SKIP_STEPS:
                    self.log.debug("Step %s is skipped (SKIP_STEPS env)", step)
                    continue
                self.log = ll.getChild(step)
                self.run_step(step, self.yml[step])
        finally:
            self.log = ll

    def run(self):
        self.checkout_repo()

        br: git.Reference = self.git.branches[self.yml['branch']]
        self.currentCommit = br.commit.__str__()

        lastErrorPath = self.wdir / 'lastfailed'
        lastProcessedPath = self.wdir / 'lastcommit'
        if doForce:
            lastErrorPath.unlink(True)
            lastProcessedPath.unlink(True)

        if lastErrorPath.exists() and lastErrorPath.read_text('utf-8') == self.currentCommit:
            self.log.debug("last run of this commit failed, skipping run")
            return

        ll = self.wdir / 'log'
        fh = logging.FileHandler(ll)
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s')
        fh.setFormatter(formatter)
        self.log.addHandler(fh)

        if lastProcessedPath.exists() and self.currentCommit == lastProcessedPath.read_text("utf-8"):
            self.log.debug("no new commit %s (%s==%s)", self.name, self.currentCommit, lastProcessedPath.read_text("utf-8"))
            return

        ll = self.wdir / 'lastlog'
        ll.unlink(True)
        fh = logging.FileHandler(ll)
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s')
        fh.setFormatter(formatter)
        self.log.addHandler(fh)

        self.log.debug("REPO %s [%s] %s %s", br.name, br.commit, br.commit.author.email, br.commit.authored_datetime)

        lockPath = self.wdir / 'build.lock'
        lock = fasteners.InterProcessLock(lockPath.absolute())
        if not lock.acquire(False, 1):
            self.log.debug("Work in progress, skipping...")
            return

        self.log.info("Building %s", self.name)
        try:
            self.run_build()
            lastProcessedPath.write_text(self.currentCommit, 'utf-8')
        except Exception:
            self.log.exception("error in run_build")
            lastErrorPath.write_text(self.currentCommit, 'utf-8')
        finally:
            lock.release()
            lockPath.unlink(True)

def run_script(name):
    scriptPath = pathlib.Path(scriptsDir / (name + '.yaml'))
    if not scriptPath.exists():
        raise Exception("Script does not exists: %s" % scriptPath.__str__())
    ss = scriptPath.read_text()
    y: dict = yaml.safe_load(ss)
    name = scriptPath.stem
    y['name'] = name

    if 'disabled' in y and not doForce:
        if y['disabled']:
            mainLog.debug('SCRIPT %s is disabled, skipping', y['name'])
            return

    wDir = workspaceDir / name
    wDir.mkdir(0o755, False, True)

    b = TaskBuilder(name, wdir=wDir, yml=y)
    b.run()


def run_check():
    if args['scripts']:
        for name in args['scripts']:
            run_script(name)
    else:
        for scriptPath in scriptsDir.glob("*.yaml"):
            run_script(scriptPath.stem)


authYamlPath = pathlib.Path(rootDir / 'auth.yaml')
if authYamlPath.exists():
    authYaml = yaml.safe_load(authYamlPath.open('r'))
else:
    authYaml = dict()

run_check()
