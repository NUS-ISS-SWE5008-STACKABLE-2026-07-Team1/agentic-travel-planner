#!/usr/bin/env bash
# Point deploy/k8s/app at one built image: set_image.sh IMAGE TAG [KUSTOMIZATION]
#
# Called by .github/workflows/deploy-gke.yml in its own checkout; the result
# is applied, never committed. tests/test_set_image.py runs this same script.
#
# The tag is QUOTED. It is a short commit id, and YAML reads some of those as
# numbers: 7750e40 is 7750 x 10^40 and 1234567 is an integer. kustomize wants
# a string and refused the whole file — the failed deploy of 7750e40
# (2026-10-03). About one commit id in 25 looks like a number.
set -euo pipefail

image="$1"
tag="$2"
file="${3:-deploy/k8s/app/kustomization.yaml}"

sed -i "s|^\(    newName:\).*|\1 ${image}|" "$file"
sed -i "s|^\(    newTag:\).*|\1 \"${tag}\"|" "$file"

# Fail loudly if either substitution missed (the layout of the file changed).
grep -q "^    newName: ${image}$" "$file" \
  || { echo "::error::could not set the image location"; exit 1; }
grep -q "^    newTag: \"${tag}\"$" "$file" \
  || { echo "::error::could not set the image tag"; exit 1; }
