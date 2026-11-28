"""Directory edges learned from Drive queries, scoped to one filesystem."""

from collections import defaultdict
from contextlib import contextmanager
from itertools import count
from threading import RLock
from typing import NamedTuple

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


class FolderLocation(NamedTuple):
    parent: str
    name: str
    revision: int


class FolderListing:
    """Accumulate one logical listing across all pages and parent batches."""

    def __init__(self, parents, name, generation, revision):
        self.revision = revision
        self.name = name
        self.generation = generation
        self.folders: dict[str, dict[str, list[str]]] = {
            p: {} for p in parents
        }
        self.files: set[tuple[str, str]] = set()
        self.complete = True

    def add(self, parent, item):
        # Drive v3 defines one parent per item. An ambiguous response cannot
        # establish which names are complete for either parent.
        if parent is None or len(item.get("parents", ())) > 1:
            self.complete = False
        elif item["mimeType"] == FOLDER_MIME_TYPE:
            self.folders[parent].setdefault(item["name"], []).append(
                item["id"]
            )
        else:
            self.files.add((parent, item["name"]))


class DirectoryCache:
    """Keep children attached to Drive IDs across moves.

    Mutations can invalidate an operation's generation. Reads are ordered by
    when each listing started, including when an ID appears under a new name.
    """

    def __init__(self, root: str):
        self.root = root
        self.lock = RLock()
        self.generation = 0
        self._revisions = count()
        self._pending: dict[FolderListing, None] = {}
        self.children: dict[str, dict[str, tuple[str, ...]]] = {}
        # Each child records its parent/name and the revision that placed it.
        self.parents: dict[str, FolderLocation] = {}
        # A mutation can leave the set of matching folders uncertain.
        self.incomplete: set[tuple[str, str]] = set()

        # A file with this name matters only when selecting the final item ID.
        self.file_collisions: set[tuple[str, str]] = set()

    def _folders(self, parents, name, *, folders_only):
        result: list[str] = []
        for parent in parents:
            ids = self.children.get(parent, {}).get(name)
            edge = (parent, name)
            if (
                ids is None
                or edge in self.incomplete
                or (not folders_only and edge in self.file_collisions)
            ):
                return None
            result.extend(ids)
        return result or None

    def lookup(self, parents, name, generation, *, folders_only):
        with self.lock:
            if generation == self.generation:
                return self._folders(parents, name, folders_only=folders_only)
            return None

    def prefix(self, path, generation, *, folders_only=False):
        parts = path.split("/") if path else []
        folders = [self.root]
        with self.lock:
            if generation != self.generation:
                return "", folders, parts
            for i, name in enumerate(parts):
                found = self._folders(
                    folders,
                    name,
                    folders_only=folders_only or i < len(parts) - 1,
                )
                if not found:
                    return "/".join(parts[:i]), folders, parts[i:]
                folders = found
        return path, folders, []

    def contains(self, path, generation):
        return not self.prefix(path, generation, folders_only=True)[2]

    def warm_parents(self, roots, maxdepth, generation):
        """Include only parents whose contents can contribute to this walk."""
        with self.lock:
            parents = dict.fromkeys(roots)
            if generation != self.generation:
                return parents
            frontier = list(parents)
            depth = 1
            while frontier and (maxdepth is None or depth < maxdepth):
                following = []
                for parent in frontier:
                    for ids in self.children.get(parent, {}).values():
                        for child in ids:
                            if child not in parents:
                                parents[child] = None
                                following.append(child)
                frontier = following
                depth += 1
            return parents

    def _remove_name(self, parent, name):
        self.incomplete.discard((parent, name))
        self.file_collisions.discard((parent, name))
        names = self.children.get(parent)
        if not names:
            return ()
        ids = names.pop(name, ())
        if not names:
            del self.children[parent]
        return ids

    def _unlink(self, item_id):
        parent, name, _ = self.parents.pop(item_id)
        ids = self.children[parent][name]
        if len(ids) == 1:
            self._remove_name(parent, name)
        else:
            self.children[parent][name] = tuple(i for i in ids if i != item_id)

    def _drop_subtrees(self, roots):
        pending = list(roots)
        while pending:
            parent = pending.pop()
            self.parents.pop(parent, None)
            for name, ids in self.children.pop(parent, {}).items():
                self.incomplete.discard((parent, name))
                self.file_collisions.discard((parent, name))
                pending.extend(ids)

    def _known_folder_ids(self, path, *, verified=False) -> tuple[str, ...]:
        ids: tuple[str, ...] = (self.root,)
        for part in path.split("/") if path else ():
            if len(ids) == 1:
                if verified and (ids[0], part) in self.incomplete:
                    return ()
                ids = self.children.get(ids[0], {}).get(part, ())
            else:
                ids = tuple(
                    child
                    for parent in ids
                    if not verified or (parent, part) not in self.incomplete
                    for child in self.children.get(parent, {}).get(part, ())
                )
            if not ids:
                break
        return ids

    def has_directory(self, path, mount):
        # One known folder suffices for exists and makedirs. Operations that
        # select a specific Drive ID must resolve all same-name matches.
        with self.lock:
            if mount and not self._known_folder_ids(mount, verified=True):
                return False
            parent_path, _, name = path.rpartition("/")
            parents = self._known_folder_ids(parent_path, verified=True)
            return any(
                (parent, name) not in self.incomplete
                and self.children.get(parent, {}).get(name)
                for parent in parents
            )

    def _parents_of_path(self, path):
        parent, _, name = path.rpartition("/")
        return self._known_folder_ids(parent), name

    def invalidate(self, path):
        with self.lock:
            self.generation += 1
            if not path:
                self.children.clear()
                self.parents.clear()
                self.incomplete.clear()
                self.file_collisions.clear()
                return
            parents, name = self._parents_of_path(path)
            for parent in parents:
                self._drop_subtrees(self._remove_name(parent, name))

    def removed(self, item_id, paths):
        """Discard this ID. Recheck affected names without evicting siblings."""
        with self.lock:
            self.generation += 1
            if item_id in self.parents:
                self._unlink(item_id)
            self._drop_subtrees([item_id])
            for path in paths:
                parents, name = self._parents_of_path(path)
                for parent in parents:
                    if name in self.children.get(parent, {}):
                        self.incomplete.add((parent, name))

    def moved(self, generation, item_id, parent, name):
        """Relink a known folder while retaining all edges below its ID."""
        with self.lock:
            if generation != self.generation or item_id not in self.parents:
                return False
            old_parent = self.parents[item_id].parent
            # A rename leaves the physical parent unchanged.
            if parent is None:
                parent = old_parent
            self._unlink(item_id)
            edge = (parent, name)
            names = self.children.setdefault(parent, {})
            names[name] = (*names.get(name, ()), item_id)
            self.parents[item_id] = FolderLocation(
                parent, name, next(self._revisions)
            )
            # The destination can already contain same-name files or folders.
            self.incomplete.add(edge)
            self.generation += 1
            return True

    def created(self, listing, parent, item_id):
        # The empty name lookup and the folder POST share this listing. A
        # concurrent file write can supersede it until the POST has finished.
        with self.lock:
            self._supersede((parent,), listing.name, exclude=listing)
            if (
                listing.generation != self.generation
                and listing.name in self.children.get(parent, {})
            ):
                self.incomplete.add((parent, listing.name))
            listing.folders[parent][listing.name] = [item_id]

    def _supersede(
        self, parents, name, *, until=None, exclude=None, retired=False
    ):
        # A newer completed listing can confirm an absence before an older
        # request has even published its first edge for that name.
        for pending in self._pending:
            if pending is until:
                break
            if pending is exclude:
                continue
            same_name = name is None or pending.name in (None, name)
            if retired or (
                same_name and not pending.folders.keys().isdisjoint(parents)
            ):
                pending.generation = None

    def file_added(self, parent_id, name, *, parent_path=None):
        with self.lock:
            # A rename can leave the physical parent unchanged without ever
            # requesting its ID. Recheck the name under each possible parent.
            parents = (
                self._known_folder_ids(parent_path)
                if parent_id is None
                else (parent_id,)
            )
            self._supersede(parents, name)
            for parent in parents:
                if name in self.children.get(parent, {}):
                    self.file_collisions.add((parent, name))

    @contextmanager
    def listing(self, generation, parents, *, name=None):
        with self.lock:
            listing = FolderListing(
                parents, name, generation, next(self._revisions)
            )
            self._pending[listing] = None
        try:
            yield listing
            with self.lock:
                if listing.complete and listing.generation == self.generation:
                    self._observe(listing, name)
        finally:
            with self.lock:
                del self._pending[listing]

    def _observe(self, listing, name):
        groups = listing.folders
        # Parents resolved by the caller can disappear while Drive is listing.
        # Publishing children under detached IDs would create unreachable edges.
        if any(p != self.root and p not in self.parents for p in groups):
            groups = {
                p: names
                for p, names in groups.items()
                if p == self.root or p in self.parents
            }
            if not groups:
                listing.generation = None
                return
        if not self._relink(groups, listing.revision):
            listing.generation = None
            return
        retired: list[str] = []
        has_folder = any(groups.values())
        for parent, names in groups.items():
            previous = self.children.get(parent, {})
            keys = (
                (name,)
                if name is not None
                else dict.fromkeys((*names, *previous))
            )
            for key in keys:
                before = previous.get(key)
                ids = tuple(names.get(key, ()))
                edge = (parent, key)
                if (
                    before
                    and ids
                    and before != ids
                    and set(before) == set(ids)
                ):
                    ids = before
                if before and before != ids:
                    retired.extend(set(before).difference(ids))
                # Remember an empty sibling only if this exact name matched
                # a folder under another queried parent. Never cache misses alone.
                if ids or (name is not None and has_folder):
                    self.children.setdefault(parent, {})[key] = ids
                    location = FolderLocation(parent, key, listing.revision)
                    for item_id in ids:
                        self.parents[item_id] = location
                    self.incomplete.discard(edge)
                    if edge in listing.files:
                        self.file_collisions.add(edge)
                    else:
                        self.file_collisions.discard(edge)
                else:
                    self._remove_name(parent, key)
        self._drop_subtrees(retired)
        # Retired IDs have no reverse entry left to check against an older
        # result. Only reads started before this observation need that barrier.
        self._supersede(groups, name, until=listing, retired=bool(retired))

    def _relink(self, groups, revision):
        # Unlink moved IDs before pruning absent names. Their children stay
        # attached to the same ID even when an ancestor changes name or parent.
        moved = defaultdict(set)
        for parent, names in groups.items():
            for name, ids in names.items():
                for item_id in ids:
                    old = self.parents.get(item_id)
                    if old is not None and (
                        old.parent != parent or old.name != name
                    ):
                        if old.revision > revision:
                            return False
                        moved[old.parent, old.name].add(item_id)
        for (parent, name), leaving in moved.items():
            remaining = tuple(
                i for i in self.children[parent][name] if i not in leaving
            )
            if remaining:
                self.children[parent][name] = remaining
            else:
                self._remove_name(parent, name)
        return True
