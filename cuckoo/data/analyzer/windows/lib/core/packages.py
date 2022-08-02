# Copyright (C) 2012-2013 Claudio Guarnieri.
# Copyright (C) 2014-2018 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import logging

from lib.common.exceptions import CuckooPackageError
from modules.packages import pkgs

log = logging.getLogger(__name__)

def has_com_exports(exports):
    com_exports = [
        "DllInstall",
        "DllCanUnloadNow",
        "DllGetClassObject",
        "DllRegisterServer",
        "DllUnregisterServer",
    ]

    return all(name in exports for name in com_exports)

def get_package_class(package):
    """Return a class object for the given analysis package name
    @param package: A lowercase string name of an analysis package"""

    package = package.lower()
    for pkg in pkgs:
        clsname, modname, pkg_cls = pkg

        if clsname.lower() == package or modname.lower() == package:
            return pkg_cls

    return None

def choose_package(config):
    """Try to automatically select an analysis package using
    the category and filename, type if available.
    @param config: Analyzer config object or dict containing: category,
    file_name, and file_type.
    """
    category = config.get("category")
    filename = config.get("file_name", "")
    package = None

    # TODO ability to configure default URL analysis package?
    if category == "url":
        # The default analysis package for the URL category is ie.
        package = "ie"

    elif category == "file":
        for pkg, search in packages.iteritems():

            # First search for file extensions. These have precedence over
            # the file type
            if filename.lower().endswith(search.get("extension", ())):
                package = pkg
                break

            for ftype in search.get("file_types", ()):
                if ftype.lower() in config.get("file_type", "").lower():
                    package = pkg

                    if custom_handler := search.get("custom"):
                        package = custom_handler(config)
                    break

            if package:
                break

        # If no matching analysis package was found, use the generic one
        package = "generic" if package is None else package

    else:
        raise CuckooPackageError("Unsupported category: '%s'" % category)

    return get_package_class(package)

def _handle_dll(config):
    if config.get("file_name", "").lower().endswith(".cpl"):
        return "cpl"
    elif has_com_exports(config.get("pe_exports").split(",")):
        return "com"
    else:
        return "dll"

# Dict where the key is the package that should be selected if
# the a string from file_types matches or extension matches extension from
# extension. Custom is optional and can contain a function object. It can be
# used to provide further package selection logic and should return the string
# name of an existing analysis package.
packages = {
    "dll": {
        "file_types": ("DLL"),
        "extension": (".dll"),
        "custom": _handle_dll
    },
    "cpl": {
        "file_types": (),
        "extension": (".cpl")
    },
    "exe": {
        "file_types": ("PE32", "MS-DOS"),
        "extension": (".exe")
    },
    "pdf": {
        "file_types": ("PDF"),
        "extension": (".pdf")
    },
    "pub": {
        "file_types": (),
        "extension": (".pub")
    },
    "hwp": {
        "file_types": ("Hangul (Korean) Word Processor File 5.x"),
        "extension": (".hwp")
    },
    "doc": {
        "file_types": (
            "Rich Text Format", "Microsoft Word", "Microsoft Office Word",
            "Microsoft OOXML"
        ),
        "extension": (".doc", ".docx", ".rtf", ".docm")
    },
    "xls": {
        "file_types": (
            "Microsoft Excel", "Microsoft Office Excel",
        ),
        "extension": (".xls", ".xlsx", ".xlt", ".xlsm", ".iqy", ".slk")
    },
    "ppt": {
        "file_types": (
            "Microsoft PowerPoint", "Microsoft Office PowerPoint",
        ),
        "extension": (
            ".ppt", ".pptx", ".pps", ".ppsx", ".pptm", ".potm", ".potx",
            ".ppsm"
        )
    },
    "jar": {
        "file_types": ("Java archive data"),
        "extension": (".jar")
    },
    "hta": {
        "file_types": (),
        "extension": (".hta")
    },
    "zip": {
        "file_types": ("Zip"),
        "extension": (".zip")
    },
    "python": {
        "file_types": ("Python script"),
        "extension": (".py", ".pyc")
    },
    "vbs": {
        "file_types": (),
        "extension": (".vbs")
    },
    "js": {
        "file_types": (),
        "extension": (".js")
    },
    "jse": {
        "file_types": (),
        "extension": (".jse")
    },
    "msi": {
        "file_types": ("MSI Installer"),
        "extension": (".msi")
    },
    "ps1": {
        "file_types": (),
        "extension": (".ps1")
    },
    "wsf": {
        "file_types": (),
        "extension": (".wsf,", ".wsc")
    },
    "ie": {
        "file_types": ("HTML"),
        "extension": (".htm", ".html", ".mht", ".mhtml", ".url", "swf")
    }
}
