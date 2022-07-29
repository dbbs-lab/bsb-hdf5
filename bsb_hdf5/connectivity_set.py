from bsb.exceptions import *
from .resource import Resource
from bsb.storage._chunks import Chunk
from bsb.storage.interfaces import ConnectivitySet as IConnectivitySet
import numpy as np

_root = "/connectivity/"


class ConnectivitySet(Resource, IConnectivitySet):
    """
    Fetches placement data from storage.

    .. note::

        Use :meth:`Scaffold.get_connectivity_set <bsb.core.Scaffold.get_connectivity_set>`
        to correctly obtain a :class:`~bsb.storage.interfaces.ConnectivitySet`.
    """

    def __init__(self, engine, tag):
        self.tag = tag
        self.pre = None
        self.post = None
        super().__init__(engine, _root + tag)
        with engine._read():
            with engine._handle("r") as h:
                if not self.exists(engine, tag, handle=h):
                    raise DatasetNotFoundError(f"ConnectivitySet '{tag}' does not exist")
                self._pre_name = h[self._path].attrs["pre"]
                self._post_name = h[self._path].attrs["post"]

    @classmethod
    def get_tags(cls, engine):
        with engine._read():
            with engine._handle("r") as h:
                return list(h[_root].keys())

    @classmethod
    def create(cls, engine, pre_type, post_type, tag=None):
        """
        Create the structure for this connectivity set in the HDF5 file. Connectivity sets are
        stored under ``/connectivity/<tag>``.
        """
        if tag is None:
            tag = f"{pre_type.name}_to_{post_type.name}"
        path = _root + tag
        with engine._write() as fence:
            with engine._handle("a") as h:
                g = h.create_group(path)
                g.attrs["pre"] = pre_type.name
                g.attrs["post"] = post_type.name
                g.require_group(path + "/inc")
                g.require_group(path + "/out")
        cs = cls(engine, tag)
        cs.pre = pre_type
        cs.post = post_type
        return cs

    @staticmethod
    def exists(engine, tag, handle=None):
        check = lambda h: _root + tag in h
        if handle is not None:
            return check(handle)
        else:
            with engine._read():
                with engine._handle("r") as h:
                    return check(h)

    @classmethod
    def require(cls, engine, pre_type, post_type, tag=None):
        if tag is None:
            tag = f"{pre_type.name}_to_{post_type.name}"
        path = _root + tag
        with engine._write():
            with engine._handle("a") as h:
                g = h.require_group(path)
                if g.attrs.setdefault("pre", pre_type.name) != pre_type.name:
                    raise ValueError(
                        "Given and stored type mismatch:"
                        + f" {pre_type.name} vs {g.attrs['pre']}"
                    )
                if g.attrs.setdefault("post", post_type.name) != post_type.name:
                    raise ValueError(
                        "Given and stored type mismatch:"
                        + f" {post_type.name} vs {g.attrs['post']}"
                    )
                g.require_group(path + "/inc")
                g.require_group(path + "/out")
        cs = cls(engine, tag)
        cs.pre = pre_type
        cs.post = post_type
        return cs

    def clear(self):
        raise NotImplementedError("Will do once I have some sample data :)")

    def connect(self, pre_set, post_set, src_locs, dest_locs):
        with self._engine._write():
            with self._engine._handle("a") as handle:
                for data in self._demux(pre_set, post_set, src_locs, dest_locs):
                    if not len(data[-1]):
                        # Don't write empty data
                        continue
                    self.chunk_connect(*data, handle=handle)

    def _demux(self, pre, post, src_locs, dst_locs):
        dst_chunks = post.get_loaded_chunks()
        if len(dst_chunks) != 1:
            warn(
                f"{self.tag} data corrupted, destination chunks mixed up."
                + " Data saved to prevent loss, but likely incorrect.",
                CriticalDataWarning,
            )
        dst = next(iter(dst_chunks))
        print("Total of", len(src_locs), "connections")
        # Iterate over each source chunk
        for src in pre.get_loaded_chunks():
            # Count the number of cells
            with pre.chunk_context(src):
                ln = len(pre)
                print("Demuxing", ln, "cells")
            src_idx = src_locs[:, 0] < ln
            print(sum(src_idx), "connections found for", ln, "cells.")
            src_block = src_locs[src_idx]
            dst_block = dst_locs[src_idx]
            block_idx = np.lexsort((src_block[:, 0], dst_block[:, 0]))
            yield src, dst, src_block[block_idx], dst_block[block_idx]
            src_locs = src_locs[~src_idx]
            dst_locs = dst_locs[~src_idx]
            # We sifted `ln` cells out of the dataset, so reduce the ids.
            src_locs[:, 0] -= ln
            print(len(src_idx), "dropped to", len(src_locs))

    def _store_pointers(self, group, chunk, n, total):
        print("Storing pointers", chunk, n)
        chunks = [Chunk(t, (0, 0, 0)) for t in group.attrs.get("chunk_list", [])]
        print("Chunks already present:", chunks)
        if chunk in chunks:
            print("Just moving up pointers")
            # Source chunk already existed, just increment the subseq. pointers
            inc_from = chunks.index(chunk) + 1
        else:
            print("Appending new chunk to end")
            # We are the last chunk, we start adding rows at the end.
            group.attrs[str(chunk.id)] = total
            # Move up the increment pointer to place ourselves after the
            # last element
            chunks.append(chunk)
            inc_from = len(chunks)
            group.attrs["chunk_list"] = chunks
        # Increment the pointers of the chunks that follow us, by `n` rows.
        for c in chunks[inc_from:]:
            group.attrs[str(c.id)] += n

    def _get_insert_pointers(self, group, chunk):
        chunks = [Chunk(t, (0, 0, 0)) for t in group.attrs["chunk_list"]]
        iptr = group.attrs[str(chunk.id)]
        idx = chunks.index(chunk)
        if idx + 1 == len(chunks):
            # Last chunk, no end pointer
            eptr = None
        else:
            # Get the pointer of the next chunk
            eptr = group.attrs[str(chunks[idx + 1].id)]
        return iptr, eptr

    def _get_chunk_data(self, dest_chunk):
        with self._engine._write():
            with self._engine._handle("a") as h:
                grp = h[f"{self._path}/{dest_chunk.id}"]
                src_chunks = grp.attrs["chunk_list"]
                chunk_ptrs = [grp.attrs[str(Chunk(c, (0, 0, 0)).id)] for c in src_chunks]
                src = grp["global_locs"][()]
                dest = grp["local_locs"][()]
        return src_chunks, chunk_ptrs, src, dest

    def chunk_connect(
        self, local_chunk, global_chunk, local_locs, global_locs, handle=None
    ):
        if len(local_locs) != len(global_locs):
            raise ValueError("Location matrices must be of same length.")
        if handle is None:
            with self._engine._write():
                with self._engine._handle("a") as handle:
                    self._connect(
                        local_chunk, global_chunk, local_locs, global_locs, handle
                    )
        else:
            self._connect(local_chunk, global_chunk, local_locs, global_locs, handle)

    def _connect(self, src_chunk, dest_chunk, lloc, gloc, handle):
        self._insert("inc", dest_chunk, src_chunk, gloc, lloc, handle)
        self._insert("out", src_chunk, dest_chunk, lloc, gloc, handle)

    def _insert(self, tag, local, global_, lloc, gloc, handle):
        grp = h.require_group(f"{self._path}/{tag}/{local.id}")
        src_id = str(global_.id)
        unpack_me = [None, None]
        # require_dataset doesn't work for resizable datasets, see
        # https://github.com/h5py/h5py/issues/2018
        # So we create a little thingy for requiring src & dest
        for i, tag in enumerate(("global_locs", "local_locs")):
            if tag in grp:
                unpack_me[i] = grp[tag]
            else:
                unpack_me[i] = grp.create_dataset(
                    tag, shape=(0, 3), dtype=int, chunks=(1024, 3), maxshape=(None, 3)
                )
        lcl_ds, gbl_ds = unpack_me
        # Move the pointers that keep track of the chunks
        new_rows = len(lloc)
        total = len(lcl_ds)
        print("Inserting", new_rows, "rows")
        print("Total data length", total)
        self._store_pointers(grp, local, new_rows, total)
        iptr, eptr = self._get_insert_pointers(grp, local)
        print("Stored data from line", iptr, "to", eptr)
        if eptr is None:
            eptr = total + new_rows
        # Resize and insert data.
        src_end = lcl_ds[(eptr - new_rows) :]
        dest_end = gbl_ds[(eptr - new_rows) :]
        lcl_ds.resize(len(lcl_ds) + new_rows, axis=0)
        gbl_ds.resize(len(gbl_ds) + new_rows, axis=0)
        lcl_ds[iptr:eptr] = np.concatenate((lcl_ds[iptr : (eptr - new_rows)], lloc))
        lcl_ds[eptr:] = src_end
        gbl_ds[iptr:eptr] = np.concatenate((gbl_ds[iptr : (eptr - new_rows)], gloc))
        gbl_ds[eptr:] = dest_end


def _sort_triple(a, b):
    # Comparator for chunks by bitshift and sum of the coords.
    return (a[0] << 42 + a[1] << 21 + a[2]) > (b[0] << 42 + b[1] << 21 + b[2])
