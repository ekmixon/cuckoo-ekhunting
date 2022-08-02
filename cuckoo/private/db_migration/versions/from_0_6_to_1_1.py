# Copyright (C) 2011-2013 Claudio Guarnieri.
# Copyright (C) 2014-2017 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

"""Database migration (from Cuckoo 0.6-1.0 to 1.1)

Revision ID: 5aa718cc79e1
Revises: None
Create Date: 2014-03-23 23:30:36.756792

"""

# Revision identifiers, used by Alembic.
revision = "5aa718cc79e1"
mongo_revision = "1"
down_revision = None

import sqlalchemy as sa
import sys

from alembic import op
from datetime import datetime
from dateutil.parser import parse

from cuckoo.common.mongo import mongo

old_enum = (
    "pending", "processing", "failure", "success",
)

new_enum = (
    "pending", "running", "completed", "recovered", "reported",
    # These were not actually supported in 1.0 or 1.1, but we have to migrate
    # them somewhere (and they're not handled later on either).
    "failed_analysis", "failed_processing",
)

mapping = {
    "processing": "running",
    "failure": "failed_analysis",
    "success": "completed",
}

old_type = sa.Enum(*old_enum, name="status_type")
new_type = sa.Enum(*new_enum, name="status_type")
tmp_type = sa.Enum(*set(old_enum + new_enum), name="status_type_temp")

def upgrade():
    # BEWARE: be prepared to really spaghetti code. To deal with SQLite limitations in Alembic we coded some workarounds.

    # Migrations are supported starting form Cuckoo 0.6 and Cuckoo 1.0; I need a way to figure out if from which release
    # it will start because both schema are missing alembic release versioning.
    # I check for tags table to distinguish between Cuckoo 0.6 and 1.0.
    conn = op.get_bind()

    if not conn.engine.dialect.has_table(
        conn.engine.connect(), "machines_tags"
    ):
        # We are on Cuckoo < 1.0, hopefully 0.6.
        # So run SQL migration.

        # Create table used by Tag.
        op.create_table(
            "tags",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        )

        # Create secondary table used in association Machine - Tag.
        op.create_table(
            "machines_tags",
            sa.Column("machine_id", sa.Integer, sa.ForeignKey("machines.id")),
            sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id")),
        )

        # Add columns to Machine.
        op.add_column("machines", sa.Column("interface", sa.String(length=255), nullable=True))
        op.add_column("machines", sa.Column("snapshot", sa.String(length=255), nullable=True))
        # TODO: change default value, be aware sqlite doesn't support that kind of ALTER statement.
        op.add_column("machines", sa.Column("resultserver_ip", sa.String(length=255), server_default="192.168.56.1", nullable=False))
        # TODO: change default value, be aware sqlite doesn't support that kind of ALTER statement.
        op.add_column("machines", sa.Column("resultserver_port", sa.String(length=255), server_default="2042", nullable=False))

        # Deal with Alembic shit.
        # Alembic is so ORMish that it was impossible to write code which works on different DBMS.
        if conn.engine.driver == "psycopg2":
            # We don"t provide a default value and leave the column as nullable because o further data migration.
            op.add_column("tasks", sa.Column("clock", sa.DateTime(timezone=False), nullable=True))
            # NOTE: We added this new column so we force clock time to the added_on for old analyses.
            conn.execute("UPDATE tasks SET clock=added_on")
            # Add the not null constraint.
            op.alter_column("tasks", "clock", nullable=False, existing_nullable=True)

            op.execute("ALTER TABLE tasks ALTER COLUMN status DROP DEFAULT")

            tmp_type.create(op.get_bind(), checkfirst=True)
            op.execute(
                "ALTER TABLE tasks ALTER COLUMN status TYPE status_type_temp "
                "USING status::text::status_type_temp"
            )
            old_type.drop(op.get_bind(), checkfirst=False)

            for old_status, new_status in mapping.items():
                op.execute(
                    "UPDATE tasks SET status = '%s' WHERE status = '%s'" %
                    (new_status, old_status)
                )

            new_type.create(op.get_bind(), checkfirst=False)
            op.execute(
                "ALTER TABLE tasks ALTER COLUMN status TYPE status_type "
                "USING status::text::status_type"
            )
            tmp_type.drop(op.get_bind(), checkfirst=False)

            op.execute(
                "ALTER TABLE tasks ALTER COLUMN status "
                "SET DEFAULT 'pending'::status_type"
            )
        elif conn.engine.driver == "mysqldb":
            op.alter_column(
                "tasks", "status", existing_type=old_type, type_=tmp_type
            )

            for old_status, new_status in mapping.items():
                op.execute(
                    "UPDATE tasks SET status = '%s' WHERE status = '%s'" %
                    (new_status, old_status)
                )

            op.alter_column(
                "tasks", "status", existing_type=tmp_type, type_=new_type
            )

            # We don"t provide a default value and leave the column as nullable because o further data migration.
            op.add_column("tasks", sa.Column("clock", sa.DateTime(timezone=False), nullable=True))
            # NOTE: We added this new column so we force clock time to the added_on for old analyses.
            conn.execute("UPDATE tasks SET clock=added_on")
            # Add the not null constraint.
            op.alter_column("tasks", "clock", nullable=False, existing_nullable=True, existing_type=sa.DateTime(timezone=False))
        elif conn.engine.driver == "pysqlite":
            tasks_data = []
            old_tasks = conn.execute(
                "SELECT id, target, category, timeout, priority, custom, "
                "machine, package, options, platform, memory, "
                "enforce_timeout, added_on, started_on, completed_on, status, "
                "sample_id FROM tasks"
            ).fetchall()

            for item in old_tasks:
                d = {
                    "id": item[0],
                    "target": item[1],
                    "category": item[2],
                    "timeout": item[3],
                    "priority": item[4],
                    "custom": item[5],
                    "machine": item[6],
                    "package": item[7],
                    "options": item[8],
                    "platform": item[9],
                    "memory": item[10],
                    "enforce_timeout": item[11],
                }

                if isinstance(item[12], datetime):
                    d["added_on"] = item[12]
                else:
                    d["added_on"] = parse(item[12]) if item[12] else None

                if isinstance(item[13], datetime):
                    d["started_on"] = item[13]
                else:
                    d["started_on"] = parse(item[13]) if item[13] else None

                if isinstance(item[14], datetime):
                    d["completed_on"] = item[14]
                else:
                    d["completed_on"] = parse(item[14]) if item[14] else None

                d["status"] = mapping.get(item[15], item[15])
                d["sample_id"] = item[16]

                # Force clock.
                # NOTE: We added this new column so we force clock time to
                # the added_on for old analyses.
                d["clock"] = d["added_on"]
                tasks_data.append(d)

            # Rename original table.
            op.rename_table("tasks", "old_tasks")
            # Drop old table.
            op.drop_table("old_tasks")
            # Drop old Enum.
            sa.Enum(name="status_type").drop(op.get_bind(), checkfirst=False)
            # Create new table with 1.0 schema.
            op.create_table(
                "tasks",
                sa.Column("id", sa.Integer(), nullable=False),
                sa.Column("target", sa.String(length=255), nullable=False),
                sa.Column("category", sa.String(length=255), nullable=False),
                sa.Column("timeout", sa.Integer(), server_default="0", nullable=False),
                sa.Column("priority", sa.Integer(), server_default="1", nullable=False),
                sa.Column("custom", sa.String(length=255), nullable=True),
                sa.Column("machine", sa.String(length=255), nullable=True),
                sa.Column("package", sa.String(length=255), nullable=True),
                sa.Column("options", sa.String(length=255), nullable=True),
                sa.Column("platform", sa.String(length=255), nullable=True),
                sa.Column("memory", sa.Boolean(), nullable=False, default=False),
                sa.Column("enforce_timeout", sa.Boolean(), nullable=False, default=False),
                sa.Column("clock", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
                sa.Column("added_on", sa.DateTime(timezone=False), nullable=False),
                sa.Column("started_on", sa.DateTime(timezone=False), nullable=True),
                sa.Column("completed_on", sa.DateTime(timezone=False), nullable=True),
                sa.Column("status", sa.Enum(*new_enum, name="status_type"), server_default="pending", nullable=False),
                sa.Column("sample_id", sa.Integer, sa.ForeignKey("samples.id"), nullable=True),
                sa.PrimaryKeyConstraint("id")
            )

            # Insert data.
            op.bulk_insert(Task.__table__, tasks_data)

    # Migrate mongo.
    mongo_upgrade()

def mongo_upgrade():
    """Migrate mongodb schema and data."""
    if mongo.init():
        print "Starting MongoDB migration."
        mongo.connect()

        # Check for schema version and create it.
        if "cuckoo_schema" in mongo.db.collection_names():
            print "Mongo schema version not expected"
            sys.exit()
        else:
            mongo.db.cuckoo_schema.save({"version": mongo_revision})
    else:
        print "Mongo reporting module not enabled, skipping mongo migration."

def downgrade():
    pass


Base = sa.ext.declarative.declarative_base()

TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_RECOVERED = "recovered"
TASK_REPORTED = "reported"
TASK_FAILED_ANALYSIS = "failed_analysis"
TASK_FAILED_PROCESSING = "failed_processing"
TASK_FAILED_REPORTING = "failed_reporting"

tasks_tags = sa.Table(
    "tasks_tags", Base.metadata,
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id")),
    sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id"))
)

class Task(Base):
    """Analysis task queue."""
    __tablename__ = "tasks"

    id = sa.Column(sa.Integer(), primary_key=True)
    target = sa.Column(sa.Text(), nullable=False)
    category = sa.Column(sa.String(255), nullable=False)
    timeout = sa.Column(sa.Integer(), server_default="0", nullable=False)
    priority = sa.Column(sa.Integer(), server_default="1", nullable=False)
    custom = sa.Column(sa.String(255), nullable=True)
    owner = sa.Column(sa.String(64), nullable=True)
    machine = sa.Column(sa.String(255), nullable=True)
    package = sa.Column(sa.String(255), nullable=True)
    tags = sa.orm.relationship(
        "Tag", secondary=tasks_tags, cascade="all, delete", single_parent=True,
        backref=sa.orm.backref("task", cascade="all"), lazy="subquery"
    )
    options = sa.Column(sa.String(255), nullable=True)
    platform = sa.Column(sa.String(255), nullable=True)
    memory = sa.Column(sa.Boolean, nullable=False, default=False)
    enforce_timeout = sa.Column(sa.Boolean, nullable=False, default=False)
    clock = sa.Column(
        sa.DateTime(timezone=False),default=datetime.now, nullable=False
    )
    added_on = sa.Column(
        sa.DateTime(timezone=False), default=datetime.now, nullable=False
    )
    started_on = sa.Column(sa.DateTime(timezone=False), nullable=True)
    completed_on = sa.Column(sa.DateTime(timezone=False), nullable=True)
    status = sa.Column(
        sa.Enum(
            TASK_PENDING, TASK_RUNNING, TASK_COMPLETED, TASK_REPORTED,
            TASK_RECOVERED, TASK_FAILED_ANALYSIS, TASK_FAILED_PROCESSING,
            TASK_FAILED_REPORTING, name="status_type"
        ),
        server_default=TASK_PENDING, nullable=False
    )
    sample_id = sa.Column(
        sa.Integer, sa.ForeignKey("samples.id"), nullable=True
    )
    sample = sa.orm.relationship("Sample", backref="tasks")
    guest = sa.orm.relationship(
        "Guest", uselist=False, backref="tasks", cascade="save-update, delete"
    )
    errors = sa.orm.relationship(
        "Error", backref="tasks", cascade="save-update, delete"
    )
