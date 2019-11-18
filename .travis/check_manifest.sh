#!/bin/bash

python scripts/create_manifest.py

changed=`git diff --name-only HEAD`

if [[ $changed == *"yupana-manifest"* ]]; then
  echo "Pipfile.lock changed without updating yupana-manifest. Run 'python scripts/create_manifest.py' to update."
  exit 1
else
  echo "Manifest is up to date."
  exit 0
fi