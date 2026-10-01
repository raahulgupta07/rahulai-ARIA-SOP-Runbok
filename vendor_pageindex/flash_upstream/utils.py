"""Minimal stand-in for upstream ``pageindex/utils.py``.

Only the two helpers Flash's LLM-free path calls (``write_node_id``,
``strip_internal_keys``) are copied verbatim from upstream. The summary /
optimize helpers (``summarize_tree``, ``SummaryScheduler``, ``ConfigLoader``)
are deliberately absent: they pull in litellm + network, and Aria only calls
Flash with ``summary=False, optimize=False``. Importing them raises
ImportError, which surfaces loudly instead of silently calling an LLM.
"""


def write_node_id(data, node_id=0):
    if isinstance(data, dict):
        data['node_id'] = str(node_id).zfill(4)
        node_id += 1
        for key in list(data.keys()):
            if 'nodes' in key:
                node_id = write_node_id(data[key], node_id)
    elif isinstance(data, list):
        for index in range(len(data)):
            node_id = write_node_id(data[index], node_id)
    return node_id


def strip_internal_keys(structure):
    """Drop the bookkeeping keys the optimize/summary passes leave behind."""
    nodes = structure if isinstance(structure, list) else [structure]
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node.pop('_same_page', None)
        if node.get('nodes'):
            strip_internal_keys(node['nodes'])
    return structure
