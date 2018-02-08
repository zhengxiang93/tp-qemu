import logging
import os

from avocado.utils.git import get_repo
from avocado.utils.process import system_output

def run(test, params, env):

    """
    KVM Unit Tests

    1) Get kvm-unit-tests git repo
    2) ./configure and make
    3) ./run_tests.sh

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    get_repo("https://git.kernel.org/pub/scm/virt/kvm/kvm-unit-tests.git",
             destination_dir="/tmp/kvm-unit-tests")
    os.chdir("/tmp/kvm-unit-tests")
    system_output("./configure")
    system_output("make")
    out = system_output("./run_tests.sh")

    lines = out.split('\n')
    for line in lines:
        test.assertTrue(line.find("PASS") != -1, "KVM Unit Test Failed!")
