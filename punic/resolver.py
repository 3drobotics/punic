from __future__ import division, absolute_import, print_function

__all__ = ['Resolver', 'Node']

from collections import (defaultdict, namedtuple)
from networkx import (DiGraph, dfs_preorder_nodes, topological_sort, number_of_nodes, number_of_edges)
import logging

from .repository import *

Node = namedtuple('Node', 'identifier version')


class Resolver(object):
    def __init__(self, root, dependencies_for_node):
        self.root = root
        self.dependencies_for_node = dependencies_for_node

    def build_graph(self, dependency_filter=None):
        # type: ([str]) -> DiGraph

        def populate_graph(graph, parent, depth=0):
            graph.add_node(parent)

            for child_identifier, child_versions in self._dependencies_for_node(parent):
                for child_version in child_versions:
                    child = Node(child_identifier, child_version)
                    if dependency_filter and dependency_filter(child.identifier, child.version) == False:
                        continue
                    graph.add_edge(parent, child)
                    populate_graph(graph, child, depth=depth + 1)

        graph = DiGraph()
        populate_graph(graph=graph, parent=self.root)
        return graph

    def resolve(self):
        # type: () -> DiGraph

        for dependency, revisions in self._dependencies_for_node(self.root):
            logging.debug('<ref>{}</ref> <rev>{}</rev>'.format(dependency, revisions))

        logging.debug('Building universal graph')

        # Build a graph up of _all_ version of _all_ dependencies
        graph = self.build_graph()

        logging.debug('Universal graph has {} nodes, {} edges.'.format(number_of_nodes(graph), number_of_edges(graph)))

        # Build a dictionary of all versions of all dependencies
        all_dependencies = defaultdict(set)
        for dependency, version in dfs_preorder_nodes(graph):
            all_dependencies[dependency].add(version)

        ################################################################################################################

        def prune_1():
            for dependency, versions in all_dependencies.items():
                if len(versions) <= 1:
                    continue
                for version in sorted(versions):
                    if len(graph.predecessors(Node(dependency, version))) <= 1:
                        graph.remove_node(Node(dependency, version))
                        all_dependencies[dependency].remove(version)
                    if len(versions) <= 1:
                        break

        def prune_2():
            def prune(node):
                mini = defaultdict(set)
                if not node in graph:
                    return

                for dependency, version in graph.successors(node):
                    mini[dependency].add(version)
                for dependency, versions in mini.items():
                    some = all_dependencies[dependency]
                    difference = some.difference(versions)
                    for version in difference:
                        graph.remove_node(Node(dependency, version))
                        all_dependencies[dependency].remove(version)
                for successor in graph.successors(node):
                    prune(successor)

            prune(self.root)

        ################################################################################################################

        logging.debug('<sub>Pruning graph</sub>')

        prune_1()
        prune_2()

        logging.debug('Pruned universal graph has {} nodes, {} edges.'.format(number_of_nodes(graph), number_of_edges(graph)))

        ################################################################################################################

        dependencies = set([(dependency, sorted(versions)[-1]) for dependency, versions in sorted(all_dependencies.items())])

        ################################################################################################################

        logging.debug('<sub>Creating new filtered graph</sub>')

        graph = self.build_graph(dependency_filter=lambda child, child_version: (child, child_version) in dependencies)

        logging.debug('Pruned universal graph has {} nodes, {} edges.'.format(number_of_nodes(graph), number_of_edges(graph)))

        ################################################################################################################

        return graph

    def resolve_build_order(self):
        # type: () -> [(ProjectIdentifier, Revision)]
        graph = self.resolve()
        logging.debug('<sub>Topologically sorting graph</sub>')
        build_order = topological_sort(graph, reverse=True)
        return build_order

    def resolve_versions(self, dependencies):
        # type: (ProjectIdentifier, Revision) -> [ProjectIdentifier, Tag]
        """Given an array of project identifier/version pairs work out the build order"""

        graph = DiGraph()
        versions_for_identifier = dict(dependencies)
        for identifier, version in dependencies:
            parent = Node(identifier, version)
            graph.add_node(parent)

            assert isinstance(version, Revision)

            dependencies_for_node = self._dependencies_for_node(Node(identifier, version))

            for dependency, _ in dependencies_for_node:
                version = versions_for_identifier[dependency]
                child = Node(dependency, version)
                graph.add_edge(parent, child)
        build_order = topological_sort(graph, reverse=True)

        return build_order

    def _dependencies_for_node(self, node):
        # type: (Edge, bool) -> [Any, [Any]]
        return self.dependencies_for_node(node)


def dump(stream, graph, node, depth=0):
    count = len(graph.predecessors(node))

    stream.write("{}{} {}\n".format('\t' * depth, node, count))
    for child in sorted(graph[node].keys()):
        dump(stream, graph, child, depth + 1)
