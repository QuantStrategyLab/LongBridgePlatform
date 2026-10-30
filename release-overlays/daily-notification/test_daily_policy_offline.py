"""Offline checks for daily routing and durable per-run evidence."""
import copy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from notifications import unified

class Memory:
    def __init__(self):
        self.data = {}
    def read(self, key):
        return copy.deepcopy(self.data.get(key, (None, None)))
    def compare_and_set(self, key, version, value):
        if self.read(key)[0] != version:
            return False
        self.data[key] = ((version or 0) + 1, copy.deepcopy(value))
        return True

def setup():
    target = {'platform_id':'longbridge','strategy_profile':'synthetic','service_name':'svc','account_scope':'test','account_selector':['test'],'deployment_selector':'test','dry_run_only':False}
    env = {'RUNTIME_TARGET_ENABLED':'true','RUNTIME_TARGET_JSON':json.dumps(target),'DIGEST_TARGET_ID':'target','DIGEST_OPAQUE_ACCOUNT_UID':'account','DIGEST_STORE_TARGET':'slot'}
    report={'schema_version':'runtime_report.v1','platform':'longbridge','strategy_profile':'synthetic','service_name':'svc','runtime_target':target,'dry_run':False,'status':'ok','run_id':'run','started_at':'2026-10-15T20:00:00Z','summary':{'routine_observation_counts':{'watch':1}}}
    return env, report

class DailyPolicy(unittest.TestCase):
    def test_coverage_locale_priority_and_normalization(self):
        cases = [({'QSL_NOTIFY_LANG':' zh-CN ', 'NOTIFY_LANG':'en'}, 'zh'),
                 ({'QSL_NOTIFY_LANG':' EN-us ', 'NOTIFY_LANG':'zh'}, 'en'),
                 ({'QSL_NOTIFY_LANG':'  ', 'NOTIFY_LANG':' EN-us '}, 'en'),
                 ({}, 'zh')]
        for env, expected in cases:
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                self.assertEqual(unified.coverage_locale(), expected)
                sent=[]
                unified._storage_failure(lambda title, detail: sent.append((title, detail)))
                self.assertEqual('Notification evidence unavailable' in sent[0][0], expected == 'en')

    def test_archive_confirmation_rejects_local_and_unbound_paths(self):
        prefix='gs://synthetic-reports/archive'
        cloud=prefix+'/longbridge/run.json'
        report={'artifacts':{'runtime_report_cloud_uri':cloud}}
        self.assertTrue(unified.report_archive_confirmed(report,cloud,prefix))
        self.assertFalse(unified.report_archive_confirmed(report,'/tmp/run.json',prefix))
        self.assertFalse(unified.report_archive_confirmed(report,cloud,None))
        self.assertFalse(unified.report_archive_confirmed({},cloud,prefix))
        self.assertFalse(unified.report_archive_confirmed(report,cloud,'gs://other/archive'))

    def test_candidate_is_per_run_bound_and_conflicting_counts_fail(self):
        env, report = setup()
        store = Memory()
        with unified.bind_report(report):
            event = unified.make_event('routine_cycle', env=env).to_candidate()
            self.assertTrue(unified.persist_candidate(event, env=env, store=store))
            report['summary']['routine_observation_counts']={'watch':2}
            with self.assertRaisesRegex(ValueError, 'candidate_plugin_observations_conflict'):
                unified.persist_candidate(event, env=env, store=store)
        published = next(v for k,(_,v) in store.data.items() if not k.startswith('candidate_state/'))
        self.assertEqual(published['runs'][0]['source_run_id'],'run')
        self.assertEqual(published['runs'][0]['routine_observation_counts'],{'watch':1})

    def test_flags_and_storage_failure_never_send_normal_order(self):
        env, report=setup()
        result=SimpleNamespace(allocation={},execution={'execution_status':'completed'},action_done=True,pending_orders=())
        config=SimpleNamespace(dry_run_only=False,notification_attention_reason_codes=())
        publisher=SimpleNamespace(log_message=lambda *_:None,send_message=lambda *_:self.fail('normal send'))
        for flag in ('false','true'):
            with self.subTest(flag=flag), patch.dict(os.environ,{**env,'LONGBRIDGE_UNIFIED_NOTIFICATION_ENABLED':flag},clear=True), unified.bind_report(report):
                with patch.object(unified,'persist_candidate',return_value=True):
                    suppressed,_=unified.prepare_cycle(result,config=config,publisher=publisher,notify_issue=lambda *_:None)
                    self.assertTrue(suppressed)
                notices=[]
                with patch.object(unified,'persist_candidate',side_effect=OSError('private')), patch.object(unified,'send_incident',side_effect=OSError('private')):
                    suppressed, wrapped=unified.prepare_cycle(result,config=config,publisher=publisher,notify_issue=lambda *a:notices.append(a))
                    self.assertFalse(suppressed)
                    self.assertFalse(wrapped.send_message('normal'))
                    self.assertEqual(len(notices),1)
                    self.assertNotIn('private',str(notices))

    def test_pending_unknown_and_error_not_routine(self):
        for execution,pending in [({'execution_status':'completed'},({'status':'pending_reconciliation'},)),({'execution_status':'completed','risk_flags':['halt']},()),({'execution_status':'completed','persistence_error':'failure'},())]:
            result=SimpleNamespace(allocation={},execution=execution,action_done=True,pending_orders=pending)
            self.assertNotIn(unified.cycle_facts(result)[0],{'routine_cycle','order_submitted'})

    def test_existing_execution_attention_classifier_controls_routing(self):
        states = [
            {'heartbeat_execution_state':'blocked'},
            {'no_execute':True},
            {'heartbeat_execution_state':'waiting_window'},
            {'heartbeat_execution_state':'waiting_window', 'durable_live_execution_command':{'status':'UNKNOWN'}},
            {'heartbeat_execution_state':'waiting_window', 'durable_live_execution_command':{'status':'PENDING_RECONCILIATION'}},
        ]
        for execution in states:
            result=SimpleNamespace(allocation={}, execution=execution, action_done=False, pending_orders=())
            self.assertEqual(unified.cycle_facts(result)[0], 'real_error')
        queued=SimpleNamespace(allocation={}, execution={'heartbeat_execution_state':'waiting_window',
            'direct_live_routing_blocked':True, 'durable_live_execution_command':{'status':'QUEUED'}},
            action_done=False, pending_orders=())
        self.assertEqual(unified.cycle_facts(queued)[0], 'routine_cycle')

    def test_partial_summary_result_without_allocation_is_supported(self):
        result = SimpleNamespace(execution={}, action_done=True)
        kind, facts = unified.cycle_facts(result, dry_run=True)
        self.assertEqual(kind, 'routine_cycle')
        self.assertTrue(facts['dry_run'])

    def test_dry_run_never_sends_coverage_for_normal_preview(self):
        result=SimpleNamespace(allocation={}, execution={'execution_status':'no_action'}, action_done=False, pending_orders=())
        config=SimpleNamespace(dry_run_only=True, notification_attention_reason_codes=())
        publisher=SimpleNamespace(log_message=lambda *_:None, send_message=lambda *_:self.fail('unexpected send'))
        with patch.object(unified,'persist_candidate',side_effect=AssertionError('unexpected write')):
            suppressed, _=unified.prepare_cycle(result,config=config,publisher=publisher,notify_issue=lambda *_:self.fail('unexpected issue'))
        self.assertTrue(suppressed)

    def test_archive_reuses_source_run_and_never_invents_fills(self):
        env, report = setup()
        store = Memory()
        with unified.bind_report(report):
            fact = unified.make_event('routine_cycle', env=env).to_candidate()
            self.assertTrue(unified.persist_candidate(fact, env=env, store=store))
        report['summary']['notification_digest']={'schema_version':'qsl.notification_cycle.v1',
            'order_count':None,'fill_count':None,'routine':False,'dry_run':False,'event':fact}
        self.assertTrue(unified.persist_archived_report(report, env=env, store=store))
        published = next(v for k,(_,v) in store.data.items() if not k.startswith('candidate_state/'))
        self.assertEqual(len(published['runs']),1)
        self.assertIsNone(published['runs'][0]['order_count'])
        self.assertIsNone(published['runs'][0]['fill_count'])
        self.assertEqual(published['runs'][0]['routine_observation_counts'],{'watch':1})

    def test_final_report_cycle_facts_match_staged_run_for_idle_and_action(self):
        for action in (False, True):
            env, report = setup()
            store = Memory()
            result=SimpleNamespace(allocation={}, execution={'execution_status':'completed' if action else 'no_action'},
                action_done=action, pending_orders=(), dry_run_orders=())
            config=SimpleNamespace(dry_run_only=False, notification_attention_reason_codes=())
            _, facts=unified.cycle_facts(result)
            with patch.dict(os.environ, env, clear=True), unified.bind_report(report):
                event=unified.make_event('routine_cycle',env=env,order_count=facts['order_count'],fill_count=facts['fill_count']).to_candidate()
                self.assertTrue(unified.persist_candidate(event,env=env,store=store))
                unified.attach_cycle_event(report['summary'],result,report=report,config=config)
            self.assertTrue(unified.persist_archived_report(report,env=env,store=store))
            published=next(v for k,(_,v) in store.data.items() if not k.startswith('candidate_state/'))
            row=published['runs'][0]
            self.assertEqual((len(published['runs']),row['source_run_id'],row['routine_observation_counts']),(1,'run',{'watch':1}))
            self.assertEqual((row['order_count'],row['fill_count']),(None,None) if action else (0,0))

    def test_market_closed_archive_exports_no_business_event(self):
        env, report=setup()
        store=Memory()
        report.update(status='skipped', diagnostics={'skip_reason':'market_closed'}, summary={})
        self.assertTrue(unified.persist_archived_report(report,env=env,store=store))
        published=next(v for k,(_,v) in store.data.items() if not k.startswith('candidate_state/'))
        row=published['runs'][0]
        self.assertIs(row['actually_ran'],False)
        self.assertEqual(row['notification_events'],[])
        self.assertEqual(row['cycle_count'],0)
        self.assertIsNone(row['order_count'])
        self.assertIsNone(row['fill_count'])

    def test_flag_off_urgent_keeps_existing_publisher(self):
        result=SimpleNamespace(allocation={},execution={'execution_status':'blocked'},action_done=False,pending_orders=())
        config=SimpleNamespace(dry_run_only=False,notification_attention_reason_codes=())
        publisher=SimpleNamespace(log_message=lambda *_:None,send_message=lambda *_:True)
        with patch.dict(os.environ,{},clear=True):
            suppressed, actual=unified.prepare_cycle(result,config=config,publisher=publisher,notify_issue=lambda *_:None)
        self.assertFalse(suppressed)
        self.assertIs(actual,publisher)

    def test_validation_keeps_silent_caller_without_candidate_writes(self):
        publisher=SimpleNamespace(log_message=lambda *_:None,send_message=lambda *_:False)
        with unified.bind_report({}, validation_only=True), patch.object(unified,'persist_candidate',side_effect=AssertionError('unexpected write')):
            suppressed, actual=unified.prepare_cycle(None,config=None,publisher=publisher,notify_issue=None)
        self.assertFalse(suppressed)
        self.assertIs(actual,publisher)

if __name__ == '__main__':
    unittest.main()
