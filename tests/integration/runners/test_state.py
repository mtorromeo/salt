# -*- coding: utf-8 -*-
'''
Tests for the state runner
'''

# Import Python Libs
from __future__ import absolute_import, print_function, unicode_literals
import errno
import logging
import os
import shutil
import signal
import tempfile
import time
import textwrap
import threading
from salt.ext.six.moves import queue

# Import Salt Testing Libs
from tests.support.case import ShellCase
from tests.support.unit import skipIf
from tests.support.paths import TMP
from tests.support.helpers import flaky, expensiveTest
from tests.support.mock import MagicMock, patch

# Import Salt Libs
import salt.utils.platform
import salt.utils.event
import salt.utils.files
import salt.utils.json
import salt.utils.stringutils
import salt.utils.yaml

# Import 3rd-party libs
from salt.ext import six

log = logging.getLogger(__name__)


class StateRunnerTest(ShellCase):
    '''
    Test the state runner.
    '''
    def add_to_queue(self, q, cmd):
        '''
        helper method to add salt-run
        return data to a queue
        '''
        ret = self.run_run(cmd)
        q.put(ret)
        q.task_done()

    @flaky
    def test_orchestrate_output(self):
        '''
        Ensure the orchestrate runner outputs useful state data.

        In Issue #31330, the output only contains ['outputter:', '    highstate'],
        and not the full stateful return. This tests ensures we don't regress in that
        manner again.

        Also test against some sample "good" output that would be included in a correct
        orchestrate run.
        '''
        #ret_output = self.run_run_plus('state.orchestrate', 'orch.simple')['out']
        ret_output = self.run_run('state.orchestrate orch.simple')
        bad_out = ['outputter:', '    highstate']
        good_out = ['    Function: salt.state',
                    '      Result: True',
                    'Succeeded: 1 (changed=1)',
                    'Failed:    0',
                    'Total states run:     1']

        # First, check that we don't have the "bad" output that was displaying in
        # Issue #31330 where only the highstate outputter was listed
        self.assertIsNot(bad_out, ret_output)

        # Now test that some expected good sample output is present in the return.
        for item in good_out:
            self.assertIn(item, ret_output)

    def test_orchestrate_nested(self):
        '''
        test salt-run state.orchestrate and failhard with nested orchestration
        '''
        if os.path.exists('/tmp/ewu-2016-12-13'):
            os.remove('/tmp/ewu-2016-12-13')

        _, code = self.run_run(
                'state.orchestrate nested-orch.outer',
                with_retcode=True)

        self.assertFalse(os.path.exists('/tmp/ewu-2016-12-13'))
        self.assertNotEqual(code, 0)

    def test_orchestrate_state_and_function_failure(self):
        '''
        Ensure that returns from failed minions are in the changes dict where
        they belong, so they can be programatically analyzed.

        See https://github.com/saltstack/salt/issues/43204
        '''
        self.run_run('saltutil.sync_modules')
        ret = salt.utils.json.loads(
            '\n'.join(
                self.run_run('state.orchestrate orch.issue43204 --out=json')
            )
        )
        # Drill down to the changes dict
        state_ret = ret['data']['master']['salt_|-Step01_|-Step01_|-state']['changes']
        func_ret = ret['data']['master']['salt_|-Step02_|-runtests_helpers.nonzero_retcode_return_false_|-function']['changes']

        # Remove duration and start time from the results, since they would
        # vary with each run and that would make it impossible to test.
        for item in ('duration', 'start_time'):
            state_ret['ret']['minion']['test_|-test fail with changes_|-test fail with changes_|-fail_with_changes'].pop(item)

        self.assertEqual(
            state_ret,
            {
                'out': 'highstate',
                'ret': {
                    'minion': {
                        'test_|-test fail with changes_|-test fail with changes_|-fail_with_changes': {
                            '__id__': 'test fail with changes',
                            '__run_num__': 0,
                            '__sls__': 'orch.issue43204.fail_with_changes',
                            'changes': {
                                'testing': {
                                    'new': 'Something pretended to change',
                                    'old': 'Unchanged'
                                }
                            },
                            'comment': 'Failure!',
                            'name': 'test fail with changes',
                            'result': False,
                        }
                    }
                }
            }
        )

        self.assertEqual(
            func_ret,
            {'out': 'highstate', 'ret': {'minion': False}}
        )

    def test_orchestrate_target_exists(self):
        '''
        test orchestration when target exists
        while using multiple states
        '''
        ret = self.run_run('state.orchestrate orch.target-exists')

        first = ['          ID: core',
                 '    Function: salt.state',
                 '      Result: True']

        second = ['          ID: test-state',
                  '    Function: salt.state',
                  '      Result: True']

        third = ['          ID: cmd.run',
                 '    Function: salt.function',
                 '      Result: True']

        ret_out = [first, second, third]

        for out in ret_out:
            for item in out:
                self.assertIn(item, ret)

    def test_orchestrate_retcode(self):
        '''
        Test orchestration with nonzero retcode set in __context__
        '''
        self.run_run('saltutil.sync_runners')
        self.run_run('saltutil.sync_wheel')
        ret = '\n'.join(self.run_run('state.orchestrate orch.retcode'))

        for result in ('          ID: test_runner_success\n'
                       '    Function: salt.runner\n'
                       '        Name: runtests_helpers.success\n'
                       '      Result: True',

                       '          ID: test_runner_failure\n'
                       '    Function: salt.runner\n'
                       '        Name: runtests_helpers.failure\n'
                       '      Result: False',

                       '          ID: test_wheel_success\n'
                       '    Function: salt.wheel\n'
                       '        Name: runtests_helpers.success\n'
                       '      Result: True',

                       '          ID: test_wheel_failure\n'
                       '    Function: salt.wheel\n'
                       '        Name: runtests_helpers.failure\n'
                       '      Result: False'):
            self.assertIn(result, ret)

    def test_orchestrate_target_doesnt_exists(self):
        '''
        test orchestration when target doesn't exist
        while using multiple states
        '''
        ret = self.run_run('state.orchestrate orch.target-doesnt-exists')

        first = ['No minions matched the target. No command was sent, no jid was assigned.',
                 '          ID: core',
                 '    Function: salt.state',
                 '      Result: False']

        second = ['          ID: test-state',
                 '    Function: salt.state',
                 '      Result: True']

        third = ['          ID: cmd.run',
                 '    Function: salt.function',
                 '      Result: True']

        ret_out = [first, second, third]

        for out in ret_out:
            for item in out:
                self.assertIn(item, ret)

    def test_state_event(self):
        '''
        test to ensure state.event
        runner returns correct data
        '''
        q = queue.Queue(maxsize=0)

        cmd = 'state.event salt/job/*/new count=1'
        expect = '"minions": ["minion"]'
        server_thread = threading.Thread(target=self.add_to_queue, args=(q, cmd))
        server_thread.setDaemon(True)
        server_thread.start()

        while q.empty():
            self.run_salt('minion test.ping --static')
        out = q.get()
        self.assertIn(expect, six.text_type(out))

        server_thread.join()

    def test_orchestrate_subset(self):
        '''
        test orchestration state using subset
        '''
        ret = self.run_run('state.orchestrate orch.subset')

        def count(thing, listobj):
            return sum([obj.strip() == thing for obj in listobj])

        self.assertEqual(count('ID: test subset', ret), 1)
        self.assertEqual(count('Succeeded: 1', ret), 1)
        self.assertEqual(count('Failed:    0', ret), 1)

    def test_orchestrate_salt_function_return_false_failure(self):
        '''
        Ensure that functions that only return False in the return
        are flagged as failed when run as orchestrations.

        See https://github.com/saltstack/salt/issues/30367
        '''
        self.run_run('saltutil.sync_modules')
        ret = salt.utils.json.loads(
            '\n'.join(
                self.run_run('state.orchestrate orch.issue30367 --out=json')
            )
        )
        # Drill down to the changes dict
        state_result = ret['data']['master']['salt_|-deploy_check_|-test.false_|-function']['result']
        func_ret = ret['data']['master']['salt_|-deploy_check_|-test.false_|-function']['changes']

        self.assertEqual(
            state_result,
            False,
        )

        self.assertEqual(
            func_ret,
            {'out': 'highstate', 'ret': {'minion': False}}
        )


@skipIf(salt.utils.platform.is_windows(), '*NIX-only test')
class OrchEventTest(ShellCase):
    '''
    Tests for orchestration events
    '''
    def setUp(self):
        self.timeout = 60
        self.master_d_dir = os.path.join(self.config_dir, 'master.d')
        try:
            os.makedirs(self.master_d_dir)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise

        self.conf = tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.conf',
            dir=self.master_d_dir,
            delete=True,
        )
        self.base_env = tempfile.mkdtemp(dir=TMP)
        self.addCleanup(shutil.rmtree, self.base_env)
        self.addCleanup(self.conf.close)
        for attr in ('timeout', 'master_d_dir', 'conf', 'base_env'):
            self.addCleanup(delattr, self, attr)
        # Force a reload of the configuration now that our temp config file has
        # been removed.
        self.addCleanup(self.run_run_plus, 'test.arg', __reload_config=True)

    def alarm_handler(self, signal, frame):
        raise Exception('Timeout of {0} seconds reached'.format(self.timeout))

    def write_conf(self, data):
        '''
        Dump the config dict to the conf file
        '''
        self.conf.write(salt.utils.yaml.safe_dump(data, default_flow_style=False))
        self.conf.flush()

    def test_jid_in_ret_event(self):
        '''
        Test to confirm that the ret event for the orchestration contains the
        jid for the jobs spawned.
        '''
        self.write_conf({
            'fileserver_backend': ['roots'],
            'file_roots': {
                'base': [self.base_env],
            },
        })

        state_sls = os.path.join(self.base_env, 'test_state.sls')
        with salt.utils.files.fopen(state_sls, 'w') as fp_:
            fp_.write(salt.utils.stringutils.to_str(textwrap.dedent('''
                date:
                  cmd.run
            ''')))

        orch_sls = os.path.join(self.base_env, 'test_orch.sls')
        with salt.utils.files.fopen(orch_sls, 'w') as fp_:
            fp_.write(salt.utils.stringutils.to_str(textwrap.dedent('''
                date_cmd:
                  salt.state:
                    - tgt: minion
                    - sls: test_state

                ping_minion:
                  salt.function:
                    - name: test.ping
                    - tgt: minion

                fileserver.file_list:
                  salt.runner

                config.values:
                  salt.wheel
            ''')))

        listener = salt.utils.event.get_event(
            'master',
            sock_dir=self.master_opts['sock_dir'],
            transport=self.master_opts['transport'],
            opts=self.master_opts)

        jid = self.run_run_plus(
            'state.orchestrate',
            'test_orch',
            __reload_config=True).get('jid')

        if jid is None:
            raise Exception('jid missing from run_run_plus output')

        signal.signal(signal.SIGALRM, self.alarm_handler)
        signal.alarm(self.timeout)
        try:
            while True:
                event = listener.get_event(full=True)
                if event is None:
                    continue

                if event['tag'] == 'salt/run/{0}/ret'.format(jid):
                    # Don't wrap this in a try/except. We want to know if the
                    # data structure is different from what we expect!
                    ret = event['data']['return']['data']['master']
                    for job in ret:
                        self.assertTrue('__jid__' in ret[job])
                    break
        finally:
            del listener
            signal.alarm(0)

    def test_parallel_orchestrations(self):
        '''
        Test to confirm that the parallel state requisite works in orch
        we do this by running 10 test.sleep's of 10 seconds, and insure it only takes roughly 10s
        '''
        self.write_conf({
            'fileserver_backend': ['roots'],
            'file_roots': {
                'base': [self.base_env],
            },
        })

        orch_sls = os.path.join(self.base_env, 'test_par_orch.sls')

        with salt.utils.files.fopen(orch_sls, 'w') as fp_:
            fp_.write(textwrap.dedent('''
                {% for count in range(1, 20) %}

                sleep {{ count }}:
                    module.run:
                        - name: test.sleep
                        - length: 10
                        - parallel: True

                {% endfor %}

                sleep 21:
                    module.run:
                        - name: test.sleep
                        - length: 10
                        - parallel: True
                        - require:
                            - module: sleep 1
            '''))

        orch_sls = os.path.join(self.base_env, 'test_par_orch.sls')

        listener = salt.utils.event.get_event(
            'master',
            sock_dir=self.master_opts['sock_dir'],
            transport=self.master_opts['transport'],
            opts=self.master_opts)

        start_time = time.time()
        jid = self.run_run_plus(
            'state.orchestrate',
            'test_par_orch',
            __reload_config=True).get('jid')

        if jid is None:
            raise Exception('jid missing from run_run_plus output')

        signal.signal(signal.SIGALRM, self.alarm_handler)
        signal.alarm(self.timeout)
        received = False
        try:
            while True:
                event = listener.get_event(full=True)
                if event is None:
                    continue

                # if we receive the ret for this job before self.timeout (60),
                # the test is implicitly sucessful; if it were happening in serial it would be
                # atleast 110 seconds.
                if event['tag'] == 'salt/run/{0}/ret'.format(jid):
                    received = True
                    # Don't wrap this in a try/except. We want to know if the
                    # data structure is different from what we expect!
                    ret = event['data']['return']['data']['master']
                    for state in ret:
                        data = ret[state]
                        # we expect each duration to be greater than 10s
                        self.assertTrue(data['duration'] > 10000)
                    break

                # self confirm that the total runtime is roughly 30s (left 10s for buffer)
                self.assertTrue((time.time() - start_time) < 40)
        finally:
            self.assertTrue(received)
            del listener
            signal.alarm(0)

    @expensiveTest
    def test_orchestration_soft_kill(self):
        '''
        Test to confirm that the parallel state requisite works in orch
        we do this by running 10 test.sleep's of 10 seconds, and insure it only takes roughly 10s
        '''
        self.write_conf({
            'fileserver_backend': ['roots'],
            'file_roots': {
                'base': [self.base_env],
            },
        })

        orch_sls = os.path.join(self.base_env, 'two_stage_orch_kill.sls')

        with salt.utils.files.fopen(orch_sls, 'w') as fp_:
            fp_.write(textwrap.dedent('''
                stage_one:
                    test.succeed_without_changes

                stage_two:
                    test.fail_without_changes
            '''))

        listener = salt.utils.event.get_event(
            'master',
            sock_dir=self.master_opts['sock_dir'],
            transport=self.master_opts['transport'],
            opts=self.master_opts)

        mock_jid = '20131219120000000000'
        self.run_run('state.soft_kill {0} stage_two'.format(mock_jid))
        with patch('salt.utils.jid.gen_jid', MagicMock(return_value=mock_jid)):
            jid = self.run_run_plus(
                'state.orchestrate',
                'two_stage_orch_kill',
                __reload_config=True).get('jid')

        if jid is None:
            raise Exception('jid missing from run_run_plus output')

        signal.signal(signal.SIGALRM, self.alarm_handler)
        signal.alarm(self.timeout)
        received = False
        try:
            while True:
                event = listener.get_event(full=True)
                if event is None:
                    continue

                # Ensure that stage_two of the state does not run
                if event['tag'] == 'salt/run/{0}/ret'.format(jid):
                    received = True
                    # Don't wrap this in a try/except. We want to know if the
                    # data structure is different from what we expect!
                    ret = event['data']['return']['data']['master']
                    self.assertNotIn('test_|-stage_two_|-stage_two_|-fail_without_changes', ret)
                    break

        finally:
            self.assertTrue(received)
            del listener
            signal.alarm(0)
