#!/usr/bin/env python

import os
import sys
import time
import re
import argparse
import logging
import copy
import subprocess
import tempfile
import shutil
import atexit
from pprint import pformat # FIXME remote it

LOGZILLA_HOME="/home/logzilla"
LOGZILLA_SRC_PATH=LOGZILLA_HOME + "/" + "src"
GIT_REPO_URL="git@git.assembla.com:lz5.git"

logger = logging.getLogger('lz5.installer')

ssh_wrapper=None
dev_null=open('/dev/null', 'r')

def run_program(cmd, silent_fail=False, env=None):

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=dev_null, env=env, shell=True)
    logger.info("Running {}".format(cmd))
    output = ''
    while proc.poll() is None:
        line = proc.stdout.readline()
        if line != '':
            line = line.rstrip()
            logger.debug("> {}".format(line))
            output += line + "\n"
        else:
            time.sleep(0.1)
    if proc.returncode != 0:
        if silent_fail:
            raise Exception("status={}".format(proc.returncode))
        logger.error("command {} failed with status {}".format(
            cmd, proc.returncode))
        logger.error("output:\n{}".format(output))
        sys.exit(1)
    return output

def run_git(cmd):
    if not ssh_wrapper:
        prepare_ssh_wrapper()
    env = copy.deepcopy(os.environ)
    env['GIT_SSH'] = ssh_wrapper
    return run_program("git {}".format(cmd), env=env)

def run_puppet(cmd, args):
    env = copy.deepcopy(os.environ)
    env['FACTER_fqdn'] = args.fqdn
    env['FACTER_install_application'] = "false" if args.preset_only else "true"
    return run_program("puppet {}".format(cmd), env=env)

def prepare_ssh_wrapper():
    ssh_dir = tempfile.mkdtemp()
    atexit.register(lambda: shutil.rmtree(ssh_dir))

    os.chmod(ssh_dir, 0700)

    logger.info("Adding installer key")
    key_content = """
        -----BEGIN DSA PRIVATE KEY-----
        MIIBuwIBAAKBgQDfE92q7Ma3xuTYtM+byUOKCbNx14VsvpW0fnwjiGaysMhA90Ir
        vN55t/f504ebhgFVwNxKoZCqEA3xwuRQfuh2tZYFzSE8gpW38Qes6/SiwwITNLVA
        fumTlBB5vcJ8k2F0yg1WhtQ1sRBIJ2GsMKczf3nv9bQpM0S1oJE+zJAqyQIVAM7i
        +YcPt4tr+mNpS9GEo517PJC5AoGAIXm5XlcZs3v5OZMsPogPUPaZqXtC2+ddeYQf
        jNZllrIjMNxk2znUMmyJKfQSm203hbYpuSw32EcjG/+GBHlS+Bs5OVFvUOa5n8Qq
        jcojJLbYLM/KxgdFmHBgioFYbocjAZ9C1ESAyn2MJJgOrmYbO4Y/Pi58n3MOaLSP
        UK+vCJICgYBoaYyHDNeY5N5Jl1nxpPmug17gcNyGfqYhxvsJWgHR1bxcuLJqZ7rQ
        E87AHj1afLSHv8Q9xk0G9o0/tG5PTXCZ5dbEhvV/cVzXjyXYQU1NGZ1SnVsygpQH
        2viFtZWAE1jW7iXOBbMsIwORCVNV6tvbCk88sUe2fcvj9gi/xc1rrAIVALSOrbzl
        pmYuBwv6BzfplsdppA3N
        -----END DSA PRIVATE KEY-----
    """
    key_content = re.sub(r'^\s+', '', key_content.strip(), flags=re.M)

    key_path = "{}/id_dsa".format(ssh_dir)
    with open(key_path, "w") as f:
        f.write(key_content)
    os.chmod(key_path, 0600)
    logger.info("Key saved in {}".format(key_path))

    global ssh_wrapper
    ssh_wrapper = "{}/ssh_wrapper".format(ssh_dir)
    with open(ssh_wrapper, "w") as f:
        f.write("#!/bin/sh\n")
        f.write("ssh -i {} -o UserKnownHostsFile=/dev/null ".format(key_path) +
            "-o StrictHostKeyChecking=no $@\n")
    os.chmod(ssh_wrapper, 0700)

def clone_repo(dest_path="/home/logzilla/src", branch="master"):
    run_git("clone", "git@git.assembla.com:lz5.git", dest_path)
    os.chdir(dest_path)
    run_git("checkout", branch)
    run_git("submodule", "update", "--init")

def ensure_puppet_installed():
    try:
        puppet_version = run_program("puppet -V", silent_fail=True).strip()
        if puppet_version.startswith("3."):
            logger.debug("Found puppet v. {}".format(puppet_version))
            return
        else:
            raise Exception("Invalid puppet version: {}".format(puppet_version))
    except Exception as e:
        logger.debug("Exception {}".format(e))
        logger.info("Puppet version 3.x not found, installing")

    run_program("wget http://apt.puppetlabs.com/puppetlabs-release-trusty.deb")
    run_program("dpkg -i puppetlabs-release-trusty.deb")
    run_program("aptitude -q update")
    run_program("aptitude -q -y install puppet")

def ensure_git_installed():
    try:
        git_version = run_program("git --version", silent_fail=True).strip()
        return
    except Exception as e:
        logger.debug("Exception {}".format(e))
        logger.info("Git version 1.7.x not found, installing")

    run_program("aptitude -q -y install git")

def check_if_repo(path):
    return (
        os.path.exists(path + "/.git") and
        os.path.exists(path + "/vagrant/puppet/modules/logzilla/manifests/user.pp")
    )

def create_user_and_home(args):
    # if we don't have any repo available, then we need to clone some to
    # get files from puppet - we do it to some temporary dir that will
    # be deleted just after use
    if args.repo_path:
        repo_path = args.repo_path
        clean_on_exit = False
    else:
        repo_path = tempfile.mkdtemp()
        clean_on_exit = True
        logger.info("Cloning shallow repo to temporary dir {}"
            .format(repo_path))
        run_git("clone {} --depth 1 -b master {}".format(GIT_REPO_URL,
            repo_path))

    os.chdir(repo_path)
    logger.info("Running puppet apply for logzilla user and home")
    run_puppet("apply --modulepath=vagrant/puppet/modules " +
        "--verbose vagrant/puppet/manifests/user_home.pp", args)

    if clean_on_exit:
        os.chdir("/")
        shutil.rmtree(repo_path)

def update_or_clone_repo(args):

    if not os.path.exists(LOGZILLA_HOME):
        create_user_and_home(args)

    if os.path.exists(LOGZILLA_SRC_PATH):
        os.chdir(LOGZILLA_SRC_PATH)
        if not args.no_update:
            if re.match(r'[0-9a-f]{40}', args.branch):
                # not a branch, but particular commit
                run_git("fetch")
                run_git("checkout {}".format(args.branch))
            else:
                run_git("checkout {}".format(args.branch))
                run_git("pull")
            run_git("submodule sync")
            run_git("submodule update --init")
        return

    if args.repo_path:
        os.symlink(args.repo_path, LOGZILLA_SRC_PATH)
        return

    run_git("clone {} {}".format(GIT_REPO_URL, LOGZILLA_SRC_PATH))
    os.chdir(LOGZILLA_SRC_PATH)
    run_git("checkout {}".format(args.branch))
    run_git("submodule update --init")

def main():
    parser = argparse.ArgumentParser(
        description='Install logzilla on fresh ubuntu system. Can use existing ' +
            'repo (usefull for vagrant with shared folder), or get it ' +
            'from assembla git repo'
    )

    parser.add_argument('-b', '--branch', default="master",
        help='When retrieving git repo, checkout given branch.')

    parser.set_defaults(loglevel=logging.INFO)
    parser.add_argument('-q', '--quiet',
        dest='loglevel', action="store_const", const=logging.WARNING,
        help='Notify only on warnings and errors (be quiet).')
    parser.add_argument('-d', '--debug',
        dest='loglevel', action="store_const", const=logging.DEBUG,
        help='Provide even more detailed log on actions performed.')

    parser.add_argument('-r', '--remote',
        help='Run remotely on given host and optionally with given user.')

    parser.add_argument('-rp', '--repo-path',
        help='When given, then don\'t retrieve repo, use the one provided.')

    parser.add_argument('-nc', '--no-clone', action='store_true',
        help='When run from the repository, just use current repo ' +
            'instead of cloning new one.')

    parser.add_argument('-nu', '--no-update', action='store_true',
        help='Don\'t update repository with pull before running puppet.')

    parser.add_argument('--fqdn', default='localhost',
        help='Hostname for the new host.')

    parser.add_argument('-p', '--preset-only', action='store_true',
        help='Don\'t install app, only packages and basic configuration.')


    args = parser.parse_args()

    logging.basicConfig(
        level=args.loglevel,
        format='%(asctime)s [%(process)5d] %(name)-14s %(levelname)-6s %(message)s',
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("Logger initialized")

    if args.remote:
        run_program("scp {} {}:install.py".format(__file__, args.remote))
        pass_args = " ".join(sys.argv[1:])
        pass_args = re.sub(r'(^|\s)-?-(remote|r)(=|\s+)\S+\b', ' ', pass_args)
        ssh_cmd = "ssh {} 'sudo ./install.py {}'".format(args.remote, pass_args)
        logger.info("Running {}".format(ssh_cmd))
        sys.exit(subprocess.call(ssh_cmd, shell=True))

    if os.geteuid() != 0:
        logger.error("This script must be run as root")
        sys.exit(1)

    if args.no_clone:
        my_path = os.path.realpath(os.path.dirname(__file__))
        root_path = os.path.realpath(my_path + "/..")
        if not check_if_repo(root_path):
            logger.error("--no-clone enabled, but this script doesn't seem " +
                    "to be in git repo (root_path={})".format(root_path))
            sys.exit(1)
        args.repo_path = root_path

    if args.repo_path and not check_if_repo(args.repo_path):
        logger.error("--repo-path {} doesn't seem to point to the git repo"
            .format(args.repo_path))
        sys.exit(1)

    ensure_puppet_installed()
    ensure_git_installed()
    update_or_clone_repo(args)
    run_puppet("apply --modulepath=vagrant/puppet/modules " +
        "--verbose vagrant/puppet/manifests/site.pp", args)


main()
