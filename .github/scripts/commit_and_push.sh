#!/usr/bin/env bash
set -euo pipefail

SSH_DIR="${SSH_DIR:-$HOME/.ssh}"
SSH_PRIVATE_KEY_NAME="${SSH_PRIVATE_KEY_NAME:-id_ed25519}"
SSH_PRIVATE_KEY_PATH="${SSH_DIR}/${SSH_PRIVATE_KEY_NAME}"
SSH_PUBLIC_KEY_NAME="${SSH_PUBLIC_KEY_NAME:-id_ed25519.pub}"
SSH_PUBLIC_KEY_PATH="${SSH_DIR}/${SSH_PUBLIC_KEY_NAME}"
PACKAGE_SRC_DIR="${PACKAGE_SRC_DIR:-$(pwd)/packages/${PACKAGE_NAME}}"

mkdir -p "${SSH_DIR}"
chmod 700 "${SSH_DIR}"

echo "${GIT_HOST_KEY}" >> "${SSH_DIR}/known_hosts"
echo "${SSH_PRIVATE_KEY}" > "${SSH_PRIVATE_KEY_PATH}"
chmod 600 "${SSH_PRIVATE_KEY_PATH}"
ssh-keygen -y -f "${SSH_PRIVATE_KEY_PATH}" > "${SSH_PUBLIC_KEY_PATH}"
chmod 644 "${SSH_PUBLIC_KEY_PATH}"
eval "$(ssh-agent -s)"
ssh-add "${SSH_PRIVATE_KEY_PATH}"
        
git clone "${TARGET_REPO}" "${CLONE_PATH}"
rm -rf "${PACKAGE_SRC_DIR}/dist"
cd "${CLONE_PATH}"
find . -mindepth 1 -maxdepth 1 ! -name ".git" -exec rm -rf {} +
cp -r "${PACKAGE_SRC_DIR}/." .
git config user.name "${GIT_USER_NAME}"
git config user.email "${GIT_USER_EMAIL}"
git config gpg.format ssh
git config user.signingkey "${SSH_PUBLIC_KEY_PATH}"
git add .
git commit -S -m "Update models to ${PACKAGE_VERSION} version"
git push
git checkout -b "${PACKAGE_VERSION}"
git push origin "${PACKAGE_VERSION}"
