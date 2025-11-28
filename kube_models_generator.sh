#!/usr/bin/env bash

K8S_REPO_URL="${K8S_REPO_URL:-https://github.com/kubernetes/kubernetes}"
K8S_OPENAPI_SPEC_PATH="${K8S_OPENAPI_SPEC_PATH:-api/openapi-spec/v3}"
K8S_VERSIONS="${K8S_VERSIONS:-1.23 1.24 1.25 1.26 1.27 1.28 1.29 1.30 1.31 1.32 1.33 1.34}"

OPENAPI_SPEC_DIR="${OPENAPI_SPEC_DIR:-.run/kube_openapi_spec}"
DATA_MODEL_DIR="${DATA_MODEL_DIR:-packages/kube_models/src/kube_models}"

mkdir -p "${OPENAPI_SPEC_DIR}"

download_version() {
    local K8S_VERSION=$1
    local REPO_URL=$2
    local SPEC_DIR=$3
    local SPEC_PATH=$4
    
    local K8S_VERSION_DIR="${SPEC_DIR}/${K8S_VERSION}"

    if [ -d "${K8S_VERSION_DIR}" ]; then
        echo "Directory ${K8S_VERSION_DIR} already exists, skipping"
        return 0
    fi

    git clone \
        --depth 1 \
        --filter=blob:none \
        --sparse \
        --branch "release-${K8S_VERSION}" \
        "${REPO_URL}" \
        "${K8S_VERSION_DIR}"
    
    cd "${K8S_VERSION_DIR}" || exit
    git sparse-checkout set "${SPEC_PATH}"
    cd - > /dev/null || exit
}

echo "=== Downloading k8s openapi specs ==="
MAX_PARALLEL_DOWNLOAD=${MAX_PARALLEL_DOWNLOAD:-5}
COUNT=0

for K8S_VERSION in ${K8S_VERSIONS}; do
    download_version "${K8S_VERSION}" "${K8S_REPO_URL}" "${OPENAPI_SPEC_DIR}" "${K8S_OPENAPI_SPEC_PATH}" &
    
    ((COUNT++))
    if (( COUNT >= MAX_PARALLEL_DOWNLOAD )); then
        wait -n
        ((COUNT--))
    fi
done
wait

echo "=== Generating data models ==="
for K8S_VERSION in ${K8S_VERSIONS}; do
    K8S_VERSION_DIR="${OPENAPI_SPEC_DIR}/${K8S_VERSION}"
    
    echo "Generate python Data Model for k8s version: ${K8S_VERSION}"
    uv run packages/kubesdk_cli/src/kubesdk_cli/cli.py \
        --from-dir "${K8S_VERSION_DIR}/${K8S_OPENAPI_SPEC_PATH}" \
        --output "${DATA_MODEL_DIR}"
done
