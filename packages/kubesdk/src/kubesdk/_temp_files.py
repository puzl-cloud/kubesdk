# SPDX-License-Identifier: MIT
# Portions of this file are derived from the Kopf project:
#   https://github.com/nolar/kopf
# Copyright (c) 2020 Sergey Vasilyev <nolar@nolar.info>
# Copyright (c) 2019-2020 Zalando SE
# Licensed under the MIT License; see the LICENSE file or https://opensource.org/licenses/MIT
import os
import tempfile

from typing import Mapping, Iterator


class _TempFiles(Mapping[bytes, str]):
    """
    A container for the temporary files, which are purged on garbage collection.

    The files are purged when the container is garbage-collected. The container
    is garbage-collected when its parent `APISession` is garbage-collected or
    explicitly closed (by `Vault` on removal of corresponding credentials).
    """
    _path_suffix: str
    _paths: dict[bytes, str]

    def __init__(self, path_suffix: str) -> None:
        super().__init__()
        self._paths: dict[bytes, str] = {}
        self._path_suffix = path_suffix

    def __del__(self) -> None:
        self.purge()

    def __len__(self) -> int:
        return len(self._paths)

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._paths)

    def __getitem__(self, item: bytes) -> str:
        if item not in self._paths:
            with tempfile.NamedTemporaryFile(delete=False, suffix=self._path_suffix) as f:
                f.write(item)
            self._paths[item] = f.name
        return self._paths[item]

    def purge(self) -> None:
        for _, path in self._paths.items():
            try:
                os.remove(path)
            except OSError:
                pass  # already removed
        self._paths.clear()
