#!/usr/bin/env bash
set -e

SSH_DIR="${SSH_DIR:-$HOME/.ssh}"
SSH_PRIVATE_KEY_NAME="${SSH_PRIVATE_KEY_NAME:-id_ed25519}"
SSH_PRIVATE_KEY_PATH="${SSH_DIR}/${SSH_PRIVATE_KEY_NAME}"
SSH_PUBLIC_KEY_NAME="${SSH_PUBLIC_KEY_NAME:-id_ed25519.pub}"
SSH_PUBLICK_KEY_PATH="${SSH_DIR}/${SSH_PUBLIC_KEY_NAME}"

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
rm -rf "${CLONE_PATH}/*"
rm -rf "packages/${PACKAGE_NAME}/dist"
cp -r "packages/${PACKAGE_NAME}/*" "${CLONE_PATH}/"
cd "${CLONE_PATH}"
git config user.name "${GIT_USER_NAME}"
git config user.email "${GIT_USER_EMAIL}"
git config gpg.format ssh
git config user.signingkey "${SSH_PUBLIC_KEY_PATH}"
git add .
git commit -m "Update models to ${PACKAGE_VERSION} version"
git push
git checkout -b "${PACKAGE_VERSION}"
git push "${PACKAGE_VERSION}"
