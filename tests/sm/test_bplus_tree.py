import inspect

import pytest

from storage_manager.index import (
    BPlusNode,
    BPlusTree,
    Index,
    IndexManager,
    InternalNode,
    LeafNode,
)

INDEX_METHODS = ["insert", "delete", "search", "range", "height"]
MANAGER_METHODS = [
    "create_index", "lookup", "range", "height",
    "on_insert", "on_delete", "on_update", "rebuild_all",
]


def test_index_is_abstract():
    with pytest.raises(TypeError):
        Index("t", "c") 


@pytest.mark.parametrize("name", INDEX_METHODS)
def test_bplustree_has_index_methods(name):
    assert callable(getattr(BPlusTree, name))


@pytest.mark.parametrize("name", MANAGER_METHODS)
def test_index_manager_has_methods(name):
    assert callable(getattr(IndexManager, name))


def test_bplustree_attributes():
    tree = BPlusTree("student", "id", order=4)
    assert tree.table == "student"
    assert tree.column == "id"
    assert tree._order == 4
    assert isinstance(tree._root, LeafNode)


def test_node_hierarchy():
    assert issubclass(InternalNode, BPlusNode)
    assert issubclass(LeafNode, BPlusNode)
    leaf = LeafNode()
    assert leaf.keys == [] and leaf.values == [] and leaf.next is None
    assert InternalNode().children == []


def test_default_returns():
    assert BPlusTree("t", "c").search(1) == []
