"""Turn identity only; no external browser or user session access."""
import pytest

from openagent.providers.turn_identity import TurnBaseline, snapshot_turns, find_new_turn, turn_id


class Node:
    def __init__(self, identifier=None, ancestor=None):
        self.identifier, self.ancestor = identifier, ancestor
    async def get_attribute(self, attr):
        if attr == 'data-turn-id':
            return self.identifier
        return None
    def locator(self, selector):
        return self.ancestor or self


class Nodes:
    def __init__(self, page):
        self.page = page
    async def count(self):
        return len(self.page.nodes)
    def nth(self, i):
        return self.page.nodes[i]


class Page:
    def __init__(self, nodes):
        self.nodes = nodes
    def locator(self, selector):
        return Nodes(self)


@pytest.mark.asyncio
async def test_snapshot_and_virtualized_count_remains_constant():
    page = Page([Node('old')])
    baseline = await snapshot_turns(page, 'assistant')
    assert baseline == TurnBaseline(1, frozenset({'old'}))
    page.nodes = [Node('new')]
    found = await find_new_turn(page, 'assistant', baseline)
    assert found is not None
    assert found[1] == 'new'


@pytest.mark.asyncio
async def test_no_ids_and_no_count_change_fails_closed():
    page = Page([Node()])
    baseline = await snapshot_turns(page, 'assistant')
    page.nodes = [Node()]
    assert await find_new_turn(page, 'assistant', baseline) is None


@pytest.mark.asyncio
async def test_new_attribute_on_old_node_is_not_proof_of_new_turn():
    page = Page([Node()])
    baseline = await snapshot_turns(page, 'assistant')
    page.nodes = [Node('newly-hydrated-id')]
    assert await find_new_turn(page, 'assistant', baseline) is None


@pytest.mark.asyncio
async def test_count_growth_without_ids_is_conservative_fallback():
    page = Page([Node()])
    baseline = await snapshot_turns(page, 'assistant')
    page.nodes = [Node(), Node()]
    found = await find_new_turn(page, 'assistant', baseline)
    assert found is not None
    assert found[1] is None


@pytest.mark.asyncio
async def test_id_on_ancestor_and_invalid_identifier():
    node = Node(None, Node('ancestor-id'))
    assert await turn_id(node) == 'ancestor-id'
    assert await turn_id(Node('x' * 200)) is None


@pytest.mark.asyncio
async def test_virtualization_is_not_just_last_message_index():
    page = Page([Node('old-a'), Node('old-b')])
    baseline = await snapshot_turns(page, 'assistant')
    page.nodes = [Node('old-b'), Node('new-c')]
    assert (await find_new_turn(page, 'assistant', baseline))[1] == 'new-c'
