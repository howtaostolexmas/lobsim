from collections import Counter

from bench.orderflow import apply, generate

from lobsim import OrderBook, Rejected


def test_same_seed_same_ops():
    assert generate(10_000, seed=7) == generate(10_000, seed=7)


def test_different_seed_differs():
    assert generate(1_000, seed=1) != generate(1_000, seed=2)


def test_mix_and_validity():
    ops = generate(20_000, seed=3)
    kinds = Counter(o[0] for o in ops)
    assert 0.55 < kinds["limit"] / len(ops) < 0.65
    assert 0.25 < kinds["cancel"] / len(ops) < 0.35
    assert all(o[-1] > 0 for o in ops if o[0] in ("limit", "market"))


def test_ids_are_unique_for_new_orders():
    ops = generate(5_000, seed=5)
    ids = [o[1] for o in ops if o[0] != "cancel"]
    assert len(ids) == len(set(ids))


def test_flow_runs_on_engine_without_rejects_except_unknown_cancel():
    book = OrderBook()
    for op in generate(5_000, seed=4):
        for e in apply(book, op):
            assert not isinstance(e, Rejected) or e.reason == "unknown_id"
