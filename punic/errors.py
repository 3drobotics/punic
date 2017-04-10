from __future__ import division, absolute_import, print_function

import contextlib
import logging

class PunicException(Exception):
    pass

class RepositoryNotClonedError(PunicException):
    pass

class CartfileNotFound(PunicException):
    def __init__(self, path):
        self.path = path

class PunicRepresentableError(PunicException):
    pass

class InvalidCarthageSpecification(PunicException):
    pass

class NoSuchRevision(PunicException):
    def __init__(self, repository, revision):
        self.repository = repository
        self.revision = revision
        self.message = "No such revision <rev>'{}'</rev> in repository <ref>'{}'</ref>.".format(self.revision, self.repository)

class UnimplementedError(PunicException):
    pass

class DeprecatedError(PunicException):
    pass

class XcodeVersionError(PunicException):
    pass

class BuildFailure(PunicException):
    pass




@contextlib.contextmanager
def error_handling():
    try:
        yield
    except RepositoryNotClonedError:
        logging.error('<err>Error</err>: No locally cloned repository found. Did you neglect to run `punic fetch` first?')
        exit(-1)
    except CartfileNotFound as e:
        logging.error('<err>Error</err>: No Cartfile found at path: <ref>{}</ref>. Did you neglect to run `punic resolve` first?'.format(e.path))
        exit(-1)
    except NoSuchRevision as e:
        logging.error('<err>Error</err>: No such revision {} found in repository {}'.format(e.revision, e.repository))
        logging.error('Are you sure you are using the latest bits? Try an explicit `punic fetch` or use `punic bootstrap` instead of `punic build`')
        exit(-1)
    except PunicRepresentableError as e:
        logging.error(e.message)
        exit(-1)
    except:
        raise
