"""
multi_disk_random_hotplug test for Autotest framework.

:copyright: 2013 Red Hat Inc.
"""
import logging
import random
import time
import threading

from autotest.client.shared import error

from virttest import funcatexit
from virttest import data_dir
from virttest import qemu_qtree
from virttest import utils_test
from virttest import env_process
from virttest.qemu_devices import utils
from virttest.remote import LoginTimeoutError
from virttest.qemu_monitor import MonitorError


# qdev is not thread safe so in case of dangerous ops lock this thread
LOCK = None


def stop_stresser(vm, stop_cmd):
    """
    Wrapper which connects to vm and sends the stop_cmd
    :param vm: Virtual Machine
    :type vm: virttest.virt_vm.BaseVM
    :param stop_cmd: Command to stop the stresser
    :type stop_cmd: string
    """
    try:
        session = vm.wait_for_login(timeout=10)
        session.cmd(stop_cmd)
        session.close()
    except LoginTimeoutError:
        vm.destroy(gracefully=False)


# TODO: Remove this silly function when qdev vs. qtree comparison is available
def convert_params(params, args):
    """
    Updates params according to images_define_by_params arguments.
    :note: This is only temporarily solution until qtree vs. qdev verification
           is available.
    :param params: Dictionary with the test parameters
    :type param: virttest.utils_params.Params
    :param args: Dictionary of images_define_by_params arguments
    :type args: dictionary
    :return: Updated dictionary with the test parameters
    :rtype: virttest.utils_params.Params
    """
    convert = {'fmt': 'drive_format', 'cache': 'drive_cache',
               'werror': 'drive_werror', 'rerror': 'drive_rerror',
               'serial': 'drive_serial', 'snapshot': 'image_snapshot',
               'bus': 'drive_bus', 'unit': 'drive_unit', 'port': 'drive_port',
               'readonly': 'image_readonly', 'scsiid': 'drive_scsiid',
               'lun': 'drive_lun', 'aio': 'image_aio',
               'imgfmt': 'image_format', 'pci_addr': 'drive_pci_addr',
               'x_data_plane': 'x-data-plane',
               'scsi': 'virtio-blk-pci_scsi'}
    name = args.pop('name')
    params['images'] += " %s" % name
    params['image_name_%s' % name] = args.pop('filename')
    params['image_raw_device_%s' % name] = 'yes'
    for key, value in args.iteritems():
        params["%s_%s" % (convert.get(key, key), name)] = value
    return params


@error.context_aware
def run(test, params, env):
    """
    This tests the disk hotplug/unplug functionality.
    1) prepares multiple disks to be hotplugged
    2) hotplugs them
    3) verifies that they are in qtree/guest system/...
    4) stop I/O stress_cmd
    5) unplugs them
    6) continue I/O stress_cmd
    7) verifies they are not in qtree/guest system/...
    8) repeats $repeat_times

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_qtree(params, info_qtree, info_block, qdev):
        """
        Verifies that params, info qtree, info block and /proc/scsi/ matches
        :param params: Dictionary with the test parameters
        :type params: virttest.utils_params.Params
        :param info_qtree: Output of "info qtree" monitor command
        :type info_qtree: string
        :param info_block: Output of "info block" monitor command
        :type info_block: dict of dicts
        :param qdev: qcontainer representation
        :type qdev: virttest.qemu_devices.qcontainer.DevContainer
        """
        err = 0
        qtree = qemu_qtree.QtreeContainer()
        qtree.parse_info_qtree(info_qtree)
        disks = qemu_qtree.QtreeDisksContainer(qtree.get_nodes())
        (tmp1, tmp2) = disks.parse_info_block(info_block)
        err += tmp1 + tmp2
        err += disks.generate_params()
        err += disks.check_disk_params(params)
        if err:
            logging.error("info qtree:\n%s", info_qtree)
            logging.error("info block:\n%s", info_block)
            logging.error(qdev.str_bus_long())
            raise error.TestFail("%s errors occurred while verifying"
                                 " qtree vs. params" % err)

    def insert_into_qdev(qdev, param_matrix, no_disks, params, new_devices):
        """
        Inserts no_disks disks int qdev using randomized args from param_matrix
        :param qdev: qemu devices container
        :type qdev: virttest.qemu_devices.qcontainer.DevContainer
        :param param_matrix: Matrix of randomizable params
        :type param_matrix: list of lists
        :param no_disks: Desired number of disks
        :type no_disks: integer
        :param params: Dictionary with the test parameters
        :type params: virttest.utils_params.Params
        :return: (newly added devices, number of added disks)
        :rtype: tuple(list, integer)
        """
        dev_idx = 0
        _new_devs_fmt = ""
        pci_bus = {'aobject': 'pci.0'}
        _formats = param_matrix.pop('fmt', [params.get('drive_format')])
        formats = _formats[:]
        if len(new_devices) == 1:
            strict_mode = None
        else:
            strict_mode = True
        i = 0
        while i < no_disks:
            # Set the format
            if len(formats) < 1:
                if i == 0:
                    raise error.TestError("Fail to add any disks, probably bad"
                                          " configuration.")
                logging.warn("Can't create desired number '%s' of disk types "
                             "'%s'. Using '%d' no disks.", no_disks,
                             _formats, i)
                break
            name = 'stg%d' % i
            args = {'name': name, 'filename': stg_image_name % i, 'pci_bus': pci_bus}
            fmt = random.choice(formats)
            if fmt == 'virtio_scsi':
                args['fmt'] = 'scsi-hd'
                args['scsi_hba'] = 'virtio-scsi-pci'
            elif fmt == 'lsi_scsi':
                args['fmt'] = 'scsi-hd'
                args['scsi_hba'] = 'lsi53c895a'
            elif fmt == 'spapr_vscsi':
                args['fmt'] = 'scsi-hd'
                args['scsi_hba'] = 'spapr-vscsi'
            else:
                args['fmt'] = fmt
            # Other params
            for key, value in param_matrix.iteritems():
                args[key] = random.choice(value)

            try:
                devs = qdev.images_define_by_variables(**args)
                # parallel test adds devices in mixed order, force bus/addrs
                qdev.insert(devs, strict_mode)
            except utils.DeviceError:
                for dev in devs:
                    if dev in qdev:
                        qdev.remove(dev, recursive=True)
                formats.remove(fmt)
                continue

            params = convert_params(params, args)
            env_process.preprocess_image(test, params.object_params(name),
                                         name)
            new_devices[dev_idx].extend(devs)
            dev_idx = (dev_idx + 1) % len(new_devices)
            _new_devs_fmt += "%s(%s) " % (name, fmt)
            i += 1
        if _new_devs_fmt:
            logging.info("Using disks: %s", _new_devs_fmt[:-1])
        param_matrix['fmt'] = _formats
        return new_devices, params

    def _hotplug(new_devices, monitor, prefix=""):
        """
        Do the actual hotplug of the new_devices using monitor monitor.
        :param new_devices: List of devices which should be hotplugged
        :type new_devices: List of virttest.qemu_devices.qdevice.QBaseDevice
        :param monitor: Monitor which should be used for hotplug
        :type monitor: virttest.qemu_monitor.Monitor
        """
        hotplug_outputs = []
        hotplug_sleep = float(params.get('wait_between_hotplugs', 0))
        for device in new_devices:      # Hotplug all devices
            time.sleep(hotplug_sleep)
            hotplug_outputs.append(device.hotplug(monitor))
        time.sleep(hotplug_sleep)
        failed = []
        passed = []
        unverif = []
        for device in new_devices:      # Verify the hotplug status
            out = hotplug_outputs.pop(0)
            out = device.verify_hotplug(out, monitor)
            if out is True:
                passed.append(str(device))
            elif out is False:
                failed.append(str(device))
            else:
                unverif.append(str(device))
        if not failed and not unverif:
            logging.debug("%sAll hotplugs verified (%s)", prefix, len(passed))
        elif not failed:
            logging.warn("%sHotplug status:\nverified %s\nunverified %s",
                         prefix, passed, unverif)
        else:
            logging.error("%sHotplug status:\nverified %s\nunverified %s\n"
                          "failed %s", prefix, passed, unverif, failed)
            logging.error("qtree:\n%s", monitor.info("qtree", debug=False))
            raise error.TestFail("%sHotplug of some devices failed." % prefix)

    def hotplug_serial(new_devices, monitor):
        _hotplug(new_devices[0], monitor)

    def hotplug_parallel(new_devices, monitors):
        threads = []
        for i in xrange(len(new_devices)):
            name = "Th%s: " % i
            logging.debug("%sworks with %s devices", name,
                          [_.str_short() for _ in new_devices[i]])
            thread = threading.Thread(target=_hotplug, name=name[:-2],
                                      args=(new_devices[i], monitors[i], name))
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        logging.debug("All threads finished.")

    def _postprocess_images():
        # remove and check the images
        _disks = []
        for disk in params['images'].split(' '):
            if disk.startswith("stg"):
                env_process.postprocess_image(test, params.object_params(disk),
                                              disk)
            else:
                _disks.append(disk)
            params['images'] = " ".join(_disks)

    def _unplug(new_devices, qdev, monitor, prefix=""):
        """
        Do the actual unplug of new_devices using monitor monitor
        :param new_devices: List of devices which should be hotplugged
        :type new_devices: List of virttest.qemu_devices.qdevice.QBaseDevice
        :param qdev: qemu devices container
        :type qdev: virttest.qemu_devices.qcontainer.DevContainer
        :param monitor: Monitor which should be used for hotplug
        :type monitor: virttest.qemu_monitor.Monitor
        """
        unplug_sleep = float(params.get('wait_between_unplugs', 0))
        unplug_outs = []
        unplug_devs = []
        for device in new_devices[::-1]:    # unplug all devices
            if device in qdev:  # Some devices are removed with previous one
                time.sleep(unplug_sleep)
                unplug_devs.append(device)
                try:
                    output = device.unplug(monitor)
                except MonitorError:
                    # In new versions of qemu, to unplug a disk, cmd
                    # '__com.redhat_drive_del' is not necessary; while it's
                    # necessary in old qemu verisons. Following update is to
                    # pass the error caused by using the cmd in new
                    # qemu versions.
                    if device.get_qid() not in monitor.info("block",
                                                            debug=False):
                        pass
                    else:
                        raise
                unplug_outs.append(output)
                # Remove from qdev even when unplug failed because further in
                # this test we compare VM with qdev, which should be without
                # these devices. We can do this because we already set the VM
                # as dirty.
                if LOCK:
                    LOCK.acquire()
                qdev.remove(device)
                if LOCK:
                    LOCK.release()
        time.sleep(unplug_sleep)
        failed = []
        passed = []
        unverif = []
        for device in unplug_devs:          # Verify unplugs
            _out = unplug_outs.pop(0)
            # unplug effect can be delayed as it waits for OS respone before
            # it removes the device form qtree
            for _ in xrange(50):
                out = device.verify_unplug(_out, monitor)
                if out is True:
                    break
                time.sleep(0.1)
            if out is True:
                passed.append(str(device))
            elif out is False:
                failed.append(str(device))
            else:
                unverif.append(str(device))

        if not failed and not unverif:
            logging.debug("%sAll unplugs verified (%s)", prefix, len(passed))
        elif not failed:
            logging.warn("%sUnplug status:\nverified %s\nunverified %s",
                         prefix, passed, unverif)
        else:
            logging.error("%sUnplug status:\nverified %s\nunverified %s\n"
                          "failed %s", prefix, passed, unverif, failed)
            logging.error("qtree:\n%s", monitor.info("qtree", debug=False))
            raise error.TestFail("%sUnplug of some devices failed." % prefix)

    def unplug_serial(new_devices, qdev, monitor):
        _unplug(new_devices[0], qdev, monitor)

    def unplug_parallel(new_devices, qdev, monitors):
        threads = []
        for i in xrange(len(new_devices)):
            name = "Th%s: " % i
            logging.debug("%sworks with %s devices", name,
                          [_.str_short() for _ in new_devices[i]])
            thread = threading.Thread(target=_unplug,
                                      args=(new_devices[i], qdev, monitors[i]))
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        logging.debug("All threads finished.")

    def verify_qtree_unsupported(params, info_qtree, info_block, qdev):
        return logging.warn("info qtree not supported. Can't verify qtree vs. "
                            "guest disks.")

    vm = env.get_vm(params['main_vm'])
    qdev = vm.devices
    session = vm.wait_for_login(timeout=int(params.get("login_timeout", 360)))
    out = vm.monitor.human_monitor_cmd("info qtree", debug=False)
    if "unknown command" in str(out):
        verify_qtree = verify_qtree_unsupported

    stg_image_name = params['stg_image_name']
    if not stg_image_name[0] == "/":
        stg_image_name = "%s/%s" % (data_dir.get_data_dir(), stg_image_name)
    stg_image_num = int(params['stg_image_num'])
    stg_params = params.get('stg_params', '').split(' ')
    i = 0
    while i < len(stg_params) - 1:
        if not stg_params[i].strip():
            i += 1
            continue
        if stg_params[i][-1] == '\\':
            stg_params[i] = '%s %s' % (stg_params[i][:-1],
                                       stg_params.pop(i + 1))
        i += 1

    param_matrix = {}
    for i in xrange(len(stg_params)):
        if not stg_params[i].strip():
            continue
        (cmd, parm) = stg_params[i].split(':', 1)
        # ',' separated list of values
        parm = parm.split(',')
        j = 0
        while j < len(parm) - 1:
            if parm[j][-1] == '\\':
                parm[j] = '%s,%s' % (parm[j][:-1], parm.pop(j + 1))
            j += 1

        param_matrix[cmd] = parm

    # Modprobe the module if specified in config file
    module = params.get("modprobe_module")
    if module:
        session.cmd("modprobe %s" % module)

    stress_cmd = params.get('stress_cmd')
    if stress_cmd:
        funcatexit.register(env, params.get('type'), stop_stresser, vm,
                            params.get('stress_kill_cmd'))
        stress_session = vm.wait_for_login(timeout=10)
        for _ in xrange(int(params.get('no_stress_cmds', 1))):
            stress_session.sendline(stress_cmd)

    rp_times = int(params.get("repeat_times", 1))
    queues = params.get("multi_disk_type") == "parallel"
    if queues:  # parallel
        queues = xrange(len(vm.monitors))
        hotplug = hotplug_parallel
        unplug = unplug_parallel
        monitor = vm.monitors
        global LOCK
        LOCK = threading.Lock()
    else:   # serial
        queues = xrange(1)
        hotplug = hotplug_serial
        unplug = unplug_serial
        monitor = vm.monitor
    context_msg = "Running sub test '%s' %s"
    error.context("Verify disk before test", logging.info)
    info_qtree = vm.monitor.info('qtree', False)
    info_block = vm.monitor.info_block(False)
    verify_qtree(params, info_qtree, info_block, qdev)
    for iteration in xrange(rp_times):
        error.context("Hotplugging/unplugging devices, iteration %d"
                      % iteration, logging.info)
        sub_type = params.get("sub_type_before_plug")
        if sub_type:
            error.context(context_msg % (sub_type, "before hotplug"),
                          logging.info)
            utils_test.run_virt_sub_test(test, params, env, sub_type)

        error.context("Insert devices into qdev", logging.debug)
        qdev.set_dirty()
        new_devices = [[] for _ in queues]
        new_devices, params = insert_into_qdev(qdev, param_matrix,
                                               stg_image_num, params,
                                               new_devices)

        error.context("Hotplug the devices", logging.debug)
        hotplug(new_devices, monitor)
        time.sleep(float(params.get('wait_after_hotplug', 0)))

        error.context("Verify disks after hotplug", logging.debug)
        info_qtree = vm.monitor.info('qtree', False)
        info_block = vm.monitor.info_block(False)
        vm.verify_alive()
        verify_qtree(params, info_qtree, info_block, qdev)
        qdev.set_clean()

        sub_type = params.get("sub_type_after_plug")
        if sub_type:
            error.context(context_msg % (sub_type, "after hotplug"),
                          logging.info)
            utils_test.run_virt_sub_test(test, params, env, sub_type)

        sub_type = params.get("sub_type_before_unplug")
        if sub_type:
            error.context(context_msg % (sub_type, "before hotunplug"),
                          logging.info)
            utils_test.run_virt_sub_test(test, params, env, sub_type)

        error.context("Unplug and remove the devices", logging.debug)
        if stress_cmd:
            session.cmd(params["stress_stop_cmd"])
        unplug(new_devices, qdev, monitor)
        if stress_cmd:
            session.cmd(params["stress_cont_cmd"])
        _postprocess_images()

        error.context("Verify disks after unplug", logging.debug)
        time.sleep(float(params.get('wait_after_unplug', 0)))
        info_qtree = vm.monitor.info('qtree', False)
        info_block = vm.monitor.info_block(False)
        vm.verify_alive()
        verify_qtree(params, info_qtree, info_block, qdev)
        # we verified the unplugs, set the state to 0
        for _ in xrange(qdev.get_state()):
            qdev.set_clean()

        sub_type = params.get("sub_type_after_unplug")
        if sub_type:
            error.context(context_msg % (sub_type, "after hotunplug"),
                          logging.info)
            utils_test.run_virt_sub_test(test, params, env, sub_type)

    # Check for various KVM failures
    error.context("Validating VM after all disk hotplug/unplugs",
                  logging.debug)
    vm.verify_alive()
    out = session.cmd_output('dmesg')
    if "I/O error" in out:
        logging.warn(out)
        raise error.TestWarn("I/O error messages occured in dmesg, check"
                             "the log for details.")
