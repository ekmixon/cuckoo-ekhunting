# Copyright (C) 2014-2016 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.
# Originally contributed by Check Point Software Technologies, Ltd.

import time
import logging
import StringIO
from threading import Thread
from lib.common.abstracts import Auxiliary
from lib.common.results import NetlogFile
from lib.api.adb import take_screenshot
from lib.api.screenshot import Screenshot

log = logging.getLogger(__name__)
SHOT_DELAY = 2

class Screenshots(Auxiliary, Thread):
    """Take screenshots."""

    def __init__(self):
        Thread.__init__(self)
        self.do_run = True

    def stop(self):
        """Stop screenshotting."""
        self.do_run = False

    def run(self):
        """Run screenshotting.
        @return: operation status.
        """
        img_counter = 0
        img_last = None

        while self.do_run:
            time.sleep(SHOT_DELAY)

            try:
                filename = f"screenshot{str(img_counter)}.jpg"
                img_current = take_screenshot(filename)
                if img_last and Screenshot().equal(img_last, img_current):
                    continue

                with open(img_current, 'r') as file:
                    tmpio = StringIO.StringIO(file.read())
                                # now upload to host from the StringIO
                    nf = NetlogFile(f'shots/{str(img_counter).rjust(4, "0")}.jpg')

                    for chunk in tmpio:
                        nf.sock.sendall(chunk)

                    nf.close()
                img_counter += 1
                img_last = img_current

            except IOError as e:
                log.error("Cannot take screenshot: %s", e)
                continue

        return True
