import pytest

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


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("insert", (1, RID("student", 0, 0)), None),
        ("delete", (1, RID("student", 0, 0)), None),
        ("search", (1,), []),
        ("range", (1, 10), []),
        ("height", (), 0),
    ],
)
def test_default_returns(name, args, expected):
    assert getattr(HashIndex("student", "id"), name)(*args) == expected
