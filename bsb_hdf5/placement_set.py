from bsb.exceptions import (
    MissingMorphologyError,
    DatasetExistsError,
    DatasetNotFoundError,
)
from bsb import config
from bsb.storage import Chunk
from bsb._encoding import EncodedLabels
from bsb.storage.interfaces import PlacementSet as IPlacementSet
from bsb.morphologies import MorphologySet, RotationSet
from bsb.morphologies.selector import MorphologySelector
from .resource import Resource, handles_handles, HANDLED
from .chunks import ChunkLoader, ChunkedProperty, ChunkedCollection
import numpy as np
import itertools
import json


_root = "/placement/"


@config.node
class _MapSelector(MorphologySelector):
    ps = config.attr(type=lambda x: x)
    names = config.attr(type=lambda x: x)

    def __init__(self, *, ps=None, names=None):
        self._ps = ps
        self._names = set(names)

    def validate(self, loaders):
        missing = set(self._names) - {m.get_meta()["name"] for m in loaders}
        if missing:
            raise MissingMorphologyError(
                "Morphology repository misses the following morphologies required by"
                + f" {self._ps.tag}: {', '.join(missing)}"
            )

    def pick(self, stored_morphology):
        name = stored_morphology.get_meta()["name"]
        return name in self._names


class PlacementSet(
    Resource,
    ChunkLoader,
    IPlacementSet,
    properties=(
        lambda loader: ChunkedProperty(loader, "position", shape=(0, 3), dtype=float),
        lambda loader: ChunkedProperty(loader, "rotation", shape=(0, 3), dtype=float),
        lambda loader: ChunkedProperty(loader, "morphology", shape=(0,), dtype=int),
        lambda loader: ChunkedProperty(
            loader, "labels", shape=(0,), dtype=int, extract=encode_labels
        ),
    ),
    collections=(
        lambda loader: ChunkedCollection(loader, "additional", shape=(0,), dtype=float),
    ),
):
    """
    Fetches placement data from storage.

    .. note::

        Use :meth:`Scaffold.get_placement_set <bsb.core.Scaffold.get_placement_set>` to
        correctly obtain a PlacementSet.
    """

    def __init__(self, engine, cell_type):
        tag = cell_type.name
        Resource.__init__(self, engine, _root + tag)
        IPlacementSet.__init__(self, engine, cell_type)
        ChunkLoader.__init__(self)
        self._labels = None
        self._morphology_labels = None
        if not self.exists(engine, cell_type):
            raise DatasetNotFoundError("PlacementSet '{}' does not exist".format(tag))

    @classmethod
    def create(cls, engine, cell_type):
        """
        Create the structure for this placement set in the HDF5 file. Placement sets are
        stored under ``/placement/<tag>``.
        """
        tag = cell_type.name
        path = _root + tag
        with engine._write():
            with engine._handle("a") as h:
                if path in h:
                    raise DatasetExistsError(f"PlacementSet '{tag}' already exists.")
                g = h.create_group(path)
                g.create_group("chunks")
        return cls(engine, cell_type)

    @staticmethod
    def exists(engine, cell_type):
        with engine._read():
            with engine._handle("r") as h:
                return "/placement/" + cell_type.name in h

    @classmethod
    def require(cls, engine, cell_type):
        tag = cell_type.name
        path = _root + tag
        with engine._write():
            with engine._handle("a") as h:
                g = h.require_group(path)
                g.require_group("chunks")
        return cls(engine, cell_type)

    @handles_handles("r")
    def load_positions(self, handle=HANDLED):
        """
        Load the cell positions.

        :raises: DatasetNotFoundError when there is no rotation information for this
           cell type.
        """
        try:
            positions = self._position_chunks.load(handle=handle)
        except DatasetNotFoundError:
            raise DatasetNotFoundError(
                f"No position information for the '{self.tag}' placement set."
            )
        else:
            if self._labels:
                mask = self.get_label_mask(self._labels, handle=handle)
                return positions[mask]
            else:
                return positions

    @handles_handles("r")
    def load_rotations(self, handle=HANDLED):
        """
        Load the cell rotations.

        :raises: DatasetNotFoundError when there is no rotation information for this
           cell type.
        """
        data = self._rotation_chunks.load(handle=handle)
        if len(data) == 0 and len(self) != 0:
            raise DatasetNotFoundError("No rotation data available.")
        if self._labels:
            mask = self.get_label_mask(self._labels, handle=handle)
            data = data[mask]
        return RotationSet(data)

    @handles_handles("r")
    def load_morphologies(self, handle=HANDLED, allow_empty=False):
        """
        Load the cell morphologies.

        :raises: DatasetNotFoundError when the morphology data is not found.
        """
        data = self._morphology_chunks.load(handle=handle)
        loaders = self._get_morphology_loaders(handle=handle)
        if not allow_empty and (len(data) == 0 and (len(self) != 0 or len(loaders) == 0)):
            raise DatasetNotFoundError("No morphology data available.")
        if self._labels:
            mask = self.get_label_mask(self._labels, handle=handle)
            data = data[mask]
        return MorphologySet(
            loaders,
            data,
            labels=self._morphology_labels,
        )

    @handles_handles("r")
    def _get_morphology_loaders(self, handle=HANDLED):
        loaded_names = set()
        stor_mor = []
        for chunk in self.get_loaded_chunks():
            path = self.get_chunk_path(chunk)
            _map = handle[path].attrs.get("morphology_loaders", [])
            cmlist = self._engine.morphologies.select(_MapSelector(ps=self, names=_map))
            stor_mor.extend(m for m in cmlist if m.name not in loaded_names)
            loaded_names.update(m.name for m in cmlist)
        return stor_mor

    @handles_handles("a")
    def _set_morphology_loaders(self, map, handle=HANDLED):
        for chunk in self.get_loaded_chunks():
            path = self.get_chunk_path(chunk)
            handle[path].attrs["morphology_loaders"] = map

    def __iter__(self):
        return itertools.zip_longest(
            self.load_positions(),
            self.load_morphologies(),
        )

    def __len__(self):
        if self._labels:
            return np.sum(self._labels_chunks.load().get_mask(self._labels))
        else:
            return len(self._position_chunks.load())

    def append_data(
        self,
        chunk,
        positions=None,
        morphologies=None,
        rotations=None,
        additional=None,
        count=None,
    ):
        """
        Append data to the placement set.

        :param chunk: The chunk to store data in.
        :param positions: Cell positions
        :type positions: :class:`numpy.ndarray`
        :param rotations: Cell rotations
        :type rotations: ~bsb.morphologies.RotationSet
        :param morphologies: Cell morphologies
        :type morphologies: ~bsb.morphologies.MorphologySet
        :param count: Amount of entities to place. Excludes the use of any positional,
          rotational or morphological data.
        :type count: int
        """
        if not isinstance(chunk, Chunk):
            chunk = Chunk(chunk, None)
        if positions is not None:
            positions = np.array(positions, copy=False)
        if count is not None:
            if not (positions is None and morphologies is None):
                raise ValueError(
                    "The `count` keyword is reserved for creating entities,"
                    + " without any positional, or morphological data."
                )
            with self._engine._write():
                with self._engine._handle("a") as f:
                    self.require_chunk(chunk, handle=f)
                    path = self.get_chunk_path(chunk)
                    prev_count = f[path].attrs.get("entity_count", 0)
                    f[path].attrs["entity_count"] = prev_count + count

        if positions is not None:
            self._position_chunks.append(chunk, positions)
        if rotations is not None and morphologies is None:
            raise ValueError("Can't append rotations without morphologies.")
        if morphologies is not None:
            self._append_morphologies(chunk, morphologies)
            if rotations is None:
                rotations = np.zeros((len(morphologies), 3))
            self._rotation_chunks.append(chunk, rotations)

        if additional is not None:
            for key, ds in additional.items():
                self.append_additional(key, chunk, ds)

    def _append_morphologies(self, chunk, new_set):
        with self.chunk_context(chunk):
            morphology_set = self.load_morphologies(allow_empty=True).merge(new_set)
            self._set_morphology_loaders(morphology_set._serialize_loaders())
            self._morphology_chunks.clear(chunk)
            self._morphology_chunks.append(chunk, morphology_set.get_indices())

    def append_entities(self, chunk, count, additional=None):
        self.append_data(chunk, count=count, additional=additional)

    def append_additional(self, name, chunk, data):
        with self._engine._write():
            self.require_chunk(chunk)
            path = self.get_chunk_path(chunk) + "/additional/" + name
            with self._engine._handle("a") as f:
                if path not in f:
                    maxshape = list(data.shape)
                    maxshape[0] = None
                    f.create_dataset(path, data=data, maxshape=tuple(maxshape))
                else:
                    dset = f[path]
                    start_pos = dset.shape[0]
                    dset.resize(start_pos + len(data), axis=0)
                    dset[start_pos:] = data

    @handles_handles("a")
    def label_by_mask(self, mask, labels, handle=HANDLED):
        cells = np.array(mask, copy=False)
        if cells.dtype != bool or len(cells) != len(self):
            raise LabellingException("Mask doesn't fit data.")
        self._write_labels(
            labels,
            handle,
            lambda: self._lendemux(),
            lambda s: cells[s],
        )

    @handles_handles("a")
    def label(self, labels, cells, handle=HANDLED):
        cells = np.array(cells, copy=False)
        len_ = len(self)
        oob = cells > len_
        if np.any(oob):
            oob_idx = cells[oob]
            raise LabellingException(
                f"Cell labels {oob_idx} out of range for placement set with size {len_}."
            )
        self._write_labels(
            labels,
            handle,
            lambda: self._demux(cells),
            lambda x: x,
        )

    def _write_labels(self, labels, handle, demux_f, data_f):
        label_reader = self._labels_chunks.get_chunk_reader(
            handle, False, pad_by="position"
        )
        updated_labels = None
        # Demultiplex the cells per chunk. _demux also sets chunk context to current chunk
        for chunk, block in demux_f():
            enc_labels = label_reader(chunk)
            if updated_labels:
                enc_labels.labels = updated_labels
            else:
                updated_labels = enc_labels.labels
            enc_labels.label(labels, data_f(block))
            self._labels_chunks.overwrite(chunk, enc_labels, handle=handle)
        if updated_labels is not None:
            handle[self._path].attrs["labelsets"] = json.dumps(
                updated_labels, default=list
            )

    def set_label_filter(self, labels):
        self._labels = labels

    def set_morphology_label_filter(self, morphology_labels):
        """
        Sets the labels by which any morphology loaded from this set will be filtered.

        :param morphology_labels: List of labels to filter the morphologies by.
        :type morphology_labels: List[str]
        """
        self._morphology_labels = morphology_labels

    @handles_handles("r")
    def get_labelled(self, labels, handle=HANDLED):
        mask = self.get_label_mask(labels, handle=handle)
        return np.nonzero(mask)[0]

    @handles_handles("r")
    def get_label_mask(self, labels, handle=HANDLED):
        return self._labels_chunks.load(handle=handle, pad_by="position").get_mask(labels)

    def _lendemux(self):
        """
        .. warning::

            This function sets the chunk context for as long as it iterates.
        """
        ctr = 0
        for chunk in self.get_loaded_chunks():
            with self.chunk_context(chunk):
                len_ = len(self)
                yield (chunk, slice(ctr, ctr := ctr + len_))

    def _demux(self, ids):
        """
        .. warning::

            This function sets the chunk context for as long as it iterates.
        """
        for chunk in self.get_loaded_chunks():
            with self.chunk_context(chunk):
                ln = len(self)
                idx = ids < ln
                block = ids[idx]
                yield chunk, block
                ids = ids[~idx]
                ids -= ln


def encode_labels(data, ds):
    if ds is None:
        return EncodedLabels.none(len(data))
    else:
        ps_group = ds.parent.parent.parent
        serialized = json.dumps(EncodedLabels.none(1).labels, default=list)
        labels = json.loads(ps_group.attrs.get("labelsets", serialized))
        return EncodedLabels(shape=data.shape, buffer=data, labels=labels)


class LabellingException(Exception):
    pass
