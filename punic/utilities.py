from __future__ import division, absolute_import, print_function

__all__ = ['work_directory', 'timeit', 'plist_get_key']

import contextlib
import os
import time
import logging
import plistlib
import subprocess

import punic

@contextlib.contextmanager
def work_directory(path):
    # type: (Union[Path, None])->None
    saved_wd = None
    if path:
        path = str(path)
        saved_wd = os.getcwd()
        os.chdir(path)
    try:
        yield
    except:
        raise
    finally:
        if saved_wd:
            os.chdir(saved_wd)


@contextlib.contextmanager
def timeit(task=None, log=None):
    if log is None:
        log = punic.current_session.config.log_timings

    # type: (Union[str, None])
    start = time.time()
    yield
    end = time.time()
    if log:
        logging.info('Task \'<ref>{}</ref>\' took <echo>{:.6f}</echo> seconds.'.format(task if task else '<unnamed task>', end - start))

def plist_get_key(path, key):
    # type: (Path)->str
    output = subprocess.check_output(['plutil', '-extract', key, 'xml1', '-o', '-', str(path)])
    try:
        return plistlib.loads(output)
    except:
        return plistlib.readPlistFromString(output)
