#!/usr/bin/env bash
set -e

LOCAL_VERSION=$(uv version --package "${PACKAGE_NAME}" --short)
echo "Local version: ${LOCAL_VERSION}"

REMOTE_VERSION=$(curl -s "${PACKAGE_REMOTE_VERSION_URL}" | jq -r .info.version)
echo "Remote version: ${REMOTE_VERSION}"

{
  echo "package_name=${PACKAGE_NAME}"
  echo "local_version=${LOCAL_VERSION}"
  echo "remote_version=${REMOTE_VERSION}"
} >> "${GITHUB_OUTPUT}"

if [ "${LOCAL_VERSION}" = "${REMOTE_VERSION}" ]; then
  echo "version_exists=true" >> "${GITHUB_OUTPUT}"
  echo "Version ${LOCAL_VERSION} already exists on PyPI. Nothing to do."
else
  echo "version_exists=false" >> "${GITHUB_OUTPUT}"
  echo "Version ${LOCAL_VERSION} is new."
fi
