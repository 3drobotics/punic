from __future__ import division, absolute_import, print_function

__version__ = '0.2.10'
__all__ = ['Punic', 'current_session']

import os
from copy import copy
from pathlib2 import Path
import logging
from collections import defaultdict

from .cartfile import Cartfile
from .checkout import Checkout
from .config import config
from .repository import Repository, Revision
from .resolver import Resolver, Node
from .runner import runner
from .specification import ProjectIdentifier, Specification, VersionPredicate, VersionOperator
from .xcode import XcodeBuildArguments
import punic.shshutil as shutil
from .errors import NoSuchRevision

current_session = None


class Punic(object):
    __slots__ = ['root_path', 'config', 'all_repositories', 'root_project']

    def __init__(self, root_path=None):

        global current_session

        if not current_session:
            current_session = self

        if not root_path:
            root_path = Path.cwd()

        self.config = config

        root_project_identifier = ProjectIdentifier(overrides=None, project_name=self.config.root_path.name)

        self.all_repositories = {root_project_identifier: Repository(punic=self, identifier=root_project_identifier, repo_path=self.config.root_path),}

        self.root_project = self._repository_for_identifier(root_project_identifier)

    def _resolver(self):
        return Resolver(root=Node(self.root_project.identifier, None), dependencies_for_node=self._dependencies_for_node)

    def _dependencies_for_node(self, node):
        assert not node.version or isinstance(node.version, Revision)
        dependencies = self.dependencies_for_project_and_tag(identifier=node.identifier, tag=node.version)
        return dependencies

    def resolve(self):
        # type: (bool)
        resolver = self._resolver()
        build_order = resolver.resolve_build_order()

        for index, value in enumerate(build_order[:-1]):
            dependency, version = value
            logging.debug('{} <ref>{}</ref> <rev>{}</rev> <ref>{}</ref>'.format(index + 1, dependency, version.revision if version else '', dependency.remote_url))

        specifications = [Specification(identifier=dependency, predicate=VersionPredicate('"{}"'.format(version.revision))) for dependency, version in build_order[:-1]]
        logging.debug("<sub>Saving</sub> <ref>Cartfile.resolved</ref>")

        cartfile = Cartfile(use_ssh=self.config.use_ssh, specifications=specifications)
        cartfile.write((self.config.root_path / 'Cartfile.resolved').open('w'))

    def graph(self):
        # type: (bool) -> DiGraph
        return self._resolver().resolve()

    # TODO: This can be deprecated and the fetch flag relied on instead
    def fetch(self, dependencies=None):

        configuration, platforms = self.config.configuration, self.config.platforms

        if not self.config.build_path.exists():
            self.config.build_path.mkdir(parents=True)

        filtered_dependencies = self._ordered_dependencies(name_filter=dependencies)

        checkouts = [Checkout(punic=self, identifier=identifier, revision=revision) for identifier, revision in filtered_dependencies]
        for checkout in checkouts:
            checkout.prepare()


    def build(self, dependencies):
        # type: ([str])

        logging.info('Using xcode version: {}'.format(self.config.xcode))

        configuration, platforms = self.config.configuration, self.config.platforms

        if not self.config.build_path.exists():
            self.config.build_path.mkdir(parents=True)

        filtered_dependencies = self._ordered_dependencies(name_filter=dependencies)

        checkouts = [Checkout(punic=self, identifier=identifier, revision=revision) for identifier, revision in filtered_dependencies]

        skips = self.config.skips

        def filter_dependency(platform, checkout, project, scheme):
            platform = platform.name
            checkout = checkout.identifier.project_name
            project = project.path.name
            scheme = scheme.name

            for skip in skips:
                current = [ platform, checkout, project, scheme ][:len(skip)]
                if skip == current:
                    # print(skip)
                    # print(current)
                    # print('SKIP?')
                    return False
            return True

        for checkout in checkouts:
            checkout.prepare()
            for project in checkout.projects:
                for platform in platforms:
                    schemes = project.schemes

                    schemes = [scheme for scheme in schemes if scheme.framework_targets]
                    schemes = [scheme for scheme in schemes if platform.device_sdk in scheme.supported_platform_names]
                    for scheme in schemes:
                        if not filter_dependency(platform, checkout, project, scheme):
                            logging.warn('<err>Warning:</err> <sub>Skipping</sub>: {} / {} / {} / {}'.format(platform, checkout.identifier.project_name, project.path.name, scheme.name))
                            continue
                        self._build_one(platform, project, scheme.name, configuration)

    def _ordered_dependencies(self, name_filter=None):
        # type: (bool, [str]) -> [(ProjectIdentifier, Revision)]

        cartfile = Cartfile(use_ssh=self.config.use_ssh, overrides=config.repo_overrides)

        resolved_path = self.config.root_path / 'Cartfile.resolved'
        if not resolved_path.exists():
            return []

        cartfile.read(resolved_path)

        def _predicate_to_revision(spec):
            repository = self._repository_for_identifier(spec.identifier)
            if spec.predicate.operator == VersionOperator.commitish:
                try:
                    revision = Revision(repository=repository, revision=spec.predicate.value, revision_type=Revision.Type.commitish, check = True)
                except Exception as e:
                    logging.warning(e.message)
                    return None
                else:
                    return revision
            else:
                raise Exception("Cannot convert spec to revision: {}".format(spec))

        dependencies = [(spec.identifier, _predicate_to_revision(spec)) for spec in cartfile.specifications]
        resolved_dependencies = self._resolver().resolve_versions(dependencies)
        resolved_dependencies = [dependency for dependency in resolved_dependencies if dependency[0].matches(name_filter)]
        return resolved_dependencies

    def _repository_for_identifier(self, identifier):
        # type: (ProjectIdentifier) -> Repository
        if identifier in self.all_repositories:
            return self.all_repositories[identifier]
        else:
            repository = Repository(self, identifier=identifier)
            if self.config.fetch:
                repository.fetch()
            self.all_repositories[identifier] = repository
            return repository

    def dependencies_for_project_and_tag(self, identifier, tag):
        # type: (ProjectIdentifier, Revision) -> [ProjectIdentifier, [Revision]]

        assert isinstance(identifier, ProjectIdentifier)
        assert not tag or isinstance(tag, Revision)

        repository = self._repository_for_identifier(identifier)
        specifications = repository.specifications_for_revision(tag)

        def make(specification):
            repository = self._repository_for_identifier(specification.identifier)
            tags = repository.revisions_for_predicate(specification.predicate)
            if specification.predicate.operator == VersionOperator.commitish:
                try:
                    revision = Revision(repository=repository, revision=specification.predicate.value, revision_type=Revision.Type.commitish, check = True)
                except NoSuchRevision as e:
                    logging.warning("<err>Warning</err>: {}".format(e.message))
                    return None
                tags.append(revision)
                tags.sort()
            assert len(tags)
            return repository.identifier, tags

        dependencies = [make(specification) for specification in specifications]
        dependencies = [dependency for dependency in dependencies if dependency]
        return dependencies

    def _build_one(self, platform, project, scheme, configuration):

        if self.config.dry_run:
            for sdk in platform.sdks:
                logging.warn('<sub>DRY-RUN: (Not) Building</sub>: <ref>{}</ref> (scheme: {}, sdk: {}, configuration: {})...'.format(project.path.name, scheme, sdk, configuration))
            return

        all_products = []

        toolchain = self.config.toolchain

        # Build device & simulator (if sim exists)
        for sdk in platform.sdks:
            logging.info('<sub>Building</sub>: <ref>{}</ref> (scheme: {}, sdk: {}, configuration: {})...'.format(project.path.name, scheme, sdk, configuration))

            derived_data_path = self.config.derived_data_path

            resolved_configuration = configuration if configuration else project.default_configuration
            if not resolved_configuration:
                logging.warn("<err>Warning</err>: No configuration specified for project and no default configuration found. This could be a problem.")

            arguments = XcodeBuildArguments(scheme=scheme, configuration=resolved_configuration, sdk=sdk, toolchain=toolchain, derived_data_path=derived_data_path)

            all_products += project.build(arguments=arguments)

        self._post_process(platform, all_products)

    def _post_process(self, platform, products):
        # type: (punic.platform.Platform, List)

        ########################################################################################################

        logging.debug("<sub>Post processing</sub>...")

        # TODO: QUESTION: Is it possible that this could mix targets with different SDKs?
        products_by_name_then_sdk = defaultdict(dict)
        for product in products:
            products_by_name_then_sdk[product.full_product_name][product.sdk] = product


        for products_by_sdk in products_by_name_then_sdk.values():

            products = products_by_sdk.values()

            # TODO: By convention sdk[0] is always the device sdk (e.g. 'iphoneos' and not 'iphonesimulator')
            primary_sdk = platform.sdks[0]

            device_product = products_by_sdk[primary_sdk]

            ########################################################################################################

            output_product = copy(device_product)
            output_product.target_build_dir = self.config.build_path / platform.output_directory_name

            ########################################################################################################

            logging.debug('<sub>Copying binary</sub>...')
            if output_product.product_path.exists():
                shutil.rmtree(output_product.product_path)

            if not device_product.product_path.exists():
                raise Exception("No product at: {}".format(device_product.product_path))

            shutil.copytree(device_product.product_path, output_product.product_path, symlinks=True)

            ########################################################################################################

            if len(products) > 1:
                logging.debug('<sub>Lipo-ing</sub>...')
                executable_paths = [product.executable_path for product in products]
                command = ['/usr/bin/xcrun', 'lipo', '-create'] + executable_paths + ['-output', output_product.executable_path]
                runner.check_run(command)
                mtime = executable_paths[0].stat().st_mtime
                os.utime(str(output_product.executable_path), (mtime, mtime))

            ########################################################################################################

            logging.debug('<sub>Copying swiftmodule files</sub>...')
            for product in products:
                for path in product.module_paths:
                    relative_path = path.relative_to(product.product_path)
                    shutil.copyfile(path, output_product.product_path / relative_path)

            ########################################################################################################

            logging.debug('<sub>Copying bcsymbolmap files</sub>...')
            for product in products:
                for path in product.bcsymbolmap_paths:
                    shutil.copy(path, output_product.target_build_dir)

            ########################################################################################################

            logging.debug('<sub>Producing dSYM files</sub>...')
            command = ['/usr/bin/xcrun', 'dsymutil', str(output_product.executable_path), '-o', str(output_product.target_build_dir / (output_product.executable_name + '.dSYM'))]
            runner.check_run(command)

            ########################################################################################################