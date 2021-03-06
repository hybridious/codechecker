# -------------------------------------------------------------------------
#                     The CodeChecker Infrastructure
#   This file is distributed under the University of Illinois Open Source
#   License. See LICENSE.TXT for details.
# -------------------------------------------------------------------------
"""
Parse the plist output of an analyzer and convert it to a report for
further processing.

With the newer clang releases more information is available in the plist files.

* Before Clang v3.7:
  - Checker name is misssing (tried to detect based on the description)
  - Report hash is not avilable (generated based on the report path elemens
    see report handling and plist parsing modules for more details.

* Clang v3.7:
  - Checker name is available in the plist
  - Report hash is still missing (hash is generated as before)

* After Clang v3.8:
  - Checker name is available
  - Report hash is available

* Clang-tidy:
  - No plist format is provided in the available releases (v3.9 and before)
  - Checker name can be parsed from the output
  - Report hash is generated based on the report path elements the same way as
    for Clang versions before v3.7

"""

from collections import defaultdict
import math
import os
import plistlib
import sys
import traceback
from xml.parsers.expat import ExpatError

from libcodechecker import suppress_handler
from libcodechecker import util
from libcodechecker.logger import get_logger
from libcodechecker.report import Report
from libcodechecker.report import generate_report_hash

LOG = get_logger('report')


def get_checker_name(diagnostic, path=""):
    """
    Check if checker name is available in the report.
    Checker name was not available in older clang versions before 3.7.
    """
    checker_name = diagnostic.get('check_name')
    if not checker_name:
        LOG.warning("Check name wasn't found in the plist file '%s'. "
                    % path)
        checker_name = "unknown"
    return checker_name


def get_report_hash(diagnostic, source_file):
    """
    Check if checker name is available in the report.
    Checker hash was not available in older clang versions before 3.8.
    """

    report_hash = diagnostic.get('issue_hash_content_of_line_in_context')
    if not report_hash:
        # Generate hash value if it is missing from the report.
        report_hash \
            = generate_report_hash(diagnostic['path'],
                                   source_file,
                                   get_checker_name(diagnostic))
    return report_hash


def parse_plist(path, source_root=None, allow_plist_update=True):
    """
    Parse the reports from a plist file.
    One plist file can contain multiple reports.
    """
    LOG.debug("Parsing plist: " + path)

    reports = []
    files = []
    try:
        plist = plistlib.readPlist(path)

        files = plist['files']

        diag_changed = False
        for diag in plist['diagnostics']:

            available_keys = diag.keys()

            main_section = {}
            for key in available_keys:
                # Skip path it is handled separately.
                if key != 'path':
                    main_section.update({key: diag[key]})

            # We need to extend information for plist files generated
            # by older clang version (before 3.7).
            main_section['check_name'] = get_checker_name(diag, path)

            # We need to extend information for plist files generated
            # by older clang version (before 3.8).
            file_path = files[diag['location']['file']]
            if source_root:
                file_path = os.path.join(source_root, file_path.lstrip('/'))

            report_hash = get_report_hash(diag, file_path)
            main_section['issue_hash_content_of_line_in_context'] = \
                report_hash

            if 'issue_hash_content_of_line_in_context' not in diag:
                # If the report hash was not in the plist, we set it in the
                # diagnostic section for later update.
                diag['issue_hash_content_of_line_in_context'] = report_hash
                diag_changed = True

            bug_path_items = [item for item in diag['path']]

            report = Report(main_section, bug_path_items, files)
            reports.append(report)

        if diag_changed and allow_plist_update:
            # If the diagnostic section has changed we update the plist file.
            # This way the client will always send a plist file where the
            # report hash field is filled.
            plistlib.writePlist(plist, path)
    except (ExpatError, TypeError, AttributeError) as err:
        LOG.error('Failed to process plist file: ' + path +
                  ' wrong file format?')
        LOG.error(err)
    except IndexError as iex:
        LOG.error('Indexing error during processing plist file ' +
                  path)
        LOG.error(type(iex))
        LOG.error(repr(iex))
        _, _, exc_traceback = sys.exc_info()
        traceback.print_tb(exc_traceback, limit=1, file=sys.stdout)
    except Exception as ex:
        LOG.error('Error during processing reports from the plist file: ' +
                  path)
        traceback.print_exc()
        LOG.error(type(ex))
        LOG.error(ex)
    finally:
        return files, reports


def fids_in_range(rng):
    """
    Get the file ids from a range.
    """
    fids = []
    for r in rng:
        for l in r:
            fids.append(l['file'])
    return fids


def fids_in_edge(edges):
    """
    Get the file ids from an edge.
    """
    fids = []
    for e in edges:
        start = e['start']
        end = e['end']
        for l in start:
            fids.append(l['file'])
        for l in end:
            fids.append(l['file'])
    return fids


def fids_in_path(report_data, file_ids_to_remove):
    """
    Skip diagnostic sections and collect file ids in
    report paths for the remaining diagnostic sections.
    """
    all_fids = []

    kept_diagnostics = []

    for diag in report_data['diagnostics']:

        if diag['location']['file'] in file_ids_to_remove:
            continue

        kept_diagnostics.append(diag)

        for pe in diag['path']:
            path_fids = []
            try:
                fids = fids_in_range(pe['ranges'])
                path_fids.extend(fids)
            except KeyError:
                pass

            try:
                fid = pe['location']['file']
                path_fids.append(fid)
            except KeyError:
                pass

            try:
                fids = fids_in_edge(pe['edges'])
                path_fids.extend(fids)
            except KeyError:
                pass

            all_fids.extend(path_fids)

    return all_fids, kept_diagnostics


def remove_report_from_plist(plist_content, skip_handler):
    """
    Parse the original plist content provided by the analyzer
    and return a new plist content where reports were removed
    if they should be skipped.

    WARN !!!!
    If the 'files' array in the plist is modified all of the
    diagnostic section (control, event ...) nodes should be
    re indexed to use the proper file array indexes!!!
    """
    new_data = {}
    try:
        report_data = plistlib.readPlistFromString(plist_content)
    except (ExpatError, TypeError, AttributeError) as ex:
        LOG.error("Failed to parse plist content, "
                  "keeping the original version")
        LOG.error(plist_content)
        LOG.error(ex)
        return plist_content

    file_ids_to_remove = []

    try:
        for i, f in enumerate(report_data['files']):
            if skip_handler.should_skip(f):
                file_ids_to_remove.append(i)

        _, kept_diagnostics = fids_in_path(report_data, file_ids_to_remove)
        report_data['diagnostics'] = kept_diagnostics

        new_data = report_data
        res = plistlib.writePlistToString(new_data)
        return res

    except KeyError:
        LOG.error("Failed to modify plist content, "
                  "keeping the original version")
        return plist_content


def skip_report_from_plist(plist_file, skip_handler):
    """
    Rewrites the provided plist file where reports
    were removed if they should be skipped.
    """
    with open(plist_file, 'r+') as plist:
        new_plist_content = remove_report_from_plist(plist.read(),
                                                     skip_handler)
        plist.seek(0)
        plist.write(new_plist_content)
        plist.truncate()


class PlistToPlaintextFormatter(object):
    """
    Parse and format plist reports to a more human readable format.
    """

    def __init__(self,
                 suppress_handler,
                 skip_handler,
                 severity_map,
                 analyzer_type="clangsa"):

        self.__analyzer_type = analyzer_type
        self.__severity_map = severity_map
        self.__print_steps = False
        self.suppress_handler = suppress_handler
        self.skiplist_handler = skip_handler

    @property
    def print_steps(self):
        """
        Print the multiple steps for a bug if there is any.
        """
        return self.__print_steps

    @print_steps.setter
    def print_steps(self, value):
        """
        Print the multiple steps for a bug if there is any.
        """
        self.__print_steps = value

    @staticmethod
    def __format_location(event, source_file):
        loc = event['location']
        line = util.get_line(source_file, loc['line'])
        if line == '':
            return line

        marker_line = line[0:(loc['col'] - 1)]
        marker_line = ' ' * (len(marker_line) + marker_line.count('\t'))
        return '%s%s^' % (line.replace('\t', '  '), marker_line)

    @staticmethod
    def __format_bug_event(name, severity, event, source_file):

        loc = event['location']
        fname = os.path.basename(source_file)
        if name:
            return '[%s] %s:%d:%d: %s [%s]' % (severity,
                                               fname,
                                               loc['line'],
                                               loc['col'],
                                               event['message'],
                                               name)
        else:
            return '%s:%d:%d: %s' % (fname,
                                     loc['line'],
                                     loc['col'],
                                     event['message'])

    def __write_bugs(self, output, reports, files, analyzed_source_file,
                     report_stats):
        """
        Write out the bugs to the output and collect report statistics.
        """

        severity_stats = defaultdict(int)
        file_stats = defaultdict(int)
        report_count = defaultdict(int)

        report_num = len(reports)
        if report_num > 0:
            index_format = '    %%%dd, ' % \
                           int(math.floor(math.log10(report_num)) + 1)

        non_suppressed = 0
        for report in reports:
            events = [i for i in report.bug_path if i.get('kind') == 'event']
            f_path = files[events[-1]['location']['file']]
            if self.skiplist_handler and \
                    self.skiplist_handler.should_skip(f_path):
                LOG.debug(report + ' is skipped (in ' + f_path + ")")
                continue
            hash_value = report.main['issue_hash_content_of_line_in_context']
            bug = {'hash_value': hash_value,
                   'file_path': f_path
                   }
            if self.suppress_handler and \
                    self.suppress_handler.get_suppressed(bug):
                LOG.debug("Suppressed by suppress file: {0}".format(report))
                continue

            last_report_event = report.bug_path[-1]
            source_file = files[last_report_event['location']['file']]
            report_line = last_report_event['location']['line']
            report_hash = report.main['issue_hash_content_of_line_in_context']
            checker_name = report.main['check_name']
            sp_handler = suppress_handler.SourceSuppressHandler(source_file,
                                                                report_line,
                                                                report_hash,
                                                                checker_name)

            # Check for suppress comment.
            suppress_data = sp_handler.get_suppressed()
            if suppress_data:
                if self.suppress_handler:
                    hash_value, file_name, comment = suppress_data
                    self.suppress_handler.store_suppress_bug_id(hash_value,
                                                                file_name,
                                                                comment)

                continue

            file_stats[f_path] += 1
            severity = self.__severity_map.get(checker_name,
                                               'UNSPECIFIED')
            severity_stats[severity] += 1
            report_count["report_count"] += 1

            output.write(self.__format_bug_event(checker_name,
                                                 severity,
                                                 last_report_event,
                                                 source_file))
            output.write('\n')
            output.write(self.__format_location(last_report_event,
                                                source_file))
            output.write('\n')

            if self.print_steps:
                output.write('  Report hash: ' + report_hash + '\n')
                output.write('  Steps:\n')
                for index, event in enumerate(events):
                    output.write(index_format % (index + 1))
                    source_file = files[event['location']['file']]
                    output.write(self.__format_bug_event(None,
                                                         None,
                                                         event,
                                                         source_file))
                    output.write('\n')
            output.write('\n')

            non_suppressed += 1

        basefile_print = (' ' +
                          os.path.basename(analyzed_source_file)) \
            if analyzed_source_file and \
            len(analyzed_source_file) > 0 else ''

        if non_suppressed == 0:
            output.write('Found no defects while analyzing%s\n' %
                         (basefile_print))
        else:
            output.write(
                'Found %d defect(s) while analyzing%s\n\n' %
                (non_suppressed, basefile_print))

        report_stats["severity"] = severity_stats
        report_stats["files"] = file_stats
        report_stats["reports"] = report_count

    def parse_and_write(self, plist_file, analyzed_source_file,
                        output=sys.stdout):
        """
        Parse a plist report file format it to a more human readable format.
        """
        report_stats = {}
        try:
            files, reports = parse_plist(plist_file)
        except Exception as ex:
            traceback.print_stack()
            LOG.error('The generated plist is not valid!')
            LOG.error(ex)
            return 1

        self.__write_bugs(output, reports, files, analyzed_source_file,
                          report_stats)

        return report_stats
