# Copyright (C) 2018 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import logging
import os

from cuckoo.common.files import Files
from cuckoo.common.objects import File, URL
from cuckoo.core.database import Database, Target as DbTarget
from cuckoo.misc import cwd

log = logging.getLogger(__name__)
db = Database()

class Target(object):

    files = ["file", "archive"]
    category_helpers = {
        "file": File,
        "archive": File,
        "url": URL,
    }

    def __init__(self, db_target=None):
        self.db_target = None
        self.target_dict = {}
        self.helper = None
        self.copied_binary = None
        self.is_file = None

        if db_target:
            self.set_target(db_target)

    def set_target(self, db_target):
        self.db_target = db_target
        self.target_dict = db_target.to_dict()
        self.is_file = db_target.category in self.files
        helper = self.category_helpers.get(db_target.category)

        if self.is_file:
            self.copied_binary = cwd(
                "storage", "binaries", db_target.sha256
            )
            self.helper = helper(self.copied_binary)
        else:
            self.helper = helper(db_target.target)

    def _create(self, target, target_helper, **kwargs):
        db_target = DbTarget(
            target=target, crc32=target_helper.get_crc32(),
            md5=target_helper.get_md5(), sha1=target_helper.get_sha1(),
            sha256=target_helper.get_sha256(),
            sha512=target_helper.get_sha512(),
            ssdeep=target_helper.get_ssdeep(),
            **kwargs
        )

        self.set_target(db_target)
        if kwargs.get("category") in self.files:
            self.copy()

        return db_target

    @staticmethod
    def create_urls(urls=[]):
        db_targets = []
        for url in urls:
            helper = URL(url)
            db_targets.append(DbTarget(
                target=url, category="url", crc32=helper.get_crc32(),
                md5=helper.get_md5(), sha1=helper.get_sha1(),
                sha256=helper.get_sha256(), sha512=helper.get_sha512(),
                ssdeep=helper.get_ssdeep()
            ))

        return db_targets

    def create_url(self, url):
        url_helper = URL(url)

        if not url:
            log.error("Cannot create target for URL '%s', it is empty", url)
            return None

        return self._create(url, url_helper, category="url")

    def create_file(self, file_path):
        file_helper = File(file_path)

        if not file_helper.valid():
            log.error(
                "Cannot create target for file '%s', file does not exist or is"
                " 0 bytes", file_path
            )
            return None

        return self._create(
            file_path, file_helper, category="file",
            file_type=file_helper.get_type(), file_size=file_helper.get_size()
        )

    def create_archive(self, file_path):
        file_helper = File(file_path)

        if not file_helper.valid():
            log.error(
                "Cannot create target for archive '%s', file does not exist or"
                " is 0 bytes", file_path
            )
            return None

        return self._create(
            file_path, file_helper, category="archive",
            file_type=file_helper.get_type(), file_size=file_helper.get_size()
        )

    def copy(self):
        """Create a copy in the binaries folder of the current target file"""
        if not self.is_file:
            return

        copy_path = cwd("storage", "binaries", self.sha256)
        if os.path.isfile(copy_path):
            return

        Files.copy(self.target, copy_path)

    def symlink_to_task(self, task_id):
        """Create symlink of current target file copy to given task_id path"""
        if not self.is_file:
            return

        copy_path = cwd("storage", "binaries", self.sha256)
        symlink = cwd("binary", analysis=task_id)
        try:
            Files.symlink_or_copy(copy_path, symlink)
        except OSError as e:
            log.error(
                "Failed to create symlink in task folder #%s to file '%s'."
                " Error: %s", task_id, copy_path, e
            )

    def delete_original(self):
        """Delete the original target. This is the location
        of where the file was submitted from"""
        if not self.is_file:
            return

        if not os.path.isfile(self.target):
            log.warning(
                "Cannot delete original file '%s'. It does not exist anymore",
                self.target
            )
            return

        try:
            os.remove(self.target)
        except OSError as e:
            log.error(
                "Failed to delete original file at path '%s' Error: %s",
                self.target, e
            )

    def delete_copy(self):
        """Delete the copy of the original target from the storage/binaries
        directory"""
        if not self.is_file:
            return

        try:
            os.remove(self.copied_binary)
        except OSError as e:
            log.error(
                "Failed to delete copied file at path '%s' Error: %s",
                self.copied_binary, e
            )

    def copy_exists(self):
        return os.path.isfile(self.copied_binary) if self.copied_binary else False

    def __getitem__(self, item):
        """Make Target readable as dictionary"""
        return self.target_dict[item]

    def __setitem__(self, key, value):
        """Make dictionary style value assignment to Target possible"""
        self.target_dict[key] = value

    @property
    def id(self):
        return self.target_dict.get("id")

    @property
    def task_id(self):
        return self.target_dict.get("task_id")

    @property
    def target(self):
        return self.target_dict.get("target")

    @property
    def category(self):
        return self.target_dict.get("category")

    @property
    def md5(self):
        return self.target_dict.get("md5")

    @property
    def crc32(self):
        return self.target_dict.get("crc32")

    @property
    def sha1(self):
        return self.target_dict.get("sha1")

    @property
    def sha256(self):
        return self.target_dict.get("sha256")

    @property
    def sha512(self):
        return self.target_dict.get("sha512")

    @property
    def ssdeep(self):
        return self.target_dict.get("ssdeep")

    @property
    def file_size(self):
        return self.target_dict.get("file_size")

    @property
    def file_type(self):
        return self.target_dict.get("file_type")

