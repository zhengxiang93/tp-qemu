import logging
import os

from avocado.utils.git import get_repo
from avocado.utils.process import system_output

def run(test, params, env):

    """
    KVM WS Tests

    1) Get kvm-ws-tests git repo
    2) make
    3) make tests

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    get_repo("https://git.kernel.org/pub/scm/linux/kernel/git/maz/kvm-ws-tests.git",
             destination_dir="/tmp/kvm-ws-tests")
    os.chdir("/tmp/kvm-ws-tests")
    system_output("make")
    system_output("make tests")
