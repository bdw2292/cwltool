# Stubs for galaxy.objectstore.pulsar (Python 3.4)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any, Optional
from ..objectstore import ObjectStore as ObjectStore

ObjectStoreClientManager = ...  # type: Any

class PulsarObjectStore(ObjectStore):
    pulsar_client = ...  # type: Any
    def __init__(self, config, config_xml) -> None: ...
    def exists(self, obj, **kwds): ...
    def file_ready(self, obj, **kwds): ...
    def create(self, obj, **kwds): ...
    def empty(self, obj, **kwds): ...
    def size(self, obj, **kwds): ...
    def delete(self, obj, **kwds): ...
    def get_data(self, obj, **kwds): ...
    def get_filename(self, obj, **kwds): ...
    def update_from_file(self, obj, **kwds): ...
    def get_store_usage_percent(self): ...
    def get_object_url(self, obj, extra_dir: Optional[Any] = ..., extra_dir_at_root: bool = ..., alt_name: Optional[Any] = ...): ...
    def shutdown(self): ...