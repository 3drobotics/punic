from __future__ import division, absolute_import, print_function

__all__ = ['Xcode', 'XcodeProject', 'uuids_from_binary', 'XcodeBuildProduct', 'XcodeBuildArguments']

import re
import affirm
from pathlib2 import Path
from memoize import mproperty
import six
import logging

from .runner import *
from .semantic_version import *
from .errors import *
from .utilities import *

class Xcode(object):
    _all_xcodes = None
    _default_xcode = None

    @classmethod
    def default(cls):
        if not Xcode._default_xcode:
            Xcode.find_all()
        return Xcode._default_xcode

    @classmethod
    def with_version(cls, version):
        if isinstance(version, six.string_types):
            version = SemanticVersion.string(version)
        if isinstance(version, int):
            version = SemanticVersion(major=version, minor=0)
        found = [xcode for xcode in Xcode.find_all() if xcode.version == version]

        if len(found) == 0:
            raise XcodeVersionError("No Xcode with version {} found".format(version))
        elif len(found) > 1:
            raise XcodeVersionError("Multiple Xcodes of version {} found: {}".format(version, found))
        else:
            return found[0]

    @classmethod
    def find_all(cls):
        if Xcode._all_xcodes is None:

            all_xcodes = list(Path("/Applications").glob("Xcode*.app"))
            all_xcodes = [path for path in all_xcodes if Xcode.is_xcode(path)]

            all_xcodes = set([Xcode(path) for path in all_xcodes])

            output = runner.check_run('/usr/bin/mdfind \'kMDItemCFBundleIdentifier="com.apple.dt.Xcode" and kMDItemContentType="com.apple.application-bundle"\'')
            all_xcodes.update(Xcode(Path(path)) for path in output.strip().split("\n") if path)

            default_developer_dir_path = Path(runner.check_run(['xcode-select', '-p']).strip())
            if six.text_type(default_developer_dir_path).endswith('/Contents/Developer'):
                default_developer_dir_path = default_developer_dir_path.parent.parent
            Xcode._default_xcode = Xcode(default_developer_dir_path)
            Xcode._default_xcode.is_default = True

            all_xcodes.discard(Xcode._default_xcode)
            all_xcodes.add(Xcode._default_xcode)

            Xcode._all_xcodes = all_xcodes

        return Xcode._all_xcodes

    @classmethod
    def is_xcode(cls, path):
        if not path.is_dir():
            return False
        if path.suffix != '.app':
            return False

        info_plist_path = path / 'Contents/Info.plist'
        bundle_identifier = plist_get_key(info_plist_path, 'CFBundleIdentifier')
        if bundle_identifier not in ['com.apple.dt.Xcode']:
            return False

        return True


    def __init__(self, path):
        assert(path.is_dir())
        assert(path.suffix == '.app')

        self.path = path
        self.is_default = False
        self.developer_dir_path = self.path / 'Contents/Developer'

    @mproperty
    def version(self):
        output = self.check_call(['xcodebuild', '-version'], env={'DEVELOPER_DIR': str(self.developer_dir_path)})
        match = re.match(r'^Xcode (?P<version>.+)\nBuild version (?P<build>.+)', output)
        return SemanticVersion.string(match.groupdict()['version'])

    @mproperty
    def internal_version(self):
        try:
            output = self.check_call(['xcodebuild', '-version'], env={'DEVELOPER_DIR': str(self.developer_dir_path)})
            match = re.match(r'^Xcode (?P<version>.+)\nBuild version (?P<build>.+)', output)
        except Exception as e:
            print(self.developer_dir_path)
            raise
        return match.groupdict()['build']


    # noinspection PyMethodMayBeStatic
    def call(self, command, **kwargs):
        command = runner.convert_args(command)
        command = ['/usr/bin/xcrun'] + command

        if not self.is_default:
            env = dict()
            env['DEVELOPER_DIR'] = str(self.developer_dir_path)
            if 'env' in kwargs:
                env.update(kwargs['env'])
            kwargs['env'] = env

        result = runner.run(command, **kwargs)
        return result

    # noinspection PyMethodMayBeStatic
    def check_call(self, command, **kwargs):
        kwargs['check'] = True
        result = self.call(command, **kwargs)
        return result.stdout

    def __repr__(self):
        s = '{} ({}/{})'.format(self.path, self.version, self.internal_version)
        if self.is_default:
            s += ' (default)'
        return s

    def __eq__(self, other):
        return self.internal_version == other.internal_version

    def __ne__(self, other):
        return not (self == other)

    def __lt__(self, other):
        return self.internal_version < other.internal_version

    def __hash__(self):
        return hash(self.internal_version)

########################################################################################################################

class XcodeProject(object):
    def __init__(self, punic, xcode, path, identifier):
        assert punic
        assert xcode
        assert path
        assert identifier

        self.punic = punic
        self.xcode = xcode
        self.path = path
        self.identifier = identifier

    @property
    def targets(self):
        return self.info[0]

    @property
    def configurations(self):
        return self.info[1]

    @property
    def default_configuration(self):
        return self.info[3]

    @property
    def scheme_names(self):
        return self.info[2]

    @mproperty
    def schemes(self):
        return [Scheme(self, scheme_name) for scheme_name in self.scheme_names]

    def scheme_named(self, name):
        return [scheme for scheme in self.schemes if scheme.name == name][0]



    @mproperty
    def info(self):
        arguments = XcodeBuildArguments()
        arguments.project = self.path
        output = self.check_call(subcommand='-list', cache_key=self.identifier)
        targets, configurations, schemes, default_configuration = _parse_info(output)
        return targets, configurations, schemes, default_configuration

    def build_settings(self, arguments):
        # type: (XcodeBuildArguments) -> dict()
        output = self.check_call(subcommand='-showBuildSettings', arguments=arguments, cache_key=self.identifier)
        return _parse_build_settings(output)

    def build(self, arguments):
        # type: (XcodeBuildArguments) -> dict()
        try:
            self.check_call(subcommand='build', arguments=arguments)
        except CalledProcessError as e:
            logging.error('<err>Error</err>: Failed to build - result code <echo>{}</echo>'.format(e.returncode))
            logging.error('Command: <echo>{}</echo>'.format(e.cmd))
            logging.error(e.output)
            exit(e.returncode)

        build_settings = self.build_settings(arguments=arguments)
        scheme = self.scheme_named(arguments.scheme)
        assert scheme

        products = []
        for target in scheme.framework_targets:
            products.append(XcodeBuildProduct.build_settings(build_settings[target.name]))
        return products

    def check_call(self, subcommand, arguments=None, **kwargs):
        # type: (str, XcodeBuildArguments) -> [str]
        assert not arguments or isinstance(arguments, XcodeBuildArguments)
        arguments = arguments.to_list() if arguments else []
        command = ['xcodebuild', '-project' if self.path.suffix =='.xcodeproj' else '-workspace', self.path] + arguments + [subcommand]
        return self.xcode.check_call(command, **kwargs)


########################################################################################################################

class Scheme(object):
    def __init__(self, project, name):
        self.project = project
        self.name = name

    def __repr__(self):
        return 'Scheme("{}")'.format(self.name)


    @mproperty
    def targets(self):
        arguments = XcodeBuildArguments(scheme=self.name)
        build_settings = self.project.build_settings(arguments=arguments)
        targets = [Target(self.project, self, target_name) for target_name in build_settings.keys()]
        return targets

    @mproperty
    def supported_platform_names(self):
        supported_platform_names = set()

        for target in self.framework_targets:
            supported_platform_names.update(target.supported_platform_names)
        return supported_platform_names


    @mproperty
    def framework_targets(self):
        targets = [target for target in self.targets if target.product_is_framework]
        return targets


########################################################################################################################

class Target(object):
    def __init__(self, project, scheme, name):
        assert isinstance(project, XcodeProject)
        assert isinstance(scheme, Scheme)
        assert isinstance(name, six.string_types)
        self.project = project
        self.scheme = scheme
        self.name = name

    def __repr__(self):
        return 'Target("{}")'.format(self.name)

    @mproperty
    def build_settings(self):
        arguments = XcodeBuildArguments(scheme=self.scheme.name)
        build_settings = self.project.build_settings(arguments=arguments)
        return build_settings[self.name]

    @property
    def supported_platform_names(self):
        return self.build_settings.get('SUPPORTED_PLATFORMS', '').split(' ')

    @property
    def package_type(self):
        return self.build_settings.get('PACKAGE_TYPE', None)

    @property
    def product_is_framework(self):
        mach_o_type = self.build_settings.get('MACH_O_TYPE', None)
        return mach_o_type == 'mh_dylib' and self.package_type == 'com.apple.package-type.wrapper.framework'

########################################################################################################################

class XcodeBuildArguments(object):
    def __init__(self, scheme=None, target=None, configuration=None, sdk=None, toolchain=None, jobs=None, derived_data_path=None, arguments=None):
        self.scheme = scheme
        self.target = target
        self.configuration = configuration
        self.sdk = sdk
        self.toolchain = toolchain
        self.jobs = jobs
        self.derived_data_path = derived_data_path
        self.arguments = {
            'ONLY_ACTIVE_ARCH': 'NO',
            'BITCODE_GENERATION_MODE': 'bitcode',
            'CODE_SIGNING_REQUIRED': 'NO',
            'CODE_SIGN_IDENTITY': '',
            'CARTHAGE': 'YES',
        }

        if arguments:
            self.arguments.update(arguments)

    def __repr__(self):
        return 'XcodeBuildArguments({})'.format(self.__dict__)

    def to_list(self):
        # type: () -> [Any]
        parts = []
        parts += ['-scheme', self.scheme] if self.scheme else []
        parts += ['-target', self.target] if self.target else []
        parts += ['-configuration', self.configuration] if self.configuration else []
        parts += ['-sdk', self.sdk] if self.sdk else []
        parts += ['-toolchain', self.toolchain] if self.toolchain else []
        parts += ['-jobs', self.jobs] if self.jobs else []
        parts += ['-derivedDataPath', self.derived_data_path] if self.derived_data_path else []
        parts += (['{}={}'.format(key, value) for key, value in self.arguments.items()]) if self.arguments else []
        return parts


########################################################################################################################


class XcodeBuildProduct(object):
    @classmethod
    def build_settings(cls, build_settings):
        assert isinstance(build_settings, dict)
        product = XcodeBuildProduct()
        product.build_settings = build_settings
        product.full_product_name = build_settings['FULL_PRODUCT_NAME']  # 'Example.framework'
        product.product_name = build_settings['PRODUCT_NAME']  # 'Example'
        product.executable_name = build_settings['EXECUTABLE_NAME']  # 'Example'
        product.target_build_dir = Path(build_settings['TARGET_BUILD_DIR'])  # ~/Library/Developer/Xcode/DerivedData/Example-<random>/Build/Products/<configuration>-<sdk>
        product.sdk = re.match(r'([A-Za-z]+)', build_settings['SDK_NAME']).groups()[0]

        return product

    def __repr__(self):
        return 'BuildProduct({full_product_name}, {sdk})'.format(**self.__dict__)

    @classmethod
    def string(cls, string):
        lines = iter(string.splitlines())
        matches = (re.match(r'^    (.+) = (.+)$', line) for line in lines)
        matches = (match.groups() for match in matches if match)
        build_settings = dict(matches)

        product = XcodeBuildProduct.build_settings(build_settings=build_settings)
        return product

    def __init__(self):
        self.build_settings = dict()
        self.full_product_name = None
        self.product_name = None
        self.executable_name = None
        self.target_build_dir = None

    @property
    def product_path(self):
        return self.target_build_dir / self.full_product_name

    @property
    def executable_path(self):
        return self.product_path / self.executable_name

    @property
    def uuids(self):
        return uuids_from_binary(self.executable_path)

    @property
    def bcsymbolmap_paths(self):
        paths = [self.target_build_dir / (uuid + '.bcsymbolmap') for uuid in self.uuids]
        paths = [path for path in paths if path.exists()]
        return paths

    @property
    def module_paths(self):
        modules_source_path = self.product_path / "Modules/{}.swiftmodule".format(self.executable_name)
        if not modules_source_path.exists():
            return []
        return list(modules_source_path.glob("*"))


########################################################################################################################

def _parse_info(string):
    lines = iter(string.splitlines())
    targets = []
    configurations = []
    schemes = []
    default_configuration = None

    try:
        while True:
            line = six.next(lines)
            if re.match(r'^\s+Targets:$', line):
                while True:
                    line = six.next(lines)
                    match = re.match(r'        (.+)', line)
                    if not match:
                        break
                    else:
                        targets.append(match.group(1))
            if re.match(r'^\s+Build Configurations:$', line):
                while True:
                    line = six.next(lines)
                    match = re.match(r'        (.+)', line)
                    if not match:
                        break
                    else:
                        configurations.append(match.group(1))
            if re.match(r'^\s+Schemes:$', line):
                while True:
                    line = six.next(lines)
                    match = re.match(r'        (.+)', line)
                    if not match:
                        break
                    else:
                        schemes.append(match.group(1))

            match = re.match(r'^\s+If no build configuration is specified and -scheme is not passed then "(.+)" is used.', line)
            if match:
                default_configuration = match.group(1)

    except StopIteration:
        pass


    return targets, configurations, schemes, default_configuration


########################################################################################################################

def _parse_build_settings(string):
    # TODO : This is woefully inadequate

    lines = iter(string.splitlines())
    lines = (line.strip() for line in lines)

    all_build_settings = list()
    current_build_settings = dict()

    for line in lines:
        match = re.match(r'^Build settings for action (?P<action>.+) and target "?(?P<target>.+)"?:$', line)
        if match:
            if current_build_settings:
                all_build_settings.append(current_build_settings)
            current_build_settings = dict()
            next_action, next_target = (match.groupdict()['action'], match.groupdict()['target'])
            assert next_action == 'build'
            continue
        match = re.match(r'^(?P<setting>.+) = (?P<value>.+)$', line)
        if match:
            setting = match.groupdict()["setting"]
            value = match.groupdict()["value"]
            current_build_settings[setting] = value

    if current_build_settings:
        all_build_settings.append(current_build_settings)

    return dict([(build_settings['TARGET_NAME'], build_settings) for build_settings in all_build_settings if 'TARGET_NAME' in build_settings])


########################################################################################################################

def uuids_from_binary(path):
    command = ['/usr/bin/xcrun', 'dwarfdump', '--uuid', path]
    output = runner.check_run(command)
    lines = output.splitlines()
    matches = [re.match(r'^UUID: ([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}) \((.+)\) (.+)$', line) for line in lines]
    uuids = [match.group(1) for match in matches]
    return uuids
