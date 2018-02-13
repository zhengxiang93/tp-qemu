import logging
import os

from avocado.utils.git import get_repo
from autotest.client import utils
from virttest import data_dir

def run(test, params, env):

    """
    Compile Latest Linux Kernel

    1) clone linux git repo
    2) checkout the latest version
    3) clean and defconfig and make

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    get_repo("https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git",
             destination_dir="/tmp/linux")
    os.chdir("/tmp/linux")
    command = "git tag | grep '^v[0-9]\+\.[0-9]\+$' | sort --version-sort | tail -n 1"
    version = utils.system_output(command)
    logging.info("Building Linux %s..." % version)
    utils.system_output("git checkout %s" % version)
    utils.system_output("make distclean")
    utils.system_output("make defconfig")
    utils.system_output("make -j `nproc`")

    test.assertTrue(os.path.isfile("arch/arm64/boot/Image"), "Build Linux Failed!")
    utils.system_output("cp arch/arm64/boot/Image %s" % data_dir.get_data_dir())
    logging.info("Building Linux Success!")
