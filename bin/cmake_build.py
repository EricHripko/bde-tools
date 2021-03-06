#!/usr/bin/env python

from __future__ import print_function

import argparse
import errno
import os
import platform
import subprocess
import sys
import multiprocessing

def enum(*sequential, **named):
    enums = dict(zip(sequential, range(len(sequential))), **named)
    return type('Enum', (), enums)

def replace_path_sep(path, sep = '/'):
    if not path:
        return path
    return path.replace(os.path.sep, sep)

def value_or_env(value, envVariableName, humanReadableName, required=False):
    ''' Get value which is either provided or from a fallback
        environment location
    '''
    ret = value if value else os.getenv(envVariableName)
    if required and not ret:
        raise RuntimeError(
            "{} was not specified using either a command-line\n"
            "argument or environment variable {}".format(humanReadableName, envVariableName)
        )
    return ret

def cmake_module_path_or_env(value, envVariableName):
    ''' Evaluate the cmake modules path that provided ether in the command line, environment
        variable or guessed
    '''
    ret = value if value else os.getenv(envVariableName)
    if not ret:
        upd = os.path.dirname
        ret = os.path.join(upd(upd(os.path.realpath(__file__))), 'cmake')

    return ret

class JobsOptions:
    Type = enum('ALL_AVAILABLE', 'FIXED')
    def __init__(self, parsedArgs):
        if not parsedArgs:
            self.type = JobsOptions.Type.ALL_AVAILABLE
            self.count = None
        else:
            self.type = JobsOptions.Type.FIXED
            self.count = parsedArgs

class Options:
    def __init__(self, args):
        self.ufid = value_or_env(args.ufid, 'BDE_WAF_UFID', 'UFID', required='configure' in args.cmd)
        self.build_dir = \
            replace_path_sep(value_or_env(args.build_dir, 'BDE_WAF_BUILD_DIR', 'Build directory', required=True))
        self.cmake_module_path = \
            replace_path_sep(cmake_module_path_or_env(args.cmake_module_path, 'CMAKE_MODULE_PATH'))
        self.prefix = replace_path_sep(value_or_env(args.prefix, 'PREFIX', 'Installation prefix'))
        self.targets = args.targets
        self.tests = args.tests
        self.jobs = JobsOptions(args.jobs)
        self.generator = args.generator if hasattr(args, 'generator') else None
        self.timeout = args.timeout
        self.refroot = \
            replace_path_sep(value_or_env(args.refroot, 'DISTRIBUTION_REFROOT', 'Distribution refroot'))

class Platform:
    @staticmethod
    def generator(options):
        host_platform = platform.system()
        if 'Windows' == host_platform:
            if not options.generator:
                raise RuntimeError('Please specify a generator for the cmake project.')

            generatorMap = {
                'msvc2013': 'Visual Studio 12 2013',
                'msvc2015': 'Visual Studio 14 2015',
                'msvc2017': 'Visual Studio 15 2017'
            }

            if options.generator in generatorMap:
                bitness = ' Win64' if options.ufid and '64' in options.ufid else ''
                return generatorMap[options.generator] + bitness
            else:
                return options.generator
        elif 'SunOS' == host_platform:
            return 'Unix Makefiles'
        else:
            return 'Ninja'

    @staticmethod
    def generator_choices():
        host_platform = platform.system()
        if 'Windows' == host_platform:
            return ['msvc' + str(y) for y in [2013, 2015, 2017]] + ['Ninja']
        elif 'SunOS' == host_platform:
            return ['Unix Makefiles']
        else:
            return ['Ninja', 'Unix Makefiles']

    @staticmethod
    def generator_jobs_arg(gen, options):
        formatStrings = {}
        if gen.startswith('Visual Studio'):
            formatStrings[JobsOptions.Type.ALL_AVAILABLE] = '/maxcpucount'
            formatStrings[JobsOptions.Type.FIXED] = '/maxcpucount:{}'
        elif 'Makefiles' in gen:
            formatStrings[JobsOptions.Type.ALL_AVAILABLE] = '-j'
            formatStrings[JobsOptions.Type.FIXED] = '-j{}'
        else:
            formatStrings[JobsOptions.Type.ALL_AVAILABLE] = ''
            formatStrings[JobsOptions.Type.FIXED] = '-j{}'
        return formatStrings[options.jobs.type].format(options.jobs.count)

    @staticmethod
    def ctest_jobs_arg(options):
        if options.jobs.type == JobsOptions.Type.FIXED:
            return '-j{}'.format(options.jobs.count)
        elif options.jobs.type == JobsOptions.Type.ALL_AVAILABLE:
            return '-j{}'.format(multiprocessing.cpu_count())

        raise RuntimeError()

    @staticmethod
    def allBuildTarget(options):
        gen = Platform.generator(options)
        if gen.startswith('Visual Studio'):
            return 'ALL_BUILD'
        else:
            return 'all'

def run_command(cmd, cwd=None):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    (out, err) = p.communicate()
    ret = p.returncode

    if ret:
        print("{}".format(out), file=sys.stdout)
        print("{}".format(err), file=sys.stderr)

    return ret

def wrapper():
    parser = argparse.ArgumentParser(prog=os.path.basename(sys.argv[0]))
    parser.add_argument('cmd', nargs='+', choices=['configure', 'build', 'install'])
    parser.add_argument('--build_dir')
    parser.add_argument('-j', '--jobs', type=int)

    group = parser.add_argument_group('configure', 'Configuration options')
    group.add_argument('-u', '--ufid')
    group.add_argument('--prefix')
    group.add_argument('--cmake-module-path')
    group.add_argument('--refroot')

    genChoices = Platform.generator_choices()
    if len(genChoices) > 1:
        group.add_argument('-G', choices=genChoices, dest='generator')

    group = parser.add_argument_group('build', 'Build options')
    group.add_argument('--targets', nargs='+')
    group.add_argument('--tests', choices=['build', 'run'])

    group = parser.add_argument_group('test', 'Testing options')
    group.add_argument('--timeout', type=int, default=120)

    group = parser.add_argument_group('install', 'Installation options')

    args = parser.parse_args()

    options = Options(args)

    if 'configure' in args.cmd:
        configure(options)

    if 'build' in args.cmd:
        build(options)

    if 'install' in args.cmd:
        install(options)
    return


def mkdir_if_not_present(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

def configure(options):
    """ Create build directory and generate build system.
    """
    mkdir_if_not_present(options.build_dir)
    # todo - detect generator change

    configure_cmd = ['cmake', os.getcwd(),
                     '-G' + Platform.generator(options),
                     '-DCMAKE_MODULE_PATH:PATH=' + options.cmake_module_path,
                     '-DUFID:STRING=' + options.ufid,
                    ]
    if options.prefix:
        configure_cmd.append('-DCMAKE_PREFIX_PATH:PATH=' + options.prefix)
        configure_cmd.append('-DCMAKE_INSTALL_PREFIX:PATH=' + options.prefix)
    if options.refroot:
        configure_cmd.append('-DDISTRIBUTION_REFROOT:PATH=' + options.refroot)

    subprocess.check_call(configure_cmd, cwd = options.build_dir)

class CacheInfo:
    def __init__(self, build_dir):
        self.generator = None
        self.multiconfig = False
        self.build_type = None

        cacheFileName = os.path.join(build_dir, 'CMakeCache.txt')
        if not os.path.isfile(cacheFileName):
            raise RuntimeError('The project build configuration not found in ' + build_dir)

        for line in open(cacheFileName):
            if line.startswith('CMAKE_GENERATOR:'):
                self.generator = line.strip().split('=')[1]
            elif line.startswith('CMAKE_CONFIGURATION_TYPES:'):
                self.multiconfig = True
            elif line.startswith('CMAKE_BUILD_TYPE:'):
                self.build_type = line.strip().split('=')[1]


def build_target(target, build_dir, extra_args):
    build_cmd = ['cmake', '--build', build_dir]
    if target:
        build_cmd += ['--target', target]

    # filter out empty extra_args or Ninja wont like it
    build_cmd += [arg for arg in extra_args if arg]

    subprocess.check_call(build_cmd)

def build(options):
    """ Build
    """
    cache_info = CacheInfo(options.build_dir)
    extra_args = []
    if cache_info.multiconfig:
        extra_args += ['--config', cache_info.build_type]
    extra_args += ['--', Platform.generator_jobs_arg(cache_info.generator, options)]

    target_list = options.targets if options.targets else ['all']
    for target in target_list:
        if options.tests and not target.endswith('.t'):
            full_target = target + '.t'
        else:
            full_target = None if target == 'all' else target
        build_target(full_target, options.build_dir, extra_args)

    if 'run' == options.tests:
        test_cmd = ['ctest',
                    '--output-on-failure',
                    Platform.ctest_jobs_arg(options)
                    ]
        if cache_info.multiconfig:
            test_cmd += ['-C', cache_info.build_type]

        if options.timeout > 0:
            test_cmd += ['--timeout', str(options.timeout)]

        if 'all' not in target_list:
            test_pattern = "|".join(['(^)'+t+'($)' for t in options.targets])
            test_cmd += ['-L', test_pattern]

        subprocess.check_call(test_cmd, cwd = options.build_dir)

def install(options):
    """ Install
    """

    build_target('install', options.build_dir, [])


if __name__ == '__main__':
    try:
        wrapper()
    except Exception as e:
        print("Error: {}".format(e), file=sys.stderr)
        sys.exit(1)
