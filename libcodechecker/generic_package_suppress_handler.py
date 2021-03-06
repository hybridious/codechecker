# -------------------------------------------------------------------------
#                     The CodeChecker Infrastructure
#   This file is distributed under the University of Illinois Open Source
#   License. See LICENSE.TXT for details.
# -------------------------------------------------------------------------
"""
Handler for suppressing a bug.
"""

import os

from libcodechecker import suppress_file_handler
from libcodechecker import suppress_handler
from libcodechecker.logger import get_logger

# Warning! this logger should only be used in this module.
LOG = get_logger('system')


class GenericSuppressHandler(suppress_handler.SuppressHandler):

    def __init__(self, suppress_file, allow_write):
        """
        Create a new suppress handler with a suppress_file as backend.
        """
        super(GenericSuppressHandler, self).__init__()

        self.__suppress_info = []
        self.__allow_write = allow_write

        if suppress_file:
            self.suppress_file = suppress_file
            self.__have_memory_backend = True
            self.__revalidate_suppress_data()
        else:
            self.__have_memory_backend = False
            self.__arrow_write = False

            if allow_write:
                raise ValueError("Can't create allow_write=True suppress "
                                 "handler without a backend file.")

    def __revalidate_suppress_data(self):
        """Reload the information in the suppress file to the memory."""

        if not self.__have_memory_backend:
            # Do not load and have suppress data stored in memory if not
            # needed.
            return

        with open(self.suppress_file, 'r') as file_handle:
            self.__suppress_info = suppress_file_handler.\
                get_suppress_data(file_handle)

    def store_suppress_bug_id(self, bug_id, file_name, comment):

        if not self.__allow_write:
            return True

        ret = suppress_file_handler.write_to_suppress_file(self.suppress_file,
                                                           bug_id,
                                                           file_name,
                                                           comment)
        self.__revalidate_suppress_data()
        return ret

    def remove_suppress_bug_id(self, bug_id, file_name):

        if not self.__allow_write:
            return True

        ret = suppress_file_handler.remove_from_suppress_file(
            self.suppress_file,
            bug_id,
            file_name)
        self.__revalidate_suppress_data()
        return ret

    def get_suppressed(self, bug):

        return any([suppress for suppress in self.__suppress_info
                    if suppress[0] == bug['hash_value'] and
                    suppress[1] == os.path.basename(bug['file_path'])])
