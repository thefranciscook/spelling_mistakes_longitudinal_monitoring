"""Pinned linguistic resources fetched once at setup time.

Hunspell dictionaries (SCOWL-derived en_US / en_CA / en_GB and Magyar Ispell for Hungarian)
are downloaded from the npm registry tarballs published by wooorm/dictionaries and verified
against pinned SHA-256 checksums. Nothing is downloaded during normal operation.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path

HUNSPELL_DICTIONARIES = {
    # name: (npm package, version, sha256 of the tarball)
    "en_US": ("dictionary-en", "4.0.0", "160cba5e6c0230d43d70b0c929e498566ff2c3c4bcf840318d98ce28e31ee756"),
    "en_CA": ("dictionary-en-ca", "3.0.0", "e38333de5903f0c7690b6f0eedbadad73e84fede0d8351f99e08c2c4a8231961"),
    "en_GB": ("dictionary-en-gb", "3.0.0", "67b3ac7f78c31408927b9e4e4f55bbd6a338ae6596a5c09bd937e471694fa218"),
    "hu": ("dictionary-hu", "3.0.0", "6a98ce9777d63787fa224728a72e8b4e8f05355d0747eac404e0ebd229ec1a3b"),
}

ENGLISH_VARIANTS = ("en_US", "en_CA", "en_GB")


def hunspell_dir(resources: Path, name: str) -> Path:
    return resources / "hunspell" / name


def hunspell_base(resources: Path, name: str) -> Path:
    """Path prefix accepted by spylls ``Dictionary.from_files`` (without extension)."""
    return hunspell_dir(resources, name) / "index"


def resources_present(resources: Path) -> bool:
    return all((hunspell_base(resources, n).with_suffix(".dic")).exists() for n in HUNSPELL_DICTIONARIES)


def fetch_resources(resources: Path, log=print) -> None:
    for name, (pkg, version, sha) in HUNSPELL_DICTIONARIES.items():
        target = hunspell_dir(resources, name)
        if (target / "index.dic").exists():
            continue
        url = f"https://registry.npmjs.org/{pkg}/-/{pkg}-{version}.tgz"
        log(f"Downloading {pkg}@{version} ...")
        with urllib.request.urlopen(url, timeout=60) as resp:
            blob = resp.read()
        digest = hashlib.sha256(blob).hexdigest()
        if digest != sha:
            raise RuntimeError(f"Checksum mismatch for {pkg}@{version}: {digest}")
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            for member in ("package/index.aff", "package/index.dic", "package/license"):
                data = tar.extractfile(member)
                if data is None:
                    raise RuntimeError(f"{member} missing from {pkg}")
                (target / Path(member).name).write_bytes(data.read())


def resources_fingerprint() -> dict:
    return {name: f"{pkg}@{version}:{sha[:12]}" for name, (pkg, version, sha) in HUNSPELL_DICTIONARIES.items()}
