from __future__ import division, absolute_import, print_function

__all__ = ['punic_cli', 'main']

import logging
import logging.handlers
import sys
import click
from click_didyoumean import DYMGroup
import networkx as nx
from pathlib2 import Path

import punic
from .copy_frameworks import *
from .errors import *
from .logger import *
from .semantic_version import *
from .utilities import *
from .version_check import *
from .config_init import *
from .carthage_cache import *
from punic.graph import make_graph
import punic.shshutil as shutil
from punic import *
from .runner import *
from .checkout import *
from .search import *
from .xcode import Xcode

@click.group(cls=DYMGroup)
@click.option('--echo', default=False, is_flag=True, help="""Echo all commands to terminal.""")
@click.option('--verbose', default=False, is_flag=True, help="""Verbose logging.""")
@click.option('--color/--no-color', default=None, is_flag=True, help="""TECHNICOLOR.""")
@click.option('--timing/--no-timing', default=False, is_flag=True, help="""Log timing info""")
@click.pass_context
def punic_cli(context, echo, verbose, timing, color):
    ### TODO: Clean this up!

    # Configure click
    context.token_normalize_func = lambda x: x if not x else x.lower()

    # Configure logging
    level = logging.DEBUG if verbose else logging.INFO

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    formatter = HTMLFormatter()

    # create console handler and set level to debug
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    # add ch to logger
    logger.addHandler(stream_handler)

    # TODO: This needs to be a better location
    logs_path = Path('~/Library/Application Support/io.schwa.Punic/Logs').expanduser()
    if not logs_path.exists():
        logs_path.mkdir(parents=True)

    log_path = logs_path / "punic.log"
    needs_rollover = log_path.exists()

    file_handler = logging.handlers.RotatingFileHandler(str(log_path), backupCount=4)
    if needs_rollover:
        file_handler.doRollover()
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(HTMLStripperFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")))
    logger.addHandler(file_handler)

    for name in ['boto', 'requests.packages.urllib3']:
        named_logger = logging.getLogger(name)
        named_logger.setLevel(logging.WARNING)
        named_logger.propagate = True

    runner.echo = echo

    # Set up punic
    punic = Punic()
    punic.config.log_timings = timing
    context.obj = punic
    punic.config.verbose = verbose
    punic.config.echo = verbose

    # Color:
    if color is None:
        if punic.config.continuous_integration:
            color = False

    if color is None:
        color = True


    punic.config.color = color
    formatter.color = color
    logger.color = color



@punic_cli.command()
@click.pass_context
@click.option('--use-submodules', default=None, help="""Add dependencies as Git submodules""")
@click.option('--use-ssh', default=None, help="""Use SSH for downloading GitHub repositories""")
def fetch(context, **kwargs):
    """Fetch the project's dependencies.."""
    logging.info("<cmd>fetch</cmd>")
    punic = context.obj
    punic.config.fetch = True  # obviously
    punic.config.update(**kwargs)

    with timeit('fetch'):
        with error_handling():
            punic.fetch()


@punic_cli.command()
@click.pass_context
@click.option('--fetch/--no-fetch', default=True, is_flag=True, help="""Controls whether to fetch dependencies.""")
@click.option('--use-submodules', default=None, help="""Add dependencies as Git submodules""")
@click.option('--use-ssh', default=None, is_flag=True, help="""Use SSH for downloading GitHub repositories""")
def resolve(context, **kwargs):
    """Resolve dependencies and output `Carthage.resolved` file.

    This sub-command does not build dependencies. Use this sub-command when a dependency has changed and you just want to update `Cartfile.resolved`.
    """
    punic = context.obj
    logging.info("<cmd>Resolve</cmd>")
    punic.config.update(**kwargs)


    with timeit('resolve'):
        with error_handling():
            punic.resolve()


@punic_cli.command()
@click.pass_context
@click.option('--configuration', default=None, help="""Dependency configurations to build. Usually 'Release' or 'Debug'.""")
@click.option('--platform', default=None, help="""Platform to build. Comma separated list.""")
@click.option('--fetch/--no-fetch', default=True, is_flag=True, help="""Controls whether to fetch dependencies.""")
@click.option('--xcode-version', default=None, help="""Xcode version to use""")
@click.option('--toolchain', default=None, help="""Xcode toolchain to use""")
@click.option('--dry-run', default=None, is_flag=True, help="""Do not actually perform final build""")
@click.option('--use-submodules', default=None, help="""Add dependencies as Git submodules""")
@click.option('--use-ssh', default=None, is_flag=True, help="""Use SSH for downloading GitHub repositories""")
@click.argument('deps', nargs=-1)
def build(context, **kwargs):
    """Fetch and build the project's dependencies."""
    logging.info("<cmd>Build</cmd>")
    punic = context.obj
    punic.config.update(**kwargs)

    deps = kwargs['deps']

    logging.debug('Platforms: {}'.format(punic.config.platforms))
    logging.debug('Configuration: {}'.format(punic.config.configuration))

    with timeit('build'):
        with error_handling():
            punic.build(dependencies=deps)


@punic_cli.command()
@click.pass_context
@click.option('--configuration', default=None, help="""Dependency configurations to build. Usually 'Release' or 'Debug'.""")
@click.option('--platform', default=None, help="""Platform to build. Comma separated list.""")
@click.option('--fetch/--no-fetch', default=True, is_flag=True, help="""Controls whether to fetch dependencies.""")
@click.option('--xcode-version', default=None, help="""Xcode version to use""")
@click.option('--toolchain', default=None, help="""Xcode toolchain to use""")
@click.option('--use-submodules', default=None, help="""Add dependencies as Git submodules""")
@click.option('--use-ssh', default=None, is_flag=True, help="""Use SSH for downloading GitHub repositories""")
@click.argument('deps', nargs=-1)
def update(context, **kwargs):
    """Update and rebuild the project's dependencies."""
    logging.info("<cmd>Update</cmd>")
    punic = context.obj
    punic.config.update(**kwargs)

    deps = kwargs['deps']

    with timeit('update'):
        with error_handling():
            punic.resolve()
            punic.build(dependencies=deps)


@punic_cli.command()
@click.pass_context
@click.option('--derived-data', default=False, is_flag=True, help="""Clean the punic derived data directory.""")
@click.option('--caches', default=False, is_flag=True, help="""Clean the global punic files.""")
@click.option('--build', default=False, is_flag=True, help="""Clean the locate Carthage/Build directorys.""")
@click.option('--all', default=False, is_flag=True, help="""Clean all.""")
def clean(context, derived_data, caches, build, all):
    """Clean project & punic environment."""
    logging.info("<cmd>Clean</cmd>")
    punic = context.obj

    with timeit('clean'):
        if build or all:
            logging.info('Erasing Carthage/Build directory')
            if punic.config.build_path.exists():
                shutil.rmtree(punic.config.build_path)

        if derived_data or all:
            logging.info('Erasing derived data directory')
            if punic.config.derived_data_path.exists():
                shutil.rmtree(punic.config.derived_data_path)

        if caches or all:
            if punic.config.repo_cache_directory.exists():
                logging.info('Erasing {}'.format(punic.config.repo_cache_directory))
                shutil.rmtree(punic.config.repo_cache_directory)
            logging.info('Erasing run cache')
            runner.reset()


@punic_cli.command()
@click.pass_context
@click.option('--fetch/--no-fetch', default=True, is_flag=True, help="""Controls whether to fetch dependencies.""")
@click.option('--use-submodules', default=None, help="""Add dependencies as Git submodules""")
@click.option('--use-ssh', default=None, is_flag=True, help="""Use SSH for downloading GitHub repositories""")
@click.option('--open', default=False, is_flag=True, help="""Open the graph image file.""")
def graph(context, fetch, use_submodules, use_ssh, open):
    """Output resolved dependency graph."""
    logging.info("<cmd>Graph</cmd>")
    punic = context.obj
    punic.config.fetch = fetch
    if use_submodules:
        punic.config.use_submodules = use_submodules
    if use_ssh:
        punic.config.use_ssh = use_ssh
    with timeit('graph'):
        make_graph(punic, open)


@punic_cli.command(name='copy-frameworks')
@click.pass_context
def copy_frameworks(context):
    """In a Run Script build phase, copies each framework specified by a SCRIPT_INPUT_FILE environment variable into the built app bundle."""
    copy_frameworks_main()


# noinspection PyUnusedLocal
@punic_cli.command()
@click.pass_context
@click.option('--check/--no-check', default=True, help="""Check for latest version.""")
@click.option('--simple', is_flag=True, default=False, help="""Only display simple version info. Implies --no-check.""")
@click.option('--xcode', is_flag=True, default=False, help="""Display xcode versions.""")
def version(context, check, simple, xcode):
    """Display the current version of Punic."""

    from .styling import styled_print

    if simple:
        print("{}".format(punic.__version__))
    else:

        styled_print('Punic version: <version>{}</version>'.format(punic.__version__))

        if punic.config.verbose:
            styled_print('Punic path: <ref>{}</ref> '.format(punic.__file__))

        sys_version = sys.version_info
        sys_version = SemanticVersion.from_dict(dict(major=sys_version.major, minor=sys_version.minor, micro=sys_version.micro, releaselevel=sys_version.releaselevel, serial=sys_version.serial, ))
        styled_print('Python version: <version>{}</version>'.format(sys_version))
        if punic.config.verbose:
            styled_print('Python path: <path>{}</path> '.format(sys.executable))

        if xcode or punic.config.verbose:
            styled_print("Xcode(s):")
            for xcode in punic.xcode.Xcode.find_all():
                s = '\t<path>{}</path>: <version>{}</version>'.format(xcode.path, xcode.version)
                if xcode.is_default:
                    s += ' (default)'
                    styled_print(s)

        if check:
            version_check(verbose=True, timeout=None, failure_is_an_option=False)


@punic_cli.command()
@click.pass_context
def readme(context):
    """Opens punic readme in your browser (https://github.com/schwa/punic/blob/HEAD/README.markdown)"""
    click.launch('https://github.com/schwa/punic/blob/HEAD/README.markdown')


@punic_cli.command()
@click.option('--configuration', default=None, help="""Dependency configurations to build. Usually 'Release' or 'Debug'.""")
@click.option('--platform', default=None, help="""Platform to build. Comma separated list.""")
@click.option('--fetch/--no-fetch', default=True, is_flag=True, help="""Controls whether to fetch dependencies.""")
@click.option('--xcode-version', default=None, help="""Xcode version to use""")
@click.option('--toolchain', default=None, help="""Xcode toolchain to use""")
@click.option('--use-submodules', default=None, help="""Add dependencies as Git submodules""")
@click.option('--use-ssh', default=None, is_flag=True, help="""Use SSH for downloading GitHub repositories""")
@click.argument('deps', nargs=-1)
@click.pass_context
def list(context, **kwargs):
    """Lists all platforms, projects, xcode projects, schemes for all dependencies."""

    print('Xcodes:')
    for xcode in Xcode.find_all():
        print('\t', xcode)

    punic = context.obj
    punic.config.update(**kwargs)
    deps = kwargs['deps']

    config = punic.config

    configuration, platforms = config.configuration, config.platforms

    if not config.build_path.exists():
        config.build_path.mkdir(parents=True)

    filtered_dependencies = punic._ordered_dependencies(name_filter=deps)

    checkouts = [Checkout(punic=punic, identifier=identifier, revision=revision) for identifier, revision in filtered_dependencies]

    print('Project/Workspace:')
    tree = {}

    for platform in platforms:
        tree[platform.name] = {}
        for checkout in checkouts:
            tree[platform.name][str(checkout.identifier)] = {'projects':{}}
            checkout.prepare()
            for project in checkout.projects:
                tree[platform.name][str(checkout.identifier)]['projects'][project.path.name] = {'schemes':{}}
                tree[platform.name][str(checkout.identifier)]['projects'][project.path.name]['path'] = str(project.path.relative_to(config.checkouts_path))
                schemes = project.schemes
                schemes = [scheme for scheme in schemes if scheme.framework_targets]
                schemes = [scheme for scheme in schemes if platform.device_sdk in scheme.supported_platform_names]
                tree[platform.name][str(checkout.identifier)]['projects'][project.path.name]['schemes'] = [scheme.name for scheme in schemes]

    import yaml

    yaml.safe_dump(tree, stream = sys.stdout)

@punic_cli.command()
@click.pass_context
@click.option('--configuration', default=None, help="""Dependency configurations to build. Usually 'Release' or 'Debug'.""")
@click.option('--platform', default=None, help="""Platform to build. Comma separated list.""")
@click.option('--xcode', default=None)
def init(context, **kwargs):
    """Generate punic configuration file."""
    config_init(**kwargs)


@punic_cli.group(cls=DYMGroup)
@click.pass_context
def cache(context):
    """Cache punic build artifacts to Amazon S3"""
    pass


@cache.command()
@click.pass_context
@click.option('--xcode-version', default=None, help="""Xcode version to use""")
@click.option('--force', default=False, is_flag=True, help="""Force publishing""")
def publish(context, xcode_version, force):
    """Generates and uploads the cache archive for the current Cartfile.resolved"""
    with error_handling():
        logging.info("<cmd>Cache Publish</cmd>")
        punic = context.obj
        if xcode_version:
            punic.config.xcode_version = xcode_version
        carthage_cache = CarthageCache(config=punic.config)
        logging.info("Cache filename: <ref>'{}'</ref>".format(carthage_cache.archive_name_for_project()))
        carthage_cache.publish(force = force)



@cache.command()
@click.pass_context
@click.option('--xcode-version', default=None, help="""Xcode version to use""")
def install(context, xcode_version):
    """Installs the cache archive for the current Cartfile.resolved"""
    with error_handling():
        logging.info("<cmd>Cache Install</cmd>")
        punic = context.obj
        if xcode_version:
            punic.config.xcode_version = xcode_version
        carthage_cache = CarthageCache(config=punic.config)
        logging.info("Cache filename: <ref>'{}'</ref>".format(carthage_cache.archive_name_for_project()))
        carthage_cache.install()



@punic_cli.command()
@click.pass_context
@click.argument('name')
@click.option('--append', is_flag=True, default=False, help="""Add a selected project to a Cartfile""")
@click.option('--language', default='swift', help="""Search for projects of specified language""")
def search(context, name, append, language):
    """Search github for repositories and optionally add them to a Cartfile."""
    punic = context.obj

    github_search(punic, name, cartfile_append=append, language=language)


def main():
    punic_cli()
