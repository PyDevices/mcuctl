# SPDX-FileCopyrightText: 2026 Brad Barnett
#
# SPDX-License-Identifier: MIT

"""Work out what an OmniPython package needs, from the computer.

A board with no network can't run ``omip`` on itself, so the host does the same
walk and copies the files. A package is looked for on the OmniPython index and
then on micropython-lib, and whatever it names under ``upstream`` in its JSON
comes from micropython-lib. ``mip`` and mpremote ignore that key.

Nothing here touches a board: `walk` returns the files, and the sidecar copies
them. A package the board's firmware has frozen in is left out, as ``omip``
leaves it out on the board, when the plan is given a way to ask the board
(`Plan.firmware`).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

INDEX = "https://lib.omnipython.org"
UPSTREAM = "https://micropython.org/pi/v2"
#: The one file that gives a board ``omip.install``, the bootstrap ``mip`` uses too.
OMIP_FILE = "github:PyDevices/omnipython/lib/omip.py"

_URL_PREFIXES = ("http://", "https://", "github:", "gitlab:", "codeberg:")
_HOSTS = {
    "codeberg:": "codeberg.org/api/v1/repos/{}/{}/raw/{p}?ref={}",
    "github:": "raw.githubusercontent.com/{}/{}/{}/{p}",
    "gitlab:": "gitlab.com/{}/{}/-/raw/{}/{p}",
}
_TIMEOUT = 30


class OmipError(RuntimeError):
    """A package or a file it needs can't be had."""


@dataclass
class Root:
    """A package asked for, or one that names its own folder; its files land in ``folder``."""

    folder: Optional[str] = None  # None: the plan's library folder


@dataclass
class PlannedFile:
    path: str  # relative to the folder of its root
    url: str
    short_hash: Optional[str] = None  # set when the file comes from an index
    root: Root = field(default_factory=Root)


@dataclass
class Plan:
    """What `walk` found for one package, laid out as ``omip.install`` on a board would.

    ``target`` is the folder given to install into. A package that names its
    own folder under ``target`` in its JSON, as an app does, lands beside it
    when it is a ``lib`` folder and inside it otherwise, and what it needs goes
    in a ``lib`` folder: `dest` says where each file goes. The rule is the
    board's, in omnipython's ``lib/omip.py``.
    """

    channel: str  # "py", or the board's .mpy version
    target: str = "/lib"
    files: list[PlannedFile] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    libs: str = ""
    roots: list[Root] = field(default_factory=list)
    #: Asks the board about module names: ``firmware(names, libs)`` gives each
    #: one's state ("frozen", "built-in" or None) and the modules the firmware
    #: left out on purpose, or None when a copy in ``libs`` would run ahead of
    #: a frozen one. Without it, nothing counts as frozen.
    firmware: Optional[Callable[[list[str], str], Optional[tuple[list[Optional[str]], tuple[str, ...]]]]] = None
    logs: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.target = self.target.rstrip("/") or "/"
        self.libs = self.libs or self.target

    def dest(self, file: PlannedFile) -> str:
        """Where ``file`` goes on the board."""
        return join(file.root.folder or self.libs, file.path)


def join(folder: str, path: str) -> str:
    return folder.rstrip("/") + "/" + path


def beside(target: str, folder: str) -> str:
    """Where a package's own folder is: beside a target named ``lib``, inside any other."""
    if folder.startswith("/"):
        return folder.rstrip("/")
    folder = folder.strip("/")
    head, slash, last = target.rpartition("/")
    if last == "lib":
        return head + slash + folder
    return target.rstrip("/") + "/" + folder


def libs_for_app(target: str) -> str:
    """The library folder for what an app needs: ``target`` when it is a ``lib`` folder, else inside it."""
    if target.rpartition("/")[2] == "lib":
        return target
    return target.rstrip("/") + "/lib"


def fetch(url: str) -> Optional[bytes]:
    """The bytes at ``url``, or None when the server says it isn't there."""
    # Some hosts refuse urllib's own user agent.
    request = urllib.request.Request(url, headers={"User-Agent": "mcuctl"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise OmipError(f"HTTP {e.code} from {url}") from e
    except (urllib.error.URLError, OSError) as e:
        raise OmipError(f"Can't reach {url}: {getattr(e, 'reason', e)}") from e


def module_names(paths: list[str]) -> list[str]:
    """The modules a package's files would be imported as, leaving out those inside another of them."""
    names = []
    for path in paths:
        stem, dot, ext = path.rpartition(".")
        if not dot or ext not in ("py", "mpy"):
            continue
        if stem.endswith("/__init__"):
            stem = stem[: -len("/__init__")]
        names.append(stem.replace("/", "."))
    return [n for n in names if not any(n.startswith(other + ".") for other in names)]


def in_modules(path: str, names: list[str]) -> bool:
    """Whether the file at ``path`` belongs to one of the modules named."""
    stem, dot, ext = path.rpartition(".")
    dotted = (stem if dot and ext in ("py", "mpy") else path).replace("/", ".")
    return any(dotted == name or dotted.startswith(name + ".") for name in names)


def frozen_part(plan: Plan, spec: str, paths: list[str]) -> tuple[list[str], bool]:
    """What of a package not to copy, as (modules, whole): the board's own rule, in omnipython's ``lib/omip.py``.

    The modules are the ones the firmware has frozen in, where they would win,
    and the ones it left out on purpose; ``whole`` is True when that is all of
    them. A module built in counts only beside frozen ones, as a package may
    extend a built-in module.
    """
    names = module_names(paths)
    if not names or plan.firmware is None:
        return [], False
    answer = plan.firmware(names, plan.libs)
    if answer is None:
        return [], False
    found, left_out = answer
    if "frozen" not in found:
        return [], False
    missing = [n for n, f in zip(names, found) if f is None and n.split(".")[0] not in left_out]
    if not missing:
        plan.logs.append(f"Skipping {spec}: it's frozen into this firmware, and that copy runs ahead of one in {plan.libs}.")
        return names, True
    plan.logs.append(f"Installing only {', '.join(missing)} of {spec}: the firmware has the rest frozen in.")
    return [n for n, f in zip(names, found) if f == "frozen" or (f is None and n not in missing)], False


def _is_url(spec: str) -> bool:
    return spec.startswith(_URL_PREFIXES)


def _rewrite_url(url: str, version: Optional[str]) -> str:
    for host, form in _HOSTS.items():
        if url.startswith(host):
            parts = url[len(host) :].split("/")
            return "https://" + form.format(parts[0], parts[1], version or "HEAD", p="/".join(parts[2:]))
    return url


def _read_json(url: str) -> Optional[dict]:
    body = fetch(url)
    if body is None:
        return None
    try:
        return json.loads(body)
    except ValueError as e:
        raise OmipError(f"Not JSON at {url}: {e}") from e


def walk(plan: Plan, spec: str, version: Optional[str], indexes: list[str], owner: Optional[Root] = None) -> None:
    """Add the files of ``spec``, and of everything it needs, to ``plan``.

    ``owner`` is the root whose folder ``spec``'s files land in; with none,
    ``spec`` is a root of its own.
    """
    if spec in plan.seen:
        return
    plan.seen.add(spec)
    entry = Root()
    if owner is None:
        owner = entry
        plan.roots.append(entry)
    source = indexes[0]
    base = None
    if _is_url(spec):
        url = _rewrite_url(spec, version)
        if spec.endswith((".py", ".mpy")):
            name = spec.rsplit("/", 1)[-1]
            if not frozen_part(plan, spec, [name])[1]:
                plan.files.append(PlannedFile(name, url, root=owner))
            return
        if not spec.endswith(".json"):
            url = url.rstrip("/") + "/package.json"
        body = _read_json(url)
        if body is None:
            raise OmipError(f"Package not found: {url}")
        base = url.rpartition("/")[0]
    else:
        for index in indexes:
            body = _read_json(f"{index}/package/{plan.channel}/{spec}/{version or 'latest'}.json")
            if body is not None:
                source = index
                break
        else:
            raise OmipError(f"Package not found: {spec} (looked on {', '.join(indexes)})")
        indexes = [source]
    folder = body.get("target")
    if folder:
        # Its own folder: what it needs is a root of its own, bound for the library folder.
        if entry is not owner:
            plan.roots.append(entry)
            owner = entry
        entry.folder = beside(plan.target, folder)
        if entry is plan.roots[0]:
            plan.libs = libs_for_app(plan.target)
    hashes = list(body.get("hashes", ()))
    urls = list(body.get("urls", ()))
    if not folder:
        drop, whole = frozen_part(plan, spec, [path for path, _ in hashes] + [path for path, _ in urls])
        if whole:
            hashes = urls = []
        elif drop:
            hashes = [h for h in hashes if not in_modules(h[0], drop)]
            urls = [u for u in urls if not in_modules(u[0], drop)]
    for path, short_hash in hashes:
        plan.files.append(PlannedFile(path, f"{source}/file/{short_hash[:2]}/{short_hash}", short_hash, owner))
    for path, url in urls:
        if base and not _is_url(url):
            url = f"{base}/{url[2:] if url.startswith('./') else url}"
        plan.files.append(PlannedFile(path, _rewrite_url(url, version), root=owner))
    for dep, dep_version in body.get("deps", ()):
        walk(plan, dep, dep_version, indexes, None if folder else owner)
    for dep, dep_version in body.get("upstream", ()):
        walk(plan, dep, dep_version, [UPSTREAM])
