from __future__ import unicode_literals, division, absolute_import
from builtins import *  # noqa pylint: disable=unused-import, redefined-builtin
from future.utils import native

import logging
import os
import re

from flexget import plugin
from flexget.event import event
from flexget.utils.template import RenderError
from flexget.utils.pathscrub import pathscrub

log = logging.getLogger('deluge_rename')


class DelugeRename(object):
    schema = {
        'type': 'object',
        'properties': {
            'content_filename': {'type': 'string'},
            'main_file_only': {'type': 'boolean'},
            'main_file_ratio': {'type': 'number'},
            'container_directory': {'type': 'string'},
            'hide_sparse_files': {'type': 'boolean'},
            'keep_subs': {'type': 'boolean'},
        },
        'additionalProperties': False
    }

    def prepare_config(self, config):
        config.setdefault('main_file_ratio', 0.90)
        config.setdefault('keep_subs', True) # does nothing without 'content_filename' or 'main_file_only' enabled
        config.setdefault('hide_sparse_files', False) # does nothing without 'main_file_only' enabled
        return config

    def on_task_modify(self, task, config):
        config = self.prepare_config(config)
        keep_subs = config.get('keep_subs')
        log.debug('keep_subs: %s', keep_subs)
        for entry in task.accepted:
            modified_content_files = []
            if entry.get('content_files'):
                content_files = entry['content_files']
            else:
                entry.fail('`content_files` not present in entry')
            if entry.get('content_size'):
                total_size = entry['content_size'] * 1024 * 1024
            else:
                entry.fail('Unable to determine total content size')
            big_file_name = ''
            if config.get('content_filename') or config.get('main_file_only'):
                # find a file that makes up more than main_file_ratio (default: 90%) of the total size
                main_file = sub_file = main_file_key = sub_file_key = None
                sub_exts = [".srt", ".sub"]
                for count, file_info in enumerate(content_files):
                    current_file = file_info['new_path'] if file_info.get('new_path') else file_info['path']
                    if file_info['size'] > (total_size * config.get('main_file_ratio')) and not main_file:
                        main_file_key = count
                        main_file = current_file
                    ext = os.path.splitext(current_file)[1]
                    if keep_subs and ext in sub_exts and not sub_file:
                        sub_file_key = count
                        sub_file = current_file

                if main_file is not None:
                    # proceed with renaming only if such a big file is found
                    content_filename = ''
                    try:
                        content_filename = entry.get('content_filename', config.get('content_filename', ''))
                        content_filename = pathscrub(entry.render(content_filename))
                    except RenderError as e:
                        log.error('Error rendering content_filename for `%s`: %s', entry['title'], e)
                    container_directory = ''
                    try:
                        container_directory = pathscrub(entry.render(entry.get('container_directory',
                                                                               config.get('container_directory', ''))))
                    except RenderError as e:
                        log.error('Error rendering container_directory for `%s`: %s', entry['title'], e)
                    # check for single file torrents so we dont add unnecessary folders
                    if len(content_files) > 1:
                        # check for top folder in user config
                        top_files_dir = ''
                        if container_directory:
                            top_files_dir = container_directory + '/'
                        if config.get('content_filename') and os.path.dirname(content_filename) is not '':
                            top_files_dir = top_files_dir + os.path.dirname(content_filename) + '/'
                        else:
                            top_files_dir = top_files_dir + os.path.dirname(main_file) + '/'
                    else:
                        top_files_dir = '/'

                    if content_filename:
                        # rename the main file
                        big_file_name = '{}{}{}'.format(top_files_dir,
                                                        os.path.basename(content_filename),
                                                        os.path.splitext(main_file)[1])
                        file_info = {'new_path': big_file_name, 'download': 1}
                        content_files[main_file_key].update(file_info)
                        log.verbose('Main file `%s` will be renamed to `%s`', main_file, big_file_name)

                        # rename subs along with the main file
                        if sub_file is not None and keep_subs:
                            sub_file_name = '{}{}'.format(os.path.splitext(big_file_name)[0],
                                                          os.path.splitext(sub_file)[1])
                            file_info = {'new_path': sub_file_name, 'download': 1}
                            content_files[sub_file_key].update(file_info)
                            log.verbose('Subs file `%s` will be renamed to `%s`', main_file, big_file_name)

                    hide_sparse_files = (config.get('main_file_only') and config.get('hide_sparse_files'))
                    if len(content_files) > 1:
                        for count, f in enumerate(content_files):
                            filepath = ''
                            # hide the other sparse files that are not supposed to download but are created anyway
                            # http://dev.deluge-torrent.org/ticket/1827
                            # Made sparse files behave better with deluge http://flexget.com/ticket/2881
                            if hide_sparse_files and count != main_file_key and (count != sub_file_key
                                                                                 or not keep_subs):
                                file_path = '{}{}{}'.format(top_files_dir, '.sparse_files/', os.path.basename(f))
                                content_files[count].update(file_info)
                            elif container_directory:
                                file_path = '{}{}'.format(top_files_dir, os.path.basename(f))
                            if filepath:
                                file_info = {'new_path': file_path}
                                content_files[count].update(file_info)

                    # update the entry with the results
                    entry['content_files'] = content_files
                else:
                    log.warning('No files in `%s` are > %d%% of content size, no files renamed.',
                                entry['title'], config.get('main_file_ratio') * 100)

@event('plugin.register')
def register_plugin():
    plugin.register(DelugeRename, 'deluge_rename', api_ver=2)