"""Deep unit tests for dreamer/trees modules (rptree, covertree, graph, lsh,
prototype, sklearn_wrapper, base, factory).

Covers pure algorithms with small synthetic datasets. No external services.
"""

import numpy as np
import pytest

from src.dreamer.trees import (
    CoverNode,
    CoverTree,
    GraphSurprisal,
    InternalNode,
    LeafNode,
    LSHSurprisal,
    PrototypeSurprisal,
    RPInternalNode,
    RPTree,
    SklearnTreeWrapper,
    SurprisalTree,
    TreeNode,
    create_tree,
)
from src.dreamer.trees.base import sklearn_import_error


# ---------------------------------------------------------------------------
# base.py
# ---------------------------------------------------------------------------

def test_sklearn_import_error_message():
    exc = ImportError("no module named sklearn")
    err = sklearn_import_error("'graph'", exc)
    assert isinstance(err, RuntimeError)
    assert "scikit-learn" in str(err)
    assert "surprisal" in str(err)
    assert "no module named sklearn" in str(err)


def test_leaf_node_post_init_sets_count_from_points():
    p = np.array([1.0, 2.0])
    leaf = LeafNode(points=[p, p], count=0)
    assert leaf.count == 2


def test_leaf_node_explicit_count_preserved():
    leaf = LeafNode(points=[np.array([1.0])], count=7)
    assert leaf.count == 7


def test_tree_node_defaults():
    node = TreeNode()
    assert node.count == 0


def test_internal_node_defaults():
    node = InternalNode()
    assert node.left is None and node.right is None


def test_surprisal_tree_is_abstract():
    with pytest.raises(TypeError):
        SurprisalTree()  # type: ignore[abstract]


def test_base_batch_insert_uses_insert():
    calls = []

    class Fake(SurprisalTree):
        def insert(self, point):
            calls.append(point.tolist())

        def surprisal(self, point):
            return 0.0

    fake = Fake()
    fake.batch_insert(np.array([[1.0, 2.0], [3.0, 4.0]]))
    assert calls == [[1.0, 2.0], [3.0, 4.0]]


# ---------------------------------------------------------------------------
# rptree.py
# ---------------------------------------------------------------------------

def test_rptree_insert_first_point_creates_root_leaf():
    tree = RPTree(max_leaf_size=2)
    assert tree.root is None
    p = np.array([1.0, 2.0])
    tree.insert(p)
    assert isinstance(tree.root, LeafNode)
    assert tree.total_points == 1
    assert len(tree.root.points) == 1


def test_rptree_surprisal_empty_returns_inf():
    tree = RPTree()
    assert tree.surprisal(np.array([1.0, 1.0])) == float("inf")


def test_rptree_split_leaf_low_variance_keeps_leaf():
    tree = RPTree(max_leaf_size=2)
    p = np.array([1.0, 1.0])
    tree.insert(p)
    tree.insert(p)
    tree.insert(p)  # identical points -> variance ~0
    assert isinstance(tree.root, LeafNode)
    assert len(tree.root.points) == 3


def test_rptree_split_leaf_creates_internal_node():
    np.random.seed(0)
    tree = RPTree(max_leaf_size=2)
    # Two well-separated clusters guarantee any random projection splits them.
    left = [np.array([0.0, 0.0]), np.array([0.1, 0.1]), np.array([0.2, 0.2])]
    right = [np.array([10.0, 10.0]), np.array([10.1, 10.1])]
    for p in left + right:
        tree.insert(p)
    assert isinstance(tree.root, RPInternalNode)
    assert tree.total_points == 5
    # Every leaf path must terminate at a LeafNode.
    node = tree.root
    assert isinstance(node, RPInternalNode)
    assert node.direction is not None


def test_rptree_surprisal_walks_path():
    np.random.seed(1)
    tree = RPTree(max_leaf_size=2)
    points = [np.array([0.0, 0.0]), np.array([0.1, 0.1]),
              np.array([0.2, 0.2]), np.array([10.0, 10.0])]
    for p in points:
        tree.insert(p)
    score = tree.surprisal(np.array([0.05, 0.05]))
    assert np.isfinite(score) and score > 0.0


def test_rptree_insert_child_type_error():
    tree = RPTree()
    bad = InternalNode()
    with pytest.raises(TypeError, match="Unexpected child type"):
        tree._insert_child(bad, np.array([1.0, 1.0]))


def test_rptree_go_left():
    tree = RPTree()
    node = RPInternalNode(direction=np.array([1.0, 0.0]), threshold=0.5)
    assert tree._go_left(node, np.array([0.0, 0.0])) is True
    assert tree._go_left(node, np.array([1.0, 0.0])) is False


def test_rptree_split_fallback_returns_leaf(monkeypatch):
    # Force every random direction to be (near) zero -> all attempts skipped.
    monkeypatch.setattr("numpy.random.randn", lambda *a: np.array([0.0, 0.0]))
    tree = RPTree(max_leaf_size=2)
    points = [np.array([0.0, 0.0]), np.array([1.0, 1.0]),
              np.array([2.0, 2.0])]
    for p in points:
        tree.insert(p)
    # No usable direction -> leaf is retained (points count > max_leaf_size).
    assert isinstance(tree.root, LeafNode)


# ---------------------------------------------------------------------------
# covertree.py
# ---------------------------------------------------------------------------

def test_covertree_insert_first_point_creates_root():
    tree = CoverTree()
    tree.insert(np.array([0.0, 0.0]))
    assert isinstance(tree.root, CoverNode)
    assert tree.total_points == 1


def test_covertree_insert_recursive_same_scale():
    tree = CoverTree(base=2.0)
    tree.insert(np.array([0.0, 0.0]))
    tree.insert(np.array([0.1, 0.0]))  # within cover radius of root
    assert isinstance(tree.root, CoverNode)
    assert len(tree.root.children) == 1


def test_covertree_insert_creates_new_root_when_distant():
    tree = CoverTree(base=2.0)
    tree.insert(np.array([0.0, 0.0]))
    tree.insert(np.array([100.0, 100.0]))  # outside cover radius
    assert isinstance(tree.root, CoverNode)
    assert tree.root.scale >= 1
    assert len(tree.root.children) == 2


def test_covertree_insert_recursive_deep_child():
    tree = CoverTree(base=2.0)
    tree.insert(np.array([0.0, 0.0]))
    tree.insert(np.array([0.05, 0.0]))
    tree.insert(np.array([0.06, 0.0]))
    # Third point should nest into the nearest child.
    assert tree.total_points == 3


def test_covertree_surprisal_empty_returns_inf():
    tree = CoverTree()
    assert tree.surprisal(np.array([1.0, 1.0])) == float("inf")


def test_covertree_surprisal_finite():
    tree = CoverTree(base=2.0)
    for p in [np.array([0.0, 0.0]), np.array([0.1, 0.0]),
              np.array([0.2, 0.0]), np.array([5.0, 5.0])]:
        tree.insert(p)
    score = tree.surprisal(np.array([0.05, 0.0]))
    assert np.isfinite(score)


def test_covertree_surprisal_leaf_only():
    tree = CoverTree()
    tree.insert(np.array([0.0, 0.0]))
    # No children -> only distance term remains; still finite for non-identical.
    assert np.isfinite(tree.surprisal(np.array([0.5, 0.5])))


# ---------------------------------------------------------------------------
# graph.py
# ---------------------------------------------------------------------------

def test_graph_insert_and_batch_insert():
    g = GraphSurprisal(k=2, max_iter=50)
    assert len(g.points) == 0
    g.insert(np.array([0.0, 0.0]))
    assert g.total_points == 1
    assert g.graph_built is False
    g.batch_insert(np.array([[1.0, 0.0], [0.0, 1.0]]))
    assert g.total_points == 3


def test_graph_build_requires_two_points():
    g = GraphSurprisal(k=2)
    g.insert(np.array([0.0, 0.0]))
    g._build_graph_and_compute_stationary()
    assert g.stationary_dist is None
    assert g.graph_built is False


def test_graph_surprisal_empty_returns_inf():
    g = GraphSurprisal()
    assert g.surprisal(np.array([1.0, 1.0])) == float("inf")


def test_graph_surprisal_builds_and_scores():
    g = GraphSurprisal(k=2, max_iter=200)
    points = [np.array([0.0, 0.0]), np.array([0.1, 0.0]),
              np.array([0.2, 0.0]), np.array([10.0, 10.0]),
              np.array([10.1, 10.0]), np.array([10.2, 10.0])]
    for p in points:
        g.insert(p)
    score = g.surprisal(np.array([0.0, 0.0]))
    assert np.isfinite(score) and score >= 0.0
    assert g.graph_built is True


def test_graph_knn_indices_shapes():
    from src.dreamer.trees.graph import _knn_indices
    points = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    idx = _knn_indices(points, 3)
    assert idx.shape == (3, 3)


def test_graph_nearest_index():
    from src.dreamer.trees.graph import _nearest_index
    points = np.array([[0.0, 0.0], [10.0, 10.0]])
    assert _nearest_index(points, np.array([0.1, 0.1])) == 0


def test_graph_isolated_node_self_loop():
    g = GraphSurprisal(k=2, max_iter=100)
    # One isolated far point, one cluster -> isolated point gets a self-loop.
    points = [np.array([0.0, 0.0]), np.array([0.1, 0.0]),
              np.array([0.2, 0.0]), np.array([50.0, 50.0])]
    for p in points:
        g.insert(p)
    g._build_graph_and_compute_stationary()
    assert g.stationary_dist is not None
    assert np.isclose(g.stationary_dist.sum(), 1.0)


# ---------------------------------------------------------------------------
# lsh.py
# ---------------------------------------------------------------------------

def test_lsh_insert_initializes_and_hashes():
    lsh = LSHSurprisal(num_tables=4, num_bits=4)
    p = np.array([1.0, 2.0])
    lsh.insert(p)
    assert lsh.initialized is True
    assert len(lsh.hash_directions) == 4
    assert lsh.total_points == 1


def test_lsh_hash_vector_deterministic():
    lsh = LSHSurprisal(num_tables=1, num_bits=4)
    np.random.seed(3)
    lsh._initialize_hash_functions(3)
    v = np.array([0.5, -0.5, 1.0])
    assert lsh._hash_vector(v, 0) == lsh._hash_vector(v, 0)


def test_lsh_surprisal_empty_returns_inf():
    lsh = LSHSurprisal()
    assert lsh.surprisal(np.array([1.0, 1.0])) == float("inf")


def test_lsh_surprisal_collision_density():
    np.random.seed(4)
    lsh = LSHSurprisal(num_tables=4, num_bits=6)
    p = np.array([0.3, 0.7])
    lsh.insert(p)
    lsh.insert(p)  # same point -> high collision -> low surprisal
    lsh.insert(np.array([5.0, -5.0]))
    score = lsh.surprisal(p)
    assert np.isfinite(score) and score >= 0.0
    rare_score = lsh.surprisal(np.array([9.0, 9.0]))
    assert np.isfinite(rare_score)


def test_lsh_surprisal_zero_collision():
    np.random.seed(5)
    lsh = LSHSurprisal(num_tables=2, num_bits=8)
    lsh.insert(np.array([0.0, 0.0]))
    score = lsh.surprisal(np.array([123.0, -123.0]))
    assert np.isfinite(score)


# ---------------------------------------------------------------------------
# prototype.py
# ---------------------------------------------------------------------------

def test_prototype_insert_and_batch_insert():
    pt = PrototypeSurprisal(n_clusters=3)
    pt.insert(np.array([0.0, 0.0]))
    assert pt.total_points == 1
    pt.batch_insert(np.array([[1.0, 1.0], [2.0, 2.0]]))
    assert pt.total_points == 3


def test_prototype_build_clusters_too_few_points():
    pt = PrototypeSurprisal(n_clusters=5)
    pt.insert(np.array([0.0, 0.0]))
    pt.insert(np.array([1.0, 1.0]))
    pt._build_clusters()
    assert pt.clusters_built is True
    assert len(pt.prototypes) == 2


def test_prototype_build_clusters_kmeans():
    pt = PrototypeSurprisal(n_clusters=2)
    np.random.seed(6)
    cluster1 = np.random.randn(10, 2) + np.array([0.0, 0.0])
    cluster2 = np.random.randn(10, 2) + np.array([20.0, 20.0])
    pt.batch_insert(np.vstack([cluster1, cluster2]))
    pt._build_clusters()
    assert pt.clusters_built is True
    assert pt.prototypes.shape[0] == 2


def test_prototype_surprisal_empty_returns_inf():
    pt = PrototypeSurprisal()
    assert pt.surprisal(np.array([1.0, 1.0])) == float("inf")


def test_prototype_surprisal_distance_scaled():
    pt = PrototypeSurprisal(n_clusters=2, surprisal_scale=10.0)
    np.random.seed(7)
    pt.batch_insert(np.array([[0.0, 0.0], [0.1, 0.1], [0.2, 0.2]]))
    score = pt.surprisal(np.array([0.0, 0.0]))
    assert np.isfinite(score) and score >= 0.0


# ---------------------------------------------------------------------------
# sklearnarn_wrapper.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tree_type", ["kd", "ball"])
def test_sklearn_wrapper_insert_and_surprisal(tree_type):
    wrapper = SklearnTreeWrapper(tree_type=tree_type, k=2)
    points = [np.array([0.0, 0.0]), np.array([0.1, 0.0]),
              np.array([0.2, 0.0])]
    for p in points:
        wrapper.insert(p)
    assert wrapper.tree is not None
    score = wrapper.surprisal(np.array([0.0, 0.0]))
    assert np.isfinite(score)


def test_sklearn_wrapper_batch_insert():
    wrapper = SklearnTreeWrapper(tree_type="kd", k=2)
    wrapper.batch_insert(np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]))
    assert wrapper.total_points == 3
    assert wrapper.tree is not None


def test_sklearn_wrapper_empty_rebuild_no_op():
    wrapper = SklearnTreeWrapper()
    wrapper._rebuild_tree()
    assert wrapper.tree is None


def test_sklearn_wrapper_unknown_tree_type_raises():
    wrapper = SklearnTreeWrapper(tree_type="bogus")
    with pytest.raises(ValueError, match="Unknown tree type"):
        wrapper.insert(np.array([0.0, 0.0]))


def test_sklearn_wrapper_surprisal_empty_returns_inf():
    wrapper = SklearnTreeWrapper(k=3)
    assert wrapper.surprisal(np.array([0.0, 0.0])) == float("inf")


def test_sklearn_wrapper_surprisal_too_few_points():
    wrapper = SklearnTreeWrapper(k=5)
    wrapper.insert(np.array([0.0, 0.0]))
    wrapper.insert(np.array([1.0, 1.0]))
    assert wrapper.surprisal(np.array([0.0, 0.0])) == float("inf")


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------

def test_create_tree_drops_k_for_non_knn():
    tree = create_tree("rptree", k=5)
    assert isinstance(tree, RPTree)
    tree2 = create_tree("covertree", k=5)
    assert isinstance(tree2, CoverTree)
    tree3 = create_tree("lsh", k=5)
    assert isinstance(tree3, LSHSurprisal)
    tree4 = create_tree("prototype", k=5)
    assert isinstance(tree4, PrototypeSurprisal)


def test_create_tree_balltree_kwargs():
    tree = create_tree("balltree", k=3)
    assert isinstance(tree, SklearnTreeWrapper)
    assert tree.tree_type == "ball"


# ---------------------------------------------------------------------------
# sklearn import-error branches
# ---------------------------------------------------------------------------

def _block_sklearn(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sklearn" or name.startswith("sklearn."):
            raise ImportError("sklearn not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_graph_knn_indices_sklearn_import_error(monkeypatch):
    _block_sklearn(monkeypatch)
    from src.dreamer.trees.graph import _knn_indices
    with pytest.raises(RuntimeError, match="scikit-learn"):
        _knn_indices(np.array([[0.0, 0.0]]), 1)


def test_graph_nearest_index_sklearn_import_error(monkeypatch):
    _block_sklearn(monkeypatch)
    from src.dreamer.trees.graph import _nearest_index
    with pytest.raises(RuntimeError, match="scikit-learn"):
        _nearest_index(np.array([[0.0, 0.0]]), np.array([0.0, 0.0]))


def test_prototype_kmeans_import_error(monkeypatch):
    _block_sklearn(monkeypatch)
    pt = PrototypeSurprisal(n_clusters=2)
    pt.batch_insert(np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]))
    with pytest.raises(RuntimeError, match="scikit-learn"):
        pt._build_clusters()


def test_sklearn_wrapper_kdtree_import_error(monkeypatch):
    _block_sklearn(monkeypatch)
    wrapper = SklearnTreeWrapper(tree_type="kd")
    with pytest.raises(RuntimeError, match="scikit-learn"):
        wrapper.insert(np.array([0.0, 0.0]))


# ---------------------------------------------------------------------------
# rptree internal traversal & split edge branches
# ---------------------------------------------------------------------------

def test_rptree_insert_traverses_internal_left_child():
    tree = RPTree(max_leaf_size=2)
    tree.root = RPInternalNode(
        direction=np.array([1.0, 0.0]),
        threshold=0.5,
        left=RPInternalNode(
            direction=np.array([1.0, 0.0]),
            threshold=0.5,
            left=LeafNode(points=[np.array([0.2, 0.0])], count=1),
            right=LeafNode(points=[np.array([0.4, 0.0])], count=1),
            count=2,
        ),
        right=LeafNode(points=[np.array([1.0, 0.0])], count=1),
        count=3,
    )
    tree.total_points = 3
    # x=0.1 goes left through the internal left child (covers the
    # "node.left is not None -> _insert_child" branch).
    tree._insert(tree.root, np.array([0.1, 0.0]))
    assert tree.root.left is not None
    assert isinstance(tree.root.left, RPInternalNode)


def test_rptree_surprisal_break_non_tree_child_left():
    tree = RPTree()
    tree.root = RPInternalNode(
        direction=np.array([1.0, 0.0]),
        threshold=0.5,
        left=TreeNode(),  # not a LeafNode/RPInternalNode -> break
        right=None,
        count=2,
    )
    score = tree.surprisal(np.array([0.0, 0.0]))
    assert score == 0.0


def test_rptree_surprisal_break_non_tree_child_right():
    tree = RPTree()
    tree.root = RPInternalNode(
        direction=np.array([1.0, 0.0]),
        threshold=0.5,
        left=None,
        right=TreeNode(),  # not a LeafNode/RPInternalNode -> break
        count=2,
    )
    score = tree.surprisal(np.array([1.0, 0.0]))
    assert score == 0.0


def test_rptree_surprisal_break_no_children():
    tree = RPTree()
    tree.root = RPInternalNode(
        direction=np.array([1.0, 0.0]),
        threshold=0.5,
        left=None,
        right=None,
        count=2,
    )
    score = tree.surprisal(np.array([0.0, 0.0]))
    assert score == 0.0


def test_rptree_split_leaf_std_continue_then_split(monkeypatch):
    """First random direction yields std~0 (continue); second splits."""
    directions = iter([np.array([1.0, -1.0]), np.array([1.0, 1.0])])

    def fake_randn(*a):
        try:
            return next(directions)
        except StopIteration:
            return np.array([1.0, 1.0])

    monkeypatch.setattr("numpy.random.randn", fake_randn)
    leaf = LeafNode(points=[np.array([0.0, 0.0]), np.array([1.0, 1.0]),
                            np.array([2.0, 2.0])], count=3)
    tree = RPTree(max_leaf_size=2)
    result = tree._split_leaf(leaf)
    assert isinstance(result, RPInternalNode)


def test_rptree_split_leaf_empty_mask_branch(monkeypatch):
    """All-same-side projections trigger the threshold-midpoint branch."""
    def fake_randn(*a):
        return np.array([1.0, 1.0])

    monkeypatch.setattr("numpy.random.randn", fake_randn)
    # projections along (1,1): 0,0,2 -> median 0 -> left mask empty
    leaf = LeafNode(points=[np.array([0.0, 0.0]), np.array([0.0, 0.0]),
                            np.array([1.0, 1.0])], count=3)
    tree = RPTree(max_leaf_size=2)
    result = tree._split_leaf(leaf)
    assert isinstance(result, RPInternalNode)


# ---------------------------------------------------------------------------
# covertree / graph remaining branches
# ---------------------------------------------------------------------------

def test_covertree_surprisal_best_child_none(monkeypatch):
    """Force distance -> inf so no child wins (covers the break)."""
    monkeypatch.setattr(
        "numpy.linalg.norm", lambda *a, **k: float("inf")
    )
    tree = CoverTree(base=2.0)
    tree.insert(np.array([0.0, 0.0]))
    tree.insert(np.array([0.1, 0.0]))  # root gains a child
    assert tree.root.children
    score = tree.surprisal(np.array([0.0, 0.0]))
    assert score == float("inf")


def test_graph_isolated_node_self_loop_k0():
    # k=0 -> k_actual=0 -> each node has no neighbors -> self-loop branch.
    g = GraphSurprisal(k=0, max_iter=100)
    points = [np.array([0.0, 0.0]), np.array([1.0, 0.0]),
              np.array([0.0, 1.0])]
    for p in points:
        g.insert(p)
    g._build_graph_and_compute_stationary()
    assert g.stationary_dist is not None
    assert np.isclose(g.stationary_dist.sum(), 1.0)


def test_graph_surprisal_single_point_returns_inf():
    g = GraphSurprisal(k=2)
    g.insert(np.array([0.0, 0.0]))
    assert g.surprisal(np.array([0.0, 0.0])) == float("inf")


def test_create_tree_kdtree_and_graph():
    assert isinstance(create_tree("kdtree", k=3), SklearnTreeWrapper)
    assert isinstance(create_tree("graph", k=3), GraphSurprisal)


def test_create_tree_unknown_raises():
    with pytest.raises(ValueError, match="Unknown tree type"):
        create_tree("bogus")


def test_rptree_surprisal_descends_right_subtree():
    # Cover the successful right-descend branch (node = node.right).
    tree = RPTree()
    tree.root = RPInternalNode(
        direction=np.array([1.0, 0.0]),
        threshold=0.5,
        left=LeafNode(points=[np.array([0.0, 0.0])], count=1),
        right=RPInternalNode(
            direction=np.array([1.0, 0.0]),
            threshold=0.5,
            left=LeafNode(points=[np.array([0.6, 0.0])], count=1),
            right=LeafNode(points=[np.array([0.9, 0.0])], count=1),
            count=2,
        ),
        count=3,
    )
    score = tree.surprisal(np.array([0.9, 0.0]))
    assert score > 0.0


def test_rptree_split_leaf_proj_range_tiny_continue(monkeypatch):
    """Cover the ``proj_max - proj_min < 1e-10`` continue inside the
    empty-mask branch by forcing np.std to pass while projections collapse."""
    monkeypatch.setattr("numpy.std", lambda *a, **k: 1.0)
    monkeypatch.setattr(
        "numpy.random.randn", lambda *a: np.array([1.0, 0.0])
    )
    leaf = LeafNode(points=[np.array([0.0, 0.0]), np.array([0.0, 1.0])],
                    count=2)
    tree = RPTree(max_leaf_size=2)
    # All attempts hit the tiny-range continue -> leaf returned unchanged.
    assert tree._split_leaf(leaf) is leaf
