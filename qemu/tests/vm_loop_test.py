import logging

from autotest.client.shared import error
from autotest.client import utils
from avocado.utils.git import get_repo

from virttest import utils_test, env_process
from virttest.staging import utils_memory


def run(test, params, env):
    """
    VM Loop Test
    Boot VMs and run hackbanch and cyclictest inside VM parallel.

    1) Boot up VMs:
    2) Run hackbanch and cyclictest inside every guest.

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """
    def vm_loop_test(session, vm_name):
        vm = env.get_vm(vm_name)
        ip = vm.get_address()
        git_url = params.get("git_url")
        dest_dir = params.get("dest_dir")
        get_repo(git_url, destination_dir=dest_dir)

        hackbanch_groups = params.get("hackbanch_groups", "500")
        hackbanch_loops = params.get("hackbanch_loops", "100")
        host_path = dest_dir + "/hackbench.c"
        guest_path = "/root/hackbench.c"
        try:
            output = utils.system_output("md5sum %s" % host_path)
            logging.info("MD5 host: %s" % output)

            vm.copy_files_to(host_path, "/root/", timeout=60)
            status, output = session.cmd_status_output("md5sum %s" % guest_path, timeout=60)
            if status != 0:
                logging.error(output)
                raise error.TestFail("Fail to copy %s to guest %s" % (host_path, guest_path))
            else:
                logging.info("MD5 guest: %s" % output)
            
            status, output = session.cmd_status_output("gcc -o /root/hackbench -pthread %s" % guest_path, timeout=60)
            if status != 0:
                logging.error(output)
                raise error.TestFail("Fail to compile %s" % guest_path)
            
            status, output = session.cmd_status_output("./hackbench %s process %s" % (hackbanch_groups, hackbanch_loops), timeout=1200)
            if status != 0:
                logging.error(output)
                raise error.TestFail("Fail to run hackbench")

            status, output = session.cmd_status_output("cyclictest -t `nproc` -l 10000", timeout=1200)
            if status != 0:
                logging.error(output)
                raise error.TestFail("Fail to run cyclictest")
        finally:
            status, _ = utils_test.ping(ip, count=10, timeout=30)
            if status != 0:
                raise error.TestFail("vm no response, pls check serial log")

    guest_number = int(params.get("guest_number", "1"))

    if guest_number < 1:
        logging.warn("At least boot up one guest for this test,"
                     " set up guest number to 1")
        guest_number = 1

    for tag in range(1, guest_number):
        params["vms"] += " guest_%s" % tag

    params["start_vm"] = "yes"
    login_timeout = int(params.get("login_timeout", 360))

    env_process.preprocess(test, params, env)

    sessions_info = []
    for vm_name in params["vms"].split():
        vm = env.get_vm(vm_name)
        vm.verify_alive()
        session = vm.wait_for_login(timeout=login_timeout)
        if not session:
            raise error.TestFail("Could not log into guest %s" % vm_name)

        sessions_info.append([session, vm_name])

    # run vm loop test in vms
    try:
        logging.info("run vm loop test in vms")
        bg_threads = []
        for session_info in sessions_info:
            session = session_info[0]
            vm_name = session_info[1]
            bg_thread = utils_test.BackgroundTest(vm_loop_test,
                                                  (session, vm_name))
            bg_thread.start()
            bg_threads.append(bg_thread)

        completed = False
        while not completed:
            completed = True
            for bg_thread in bg_threads:
                if bg_thread.is_alive():
                    completed = False
    finally:
        try:
            for bg_thread in bg_threads:
                if bg_thread:
                    bg_thread.join()
        finally:
            for session_info in sessions_info:
                session_info[0].close()
