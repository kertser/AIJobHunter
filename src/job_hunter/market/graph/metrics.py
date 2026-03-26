"""Export the market evidence graph to NetworkX and file formats."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

logger = logging.getLogger("job_hunter.market.graph.nx_export")


def to_networkx(session: Session):
    """Build a :class:`networkx.Graph` from market entities and edges.

    Returns a NetworkX ``Graph`` (undirected for co-occurrence edges).
    """
    import networkx as nx

    from job_hunter.market.repo import get_all_edges, get_all_entities

    G = nx.Graph()

    for entity in get_all_entities(session):
        G.add_node(
            entity.id,
            label=entity.display_name,
            entity_type=entity.entity_type.value,
            canonical_name=entity.canonical_name,
        )

    for edge in get_all_edges(session):
        G.add_edge(
            edge.src_entity_id,
            edge.dst_entity_id,
            edge_type=edge.edge_type.value,
            weight=edge.weight,
            count=edge.count,
        )

    return G


def export_graphml(session: Session, path: Path) -> Path:
    """Write the graph as a GraphML file."""
    import networkx as nx

    G = to_networkx(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(G, str(path))
    logger.info("Exported GraphML to %s (%d nodes, %d edges)", path, G.number_of_nodes(), G.number_of_edges())
    return path


def export_json(session: Session, path: Path) -> Path:
    """Write the graph as a JSON adjacency file."""
    import networkx as nx
    from networkx.readwrite import json_graph

    G = to_networkx(session)
    data = json_graph.node_link_data(G)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info("Exported JSON graph to %s", path)
    return path

