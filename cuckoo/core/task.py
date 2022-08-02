# Copyright (C) 2017-2018 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import datetime
import io
import json
import logging
import os
import threading
import zipfile

from cuckoo.common.config import config
from cuckoo.common.exceptions import CuckooOperationalError
from cuckoo.common.files import Folders, Files
from cuckoo.common.utils import (
    get_directory_size, json_default, json_encode, str_to_datetime
)
from cuckoo.core.database import (
    Database, TASK_RECOVERED, Task as DbTask, Target as DbTarget
)
from cuckoo.core.plugins import RunProcessing, RunSignatures, RunReporting
from cuckoo.core.target import Target
from cuckoo.misc import cwd

from sqlalchemy.exc import SQLAlchemyError

log = logging.getLogger(__name__)
db = Database()
target = Target()

class Task(object):

    dirs = ["shots", "logs", "files", "extracted", "buffer", "memory"]
    latest_symlink_lock = threading.Lock()

    def __init__(self, db_task=None):
        self.db_task = None
        self.task_dict = {}

        if db_task:
            self.set_task(db_task)

    def set_task(self, db_task):
        """Update Task wrapper with new task db object
        @param db_task: Task Db object"""
        self.db_task = db_task
        self.path = cwd(analysis=db_task.id)
        self.task_dict = db_task.to_dict()

        self.task_dict["targets"] = [
            Target(db_target) for db_target in db_task.targets
        ]
        # For backwards compatibility, load these two attributes
        # TODO Remove when processing and reporting changes
        if self.task_dict["targets"]:
            self.task_dict["target"] = self.task_dict["targets"][0].target
            self.task_dict["category"] = self.task_dict["targets"][0].category
        else:
            self.task_dict["target"] = "none"
            self.task_dict["category"] = None

    def load_task_dict(self, task_dict):
        """Load all dict key values as attributes to the object.
        Try to change target dictionaries to target objects"""
        # Cast to Cuckoo dictionary, so keys can be accessed as attributes
        targets = []
        for target_dict in task_dict.get("targets", []):
            target = Target()
            target.target_dict = target_dict
            targets.append(target)

        newtask = DbTask().to_dict()
        task_dict["targets"] = targets
        newtask.update(task_dict)
        self.task_dict = newtask
        self.path = cwd(analysis=task_dict["id"])

    def load_from_db(self, task_id):
        """Load task from id. Returns True of success, False otherwise"""
        db_task = db.view_task(task_id)
        if not db_task:
            return False

        self.set_task(db_task)
        return True

    def create_empty(self):
        """Create task directory and copy files to binary folder"""
        log.debug("Creating directories for task #%s", self.id)
        self.create_dirs()
        if self.targets:
            self.targets[0].symlink_to_task(self.id)

    def create_dirs(self, id=None):
        """Create the folders for this analysis. Returns True if
        all folders were created. False if not"""
        if not id:
            id = self.id

        for task_dir in self.dirs:
            create_dir = cwd(task_dir, analysis=id)
            try:
                if not os.path.exists(create_dir):
                    Folders.create(create_dir)
            except CuckooOperationalError as e:
                log.error(
                    "Unable to create folder '%s' for task #%s Error: %s",
                    create_dir, id, e
                )
                return False

        return True

    def set_latest(self):
        """Create a symlink called 'latest' pointing to this analysis
        in the analysis folder"""
        latest = cwd("storage", "analyses", "latest")
        try:
            self.latest_symlink_lock.acquire()

            if os.path.lexists(latest):
                os.remove(latest)

            Files.symlink(self.path, latest)
        except OSError as e:
            log.error(
                "Error pointing to latest analysis symlink. Error: %s", e
            )
        finally:
            self.latest_symlink_lock.release()

    def delete_binary_symlink(self):
        # If the copied binary was deleted, also delete the symlink to it
        symlink = cwd("binary", analysis=self.id)
        if os.path.islink(symlink):
            try:
                os.remove(symlink)
            except OSError as e:
                log.error(
                    "Failed to delete symlink to removed binary '%s'. Error:"
                    " %s", symlink, e
                )

    def dir_exists(self):
        """Checks if the analysis folder for this task id exists"""
        return os.path.exists(self.path)

    def is_reported(self):
        """Checks if a JSON report exists for this task"""
        return os.path.exists(
            os.path.join(self.path, "reports", "report.json")
        )

    def write_task_json(self, **kwargs):
        """Change task to JSON and write it to disk"""
        path = os.path.join(self.path, "task.json")
        dump = self.db_task.to_dict()

        # For backwards compatibility, add these to task json.
        # TODO: Remove when processing and reporting change
        dump.update({
            "category": self.category,
            "target": self.target
        })

        if kwargs:
            dump.update(kwargs)

        with open(path, "wb") as fw:
            fw.write(json_encode(dump))

    def process(self, signatures=True, reporting=True, processing_modules=[]):
        """Process, run signatures and reports the results for this task"""
        results = RunProcessing(task=self.task_dict).run(
            processing_list=processing_modules
        )
        if signatures:
            RunSignatures(results=results).run()

        if reporting:
            RunReporting(task=self.task_dict, results=results).run()

        if config("cuckoo:cuckoo:delete_original"):
            for target in self.targets:
                target.delete_original()

        if config("cuckoo:cuckoo:delete_bin_copy"):
            for target in self.targets:
                target.delete_copy()

        return True

    def get_tags_list(self, tags):
        """Check tags and change into usable format"""
        ret = []
        if isinstance(tags, basestring):
            ret.extend(tag.strip() for tag in tags.split(",") if tag.strip())
        elif isinstance(tags, (tuple, list)):
            ret.extend(
                tag.strip()
                for tag in tags
                if isinstance(tag, basestring) and tag.strip()
            )

        return ret

    def add(self, targets=[], timeout=0, package="", options="", priority=1,
            custom="", owner="", machine="", platform="", tags=None,
            memory=False, enforce_timeout=False, clock=None, task_type=None,
            submit_id=None, start_on=None, longterm_id=None):
        """Create new task
        @param targets: List of ORM Target objects.
        @param timeout: selected timeout.
        @param package: the analysis package to use
        @param options: analysis options.
        @param priority: analysis priority.
        @param custom: custom options.
        @param owner: task owner.
        @param machine: selected machine.
        @param platform: platform.
        @param tags: optional tags that must be set for machine selection
        @param memory: toggle full memory dump.
        @param enforce_timeout: toggle full timeout execution.
        @param clock: virtual machine clock time
        @param task_type: The type of task: regular, longterm, other type
        @param longterm_id: Longterm analysis ID to connect this task to
        @return: task id or None.
        """
        if isinstance(start_on, basestring):
            start_on = str_to_datetime(start_on)
            if not start_on:
                log.error("'start on' format should be: 'YYYY-M-D H:M'")
                return None

        # If no clock time was provided, but a specific starting time/date
        # was, also use this starting time for the system clock instead of
        # the default current time (now).
        if not clock and start_on:
            clock = start_on

        if clock and isinstance(clock, basestring):
            clock = str_to_datetime(clock)
            if not clock:
                log.warning(
                    "Datetime %s not in format M-D-YYY H:M:S. Using current "
                    "timestamp", clock
                )
                clock = datetime.datetime.now()

        newtask = DbTask()
        newtask.type = task_type
        newtask.timeout = timeout
        newtask.priority = priority
        newtask.custom = custom
        newtask.owner = owner
        newtask.machine = machine
        newtask.package = package
        newtask.options = options
        newtask.platform = platform
        newtask.memory = memory
        newtask.enforce_timeout = enforce_timeout
        newtask.clock = clock
        newtask.submit_id = submit_id
        newtask.start_on = start_on
        newtask.longterm_id = longterm_id

        session = db.Session()
        for tag in self.get_tags_list(tags):
            newtask.tags.append(db.get_or_create(session, name=tag))

        session.add(newtask)
        try:
            session.commit()
        except SQLAlchemyError as e:
            log.exception("Exception when adding task to database: %s", e)
            session.rollback()
            session.close()
            return None

        task_id = newtask.id
        for t in targets:
            t.task_id = task_id

        try:
            if len(targets) > 1:
                # Bulk add targets
                db.engine.execute(
                    DbTarget.__table__.insert(),
                    [t.to_dict(exclude=["id"]) for t in targets]
                )
            elif targets:
                session.add(targets[0])
                session.commit()

            # Create the directories for this task
            self.create_dirs(id=task_id)

            # If the target type is a file, create a symlink pointing to it
            # inside the task folder.
            if targets and targets[0].category in Target.files:
                Target(targets[0]).symlink_to_task(task_id)

        except SQLAlchemyError as e:
            log.exception("Exception while adding targets to database: %s", e)
            session.rollback()
            return None
        finally:
            session.close()

        return task_id

    def add_massurl(self, urls=[], package="ie", options="", priority=1,
                    custom="", owner="", machine="", platform="", tags=None,
                    memory=False, clock=None, start_on=None):
        if not urls:
            log.error("No URLs provided. Cannot create task.")
            return None

        return self.add(
            targets=Target.create_urls(urls), timeout=len(urls) * 60,
            package=package, options=options, priority=priority, custom=custom,
            owner=owner, machine=machine, platform=platform, tags=tags,
            memory=memory, enforce_timeout=True, clock=clock,
            task_type="massurl", start_on=start_on
        )

    def add_path(self, file_path, timeout=0, package="", options="",
                 priority=1, custom="", owner="", machine="", platform="",
                 tags=None, memory=False, enforce_timeout=False, clock=None,
                 submit_id=None, start_on=None, task_type="regular"):
        """Add a task to database from file path.
        @param file_path: sample path.
        @param timeout: selected timeout.
        @param options: analysis options.
        @param priority: analysis priority.
        @param custom: custom options.
        @param owner: task owner.
        @param machine: selected machine.
        @param platform: platform.
        @param tags: Tags required in machine selection
        @param memory: toggle full memory dump.
        @param enforce_timeout: toggle full timeout execution.
        @param clock: virtual machine clock time
        @param task_type: The type of task: regular, longterm, other type
        @return: task id or None
        """
        if not file_path:
            log.error("No file path given to analyze, cannot create task")
            return None

        db_target = target.create_file(file_path)
        if not db_target:
            log.error("New task creation failed, could not create target")
            return None

        return self.add(
            targets=[db_target], timeout=timeout, package=package,
            options=options, priority=priority, custom=custom, owner=owner,
            machine=machine, platform=platform, tags=tags, memory=memory,
            enforce_timeout=enforce_timeout, clock=clock, task_type=task_type,
            submit_id=submit_id, start_on=start_on
        )

    def add_archive(self, file_path, filename, package, timeout=0,
                    options=None, priority=1, custom="", owner="", machine="",
                    platform="", tags=None, memory=False,
                    enforce_timeout=False, clock=None, submit_id=None,
                    start_on=None, task_type="regular"):
        """Add a task to the database that's packaged in an archive file.
        @param file_path: path to archive
        @param filename: name of file in archive
        @param timeout: selected timeout.
        @param options: analysis options.
        @param priority: analysis priority.
        @param custom: custom options.
        @param owner: task owner.
        @param machine: selected machine.
        @param platform: platform.
        @param tags: tags for machine selection
        @param memory: toggle full memory dump.
        @param enforce_timeout: toggle full timeout execution.
        @param clock: virtual machine clock time
        @return: task id or None.
        """
        if not file_path:
            log.error("No file path given to analyze, cannot create task")
            return None

        options = options or {}
        options["filename"] = filename

        db_target = target.create_archive(file_path)
        if not db_target:
            log.error("New task creation failed, could not create target")
            return None

        return self.add(
            targets=[db_target], timeout=timeout, package=package,
            options=options, priority=priority, custom=custom, owner=owner,
            machine=machine, platform=platform, tags=tags, memory=memory,
            enforce_timeout=enforce_timeout, clock=clock, task_type=task_type,
            submit_id=submit_id, start_on=start_on
        )

    def add_url(self, url, timeout=0, package="", options="", priority=1,
                custom="", owner="", machine="", platform="", tags=None,
                memory=False, enforce_timeout=False, clock=None,
                submit_id=None, start_on=None, task_type="regular"):
        """Add a task to database from url.
        @param url: url.
        @param timeout: selected timeout.
        @param package: the analysis package to use
        @param options: analysis options.
        @param priority: analysis priority.
        @param custom: custom options.
        @param owner: task owner.
        @param machine: selected machine.
        @param platform: platform.
        @param tags: tags for machine selection
        @param memory: toggle full memory dump.
        @param enforce_timeout: toggle full timeout execution.
        @param clock: virtual machine clock time
        @return: task id or None.
        """
        if not url:
            log.error("No URL given, cannot create task")
            return None

        db_target = target.create_url(url)
        if not db_target:
            log.error("New task creation failed, could not create target")
            return None

        return self.add(
            targets=[db_target], timeout=timeout, package=package,
            options=options, priority=priority, custom=custom, owner=owner,
            machine=machine, platform=platform, tags=tags, memory=memory,
            enforce_timeout=enforce_timeout, clock=clock, task_type=task_type,
            submit_id=submit_id, start_on=start_on
        )

    def add_reboot(self, task_id, timeout=0, options="", priority=1,
                   owner="", machine="", platform="", tags=None, memory=False,
                   enforce_timeout=False, clock=None, submit_id=None,
                   task_type="regular"):
        """Add a reboot task to database from an existing analysis.
        @param task_id: task id of existing analysis.
        @param timeout: selected timeout.
        @param package: the analysis package to use
        @param options: analysis options.
        @param priority: analysis priority.
        @param owner: task owner.
        @param machine: selected machine.
        @param platform: platform.
        @param tags: tags for machine selection
        @param memory: toggle full memory dump.
        @param enforce_timeout: toggle full timeout execution.
        @param clock: virtual machine clock time
        @return: task id or None.
        """

        if not self.load_from_db(task_id):
            log.error(
                "Unable to add reboot analysis as the original task or its "
                "sample has already been deleted."
            )
            return None

        custom = f"{task_id}"

        if not self.targets:
            log.error(
                "No target to reboot available to reboot task #%s", self.id
            )
            return None

        target = self.targets[0]
        if target.is_file and not target.copy_exists():
            log.error(
                "Target file no longer exists, cannot reboot task #%s", self.id
            )
            return None

        return self.add(
            targets=[target.db_target], timeout=timeout, package="reboot",
            options=options, priority=priority, custom=custom, owner=owner,
            machine=machine, platform=platform, tags=tags, memory=memory,
            enforce_timeout=enforce_timeout, clock=clock, task_type=task_type,
            submit_id=submit_id
        )

    def add_baseline(self, timeout=0, owner="", machine="", memory=False):
        """Add a baseline task to database.
        @param timeout: selected timeout.
        @param owner: task owner.
        @param machine: selected machine.
        @param memory: toggle full memory dump.
        @return: task id or None.
        """
        return self.add(
            timeout=timeout, priority=999, owner=owner, machine=machine,
            memory=memory, task_type="baseline"
        )

    def add_service(self, timeout, owner, tags):
        """Add a service task to database.
        @param timeout: selected timeout.
        @param owner: task owner.
        @param tags: task tags.
        @return: task id or None.
        """
        return self.add(
            timeout=timeout, priority=999, owner=owner, tags=tags,
            task_type="service"
        )

    def reschedule(self, task_id=None, priority=None):
        """Reschedule this task or the given task
        @param task_id: task_id to reschedule
        @param priority: overwrites the priority the task already has"""
        if not self.db_task and not task_id:
            log.error(
                "Task is None and no task_id provided, cannot reschedule"
            )
            return None
        elif task_id:
            if not self.load_from_db(task_id):
                log.error("Failed to load task from id: %s", task_id)
                return None

        priority = priority or self.priority

        # Change status to recovered
        db.set_status(self.id, TASK_RECOVERED)

        return self.add(
            targets=[target.db_target for target in self.targets],
            timeout=self.timeout, package=self.package,
            options=self.options, priority=priority, custom=self.custom,
            owner=self.owner, machine=self.machine, platform=self.platform,
            tags=self.tags, memory=self.memory,
            enforce_timeout=self.enforce_timeout, clock=self.clock,
            task_type=self.type,
        )

    def add_longterm(self, db_target, startdate, days, starttime, stoptime,
                     name=None, package=None, options="", priority=1,
                     custom=None, owner=None, machine=None, platform=None,
                     tags=None, memory=False, clock=None):

        if not days:
            log.error("Invalid amount of days to run provided")
            return None

        try:
            days = int(days)
        except ValueError:
            log.error("Invalid amount of days to run provided")
            return None

        if days < 1:
            log.error("The amount of days cannot be lower than 1")
            return None

        # Verify times are the correct format
        try:
            startdate = datetime.datetime.strptime(startdate, "%Y-%m-%d")
        except ValueError:
            log.error("Invalid start date format. Use 'YYYY-MM-DD'")
            return None

        try:
            starttime = datetime.datetime.strptime(starttime, "%H:%M")
            stoptime = datetime.datetime.strptime(stoptime, "%H:%M")
        except ValueError:
            log.error("Invalid start/stop time format. Use '%H:%M'")
            return None

        if stoptime <= starttime:
            log.error("Stop time cannot be earlier than starting time")
            return None

        lta_id = db.add_longterm(name=name, machine=machine)
        if not lta_id:
            log.error("Failed to create new longterm analysis")
            return None

        # Create 'days' amount of tasks
        timeout = (stoptime - starttime).seconds
        starton = datetime.datetime.combine(startdate.date(), starttime.time())
        for d in range(days):
            # Only the first task should have the chosen package. All following
            # tasks should look try to monitor behavior created by files left
            # behind of the first task. This is taken care of by the longterm
            # analysis package
            self.add(
                targets=db_target if d < 1 else [], timeout=timeout,
                package=package if d < 1 else "longterm", options=options,
                priority=priority, custom=custom, owner=owner, machine=machine,
                platform=platform, tags=tags, memory=memory,
                enforce_timeout=True, clock=clock, task_type="longterm",
                longterm_id=lta_id, start_on=starton
            )

            # Increment the starting datetime by one day, so the next task
            # is scheduled for the next day.
            starton = starton + datetime.timedelta(days=1)

        return lta_id

    def add_url_longterm(self, url, startdate, days, starttime, stoptime,
                     name=None, package=None, options="", priority=1,
                     custom=None, owner=None, machine=None, platform=None,
                     tags=None, memory=False, clock=None):

        if not url:
            log.error("No URL given, cannot create task")
            return None

        db_target = target.create_url(url)
        if not db_target:
            log.error("New task creation failed, could not create target")
            return None

        return self.add_longterm(
            db_target=db_target, startdate=startdate, days=days,
            starttime=starttime, stoptime=stoptime, name=name, package=package,
            options=options, priority=priority, custom=custom, owner=owner,
            machine=machine, platform=platform, tags=tags, memory=memory,
            clock=clock
        )

    def add_path_longterm(self, file_path, startdate, days, starttime,
                          stoptime, name=None, package=None, options="",
                          priority=1, custom=None, owner=None, machine=None,
                          platform=None, tags=None, memory=False, clock=None):

        if not file_path:
            log.error("No file path given to analyze, cannot create task")
            return None

        db_target = target.create_file(file_path)
        if not db_target:
            log.error("New task creation failed, could not create target")
            return None

        return self.add_longterm(
            db_target=db_target, startdate=startdate, days=days,
            starttime=starttime, stoptime=stoptime, name=name, package=package,
            options=options, priority=priority, custom=custom, owner=owner,
            machine=machine, platform=platform, tags=tags, memory=memory,
            clock=clock
        )

    @staticmethod
    def requirements_str(db_task):
        """Returns the task machine requirements in a printable string
        @param db_task: Database Task object"""
        requirements = ""

        req_fields = {
            "platform": db_task.platform,
            "machine": db_task.machine,
            "tags": db_task.tags
        }

        for reqname, value in req_fields.iteritems():
            if value:
                requirements += f"{reqname}="
                if reqname == "tags":
                    for tag in db_task.tags:
                        requirements += f"{tag.name},"
                else:
                    requirements += f"{value}"
                requirements += " "

        return requirements

    @staticmethod
    def estimate_export_size(task_id, taken_dirs, taken_files):
        """Estimate the size of the export zip if given dirs and files
        are included"""
        path = cwd(analysis=task_id)
        if not os.path.exists(path):
            log.error("Path %s does not exist", path)
            return 0

        size_total = 0

        for directory in taken_dirs:
            destination = f"{path}/{os.path.basename(directory)}"
            if os.path.isdir(destination):
                size_total += get_directory_size(destination)

        for filename in taken_files:
            destination = f"{path}/{os.path.basename(filename)}"
            if os.path.isfile(destination):
                size_total += os.path.getsize(destination)

        return size_total / 6.5

    @staticmethod
    def get_files(task_id):
        """Locate all directories/results available for this task
        returns a tuple of all dirs and files"""
        analysis_path = cwd(analysis=task_id)
        if not os.path.exists(analysis_path):
            log.error("Path %s does not exist", analysis_path)
            return [], []

        dirs, files = [], []
        for filename in os.listdir(analysis_path):
            path = os.path.join(analysis_path, filename)
            if os.path.isdir(path):
                dirs.append((filename, len(os.listdir(path))))
            else:
                files.append(filename)

        return dirs, files

    @staticmethod
    def create_zip(task_id, taken_dirs, taken_files, export=True):
        """Returns a zip file as a file like object.
        @param task_id: task id of an existing task
        @param taken_dirs: list of directories (limiting to extension possible
        if a dir is given in a tuple with a list of extensions
        ['dir1', ('dir2', ['.bson'])]
        @param taken_files: files from root dir to include
        @param export: Is this a full task export
        (should extra info be included?)"""

        if not taken_dirs and not taken_files:
            log.warning("No directories or files to zip were provided")
            return None

        # Test if the task_id is an actual integer, to prevent it being
        # a path.
        try:
            int(task_id)
        except ValueError:
            log.error("Task id was not integer! Actual value: %s", task_id)
            return None

        task_path = cwd(analysis=task_id)
        if not os.path.exists(task_path):
            log.error("Path %s does not exist", task_path)
            return None

        # Fill dictionary with extensions per directory to include.
        # If no extensions exist for a directory, it will include all when
        # making the zip
        include_exts = {}

        taken_dirs_tmp = []
        for taken_dir in taken_dirs:

            # If it is a tuple, it contains extensions to include
            if isinstance(taken_dir, tuple):
                taken_dirs_tmp.append(taken_dir[0])
                if taken_dir[0] not in include_exts:
                    include_exts[taken_dir[0]] = []

                if isinstance(taken_dir[1], list):
                    include_exts[taken_dir[0]].extend(taken_dir[1])
                else:
                    include_exts[taken_dir[0]].append(taken_dir[1])
            else:
                taken_dirs_tmp.append(taken_dir)

        taken_dirs = taken_dirs_tmp
        f = io.BytesIO()
        z = zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED, allowZip64=True)

        # If exporting a complete analysis, create an analysis.json file with
        # additional information about this analysis. This information serves
        # as metadata when importing a task.
        if export:
            report_path = cwd("reports", "report.json", analysis=task_id)

            if not os.path.isfile(report_path):
                log.warning(
                    "Cannot export task %s, report.json does not exist",
                    task_id
                )
                z.close()
                return None

            report = json.loads(open(report_path, "rb").read())
            obj = {
                "action": report.get("debug", {}).get("action", []),
                "errors": report.get("debug", {}).get("errors", []),
            }
            z.writestr(
                "analysis.json", json.dumps(
                    obj, indent=4, default=json_default
                )
            )

        for dirpath, dirnames, filenames in os.walk(task_path):
            if dirpath == task_path:
                for filename in filenames:
                    if filename in taken_files:
                        z.write(os.path.join(dirpath, filename), filename)

            basedir = os.path.basename(dirpath)
            if basedir in taken_dirs:

                for filename in filenames:

                    # Check if this directory has a set of extensions that
                    # should only be included
                    include = True
                    if basedir in include_exts and include_exts[basedir]:
                        include = any(filename.endswith(ext) for ext in include_exts[basedir])
                    if not include:
                        continue

                    z.write(
                        os.path.join(dirpath, filename),
                        os.path.join(os.path.basename(dirpath), filename)
                    )

        z.close()
        f.seek(0)

        return f

    def refresh(self):
        """Reload the task object from the database to have the latest
        changes"""
        db_task = db.view_task(self.db_task.id)
        self.set_task(db_task)

    def set_status(self, status):
        """Set the task to given status in the database and update the
        dbtask object to have the new status"""
        db.set_status(self.db_task.id, status)
        self.refresh()

    def __getitem__(self, item):
        """Make Task.db_task readable as dictionary"""
        return self.task_dict[item]

    def __setitem__(self, key, value):
        """Make value assignment to Task.db_task possible"""
        self.task_dict[key] = value

    @property
    def id(self):
        return self.task_dict.get("id")

    @property
    def type(self):
        return self.task_dict.get("type")

    @property
    def target(self):
        return self.task_dict.get("target")

    @property
    def category(self):
        return self.task_dict.get("category")

    @property
    def targets(self):
        return self.task_dict.get("targets")

    @property
    def timeout(self):
        return self.task_dict.get("timeout")

    @property
    def priority(self):
        return self.task_dict.get("priority")

    @property
    def custom(self):
        return self.task_dict.get("custom")

    @property
    def owner(self):
        return self.task_dict.get("owner")

    @property
    def machine(self):
        return self.task_dict.get("machine")

    @property
    def package(self):
        return self.task_dict.get("package")

    @property
    def tags(self):
        return self.task_dict.get("tags")

    @property
    def options(self):
        return self.task_dict.get("options")

    @property
    def platform(self):
        return self.task_dict.get("platform")

    @property
    def memory(self):
        return self.task_dict.get("memory")

    @property
    def enforce_timeout(self):
        return self.task_dict.get("enforce_timeout")

    @property
    def clock(self):
        return self.task_dict.get("clock")

    @property
    def added_on(self):
        return self.task_dict.get("added_on")

    @property
    def start_on(self):
        return self.task_dict.get("start_on")

    @property
    def started_on(self):
        return self.task_dict.get("started_on")

    @property
    def completed_on(self):
        return self.task_dict.get("completed_on")

    @property
    def status(self):
        return self.task_dict.get("status")

    @property
    def sample_id(self):
        return self.task_dict.get("sample_id")

    @property
    def submit_id(self):
        return self.task_dict.get("submit_id")

    @property
    def longterm_id(self):
        return self.task_dict.get("longterm_id")

    @property
    def processing(self):
        return self.task_dict.get("processing")

    @property
    def route(self):
        return self.task_dict.get("route")
