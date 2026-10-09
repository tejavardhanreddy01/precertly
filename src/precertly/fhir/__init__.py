"""FHIR R4 bundles to citable text chunks."""

from precertly.fhir.chunks import SUPPORTED_TYPES, Chunk, chunk_bundle, load_bundle, split_spans

__all__ = ["SUPPORTED_TYPES", "Chunk", "chunk_bundle", "load_bundle", "split_spans"]
