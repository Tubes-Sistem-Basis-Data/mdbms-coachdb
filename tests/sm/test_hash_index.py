import pytest

from common.errors import IndexDefinitionError
from common.storage import RID
from storage_manager.index import HashIndex, Index


def test_hash_index_is_index():
    assert issubclass(HashIndex, Index)


def test_hash_index_attributes():
    idx = HashIndex("student", "id", n_buckets=8)
    assert idx.table == "student"
    assert idx.column == "id"
    assert idx._n_buckets == 8
    assert idx._buckets == [{} for _ in range(8)]


def test_range_not_supported():
    with pytest.raises(IndexDefinitionError):
        HashIndex("student", "id").range(1, 10)


@pytest.mark.parametrize(
    "name,args",
    [
        ("insert", (1, RID("student", 0, 0))),
        ("delete", (1, RID("student", 0, 0))),
        ("search", (1,)),
        ("height", ()),
    ],
)
def test_unimplemented_raises(name, args):
    with pytest.raises(NotImplementedError):
        getattr(HashIndex("student", "id"), name)(*args)
