#!/usr/bin/env python

import json
import os

lockfile = {}

with open("Pipfile.lock") as json_file:
    lockfile = json.load(json_file)

with open("yupana-manifest", "w") as manifest:
    for name, value in sorted(lockfile["default"].items()):
        if "version" in value:
            version = value["version"].replace("=", "")
            manifest.write("mgmt_services/subscriptions:yupana/python-%s:%s.pipfile\n" % (name, version))
        elif "ref" in value:
            ref = value["ref"]
            manifest.write("mgmt_services/subscriptions:yupana/python-%s:%s.pipfile\n" % (name, ref))
        else:
            raise "unable to parse %s" % value
