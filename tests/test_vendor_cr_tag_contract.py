"""Offline contract tests; all resource and operation identities are synthetic."""
from __future__ import annotations

import importlib
import json
import multiprocessing
from pathlib import Path

import pytest


def api():
    return importlib.import_module('scripts.vendor.cloud_run_tags')


def allocate(tmp_path, **overrides):
    options = dict(
        service_name='example-service', preferred_tag='cand-abcdef01',
        existing_tags={}, operation_id='fake-run-one', candidate_id='fake-revision-one',
        project='<PROJECT_ID>', region='example-region', state_path=tmp_path / 'tags.json',
    )
    options.update(overrides)
    return api().allocate_tag(**options)


def test_preferred_tag_and_exact_headroom(tmp_path):
    result = allocate(tmp_path)
    assert isinstance(result, api().AllocatedTag)
    assert result.tag == 'cand-abcdef01'
    assert api().headroom('example-service') == 31
    assert api().headroom('x' * 33) == 13


@pytest.mark.parametrize('room', range(1, 13))
def test_shorten_every_tight_budget_and_recover_after_restart(tmp_path, room):
    result = allocate(tmp_path, service_name='x' * (46 - room))
    assert isinstance(result, api().AllocatedTag)
    assert 1 <= len(result.tag) <= room
    assert result.tag[0].isalpha() and result.tag.isalnum()
    assert allocate(tmp_path, service_name='x' * (46 - room)) == result
    assert json.loads((tmp_path / 'tags.json').read_text())['contract_version'] == api().CONTRACT_VERSION


@pytest.mark.parametrize('length', [46, 47, 48, 49, 63])
def test_impossible_never_returns_silent_success(tmp_path, length):
    result = allocate(tmp_path, service_name='x' * length)
    assert result == api().Refusal('TAG_IMPOSSIBLE')
    assert not (tmp_path / 'tags.json').exists()


@pytest.mark.parametrize('occupant', ['foreign-revision', None])
def test_foreign_or_unknown_preferred_tag_refused(tmp_path, occupant):
    assert allocate(tmp_path, existing_tags={'cand-abcdef01': occupant}) == api().Refusal('TAG_OCCUPIED')


def test_cloud_revision_match_without_local_ownership_is_not_owned(tmp_path):
    assert allocate(tmp_path, existing_tags={'cand-abcdef01': 'fake-revision-one'}) == api().Refusal('TAG_OCCUPIED')


def test_owned_tag_and_zero_percent_tags_are_preserved(tmp_path):
    allocated = allocate(tmp_path)
    # Tag inventories include ALL entries, including zero-percent tags.
    tags = {allocated.tag: 'fake-revision-one', 'zero-percent': 'fake-previous'}
    before = dict(tags)
    assert allocate(tmp_path, existing_tags=tags) == allocated
    assert tags == before
    assert allocate(tmp_path, existing_tags={allocated.tag: 'foreign-revision'}) == api().Refusal('TAG_OCCUPIED')


def test_hash_collision_refused_without_searching_another_tag(tmp_path):
    first = allocate(tmp_path, service_name='x' * 45)
    assert allocate(tmp_path, service_name='x' * 45, existing_tags={first.tag: None}) == api().Refusal('TAG_OCCUPIED')


def test_operation_identity_cannot_be_rebound_after_restart(tmp_path):
    allocate(tmp_path)
    assert allocate(tmp_path, candidate_id='fake-revision-two') == api().Refusal('OPERATION_REBOUND')
    assert allocate(tmp_path, preferred_tag='other') == api().Refusal('OPERATION_REBOUND')


def test_latest_disk_reservation_wins_over_free_cloud_snapshot(tmp_path):
    allocate(tmp_path)
    assert allocate(tmp_path, operation_id='fake-run-two') == api().Refusal('TAG_RESERVED')


def test_missing_or_corrupt_inventory_and_journal_fail_closed(tmp_path):
    assert allocate(tmp_path, existing_tags=None) == api().Refusal('INVALID_INPUT')
    path = tmp_path / 'tags.json'
    path.write_text('{incomplete')
    assert allocate(tmp_path) == api().Refusal('STATE_UNAVAILABLE')
    path.write_text('{}')
    assert allocate(tmp_path) == api().Refusal('STATE_UNAVAILABLE')


def test_truncated_existing_journal_is_not_reinitialized(tmp_path):
    allocate(tmp_path)
    (tmp_path / 'tags.json').write_text('')
    assert allocate(tmp_path) == api().Refusal('STATE_UNAVAILABLE')


def test_state_does_not_disclose_resource_or_candidate_identity(tmp_path):
    result = allocate(tmp_path, service_name='fake-sensitive-service', candidate_id='fake-sensitive-candidate', operation_id='fake-sensitive-run')
    data = (tmp_path / 'tags.json').read_text()
    for value in ['fake-sensitive-service', 'fake-sensitive-candidate', 'fake-sensitive-run', '<PROJECT_ID>', 'example-region']:
        assert value not in data
        assert value not in repr(result)
    target = api().Target('<PROJECT_ID>', 'example-region', 'fake-sensitive-service')
    assert 'fake-sensitive-service' not in repr(target)
    assert 'fake-sensitive-run' not in repr(api().WarmupApproval(target, 'warmup', 'fake-sensitive-run', True))


def _allocate_process(path, run, queue):
    result = allocate(Path(path), operation_id=run)
    queue.put(type(result).__name__)


def test_two_processes_cannot_reserve_same_free_tag(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    workers = [ctx.Process(target=_allocate_process, args=(str(tmp_path), f'fake-run-{n}', queue)) for n in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    assert sorted(queue.get(timeout=2) for _ in workers) == ['AllocatedTag', 'Refusal']


def test_symlink_state_is_refused(tmp_path):
    other = tmp_path / 'other.json'
    other.write_text('')
    (tmp_path / 'tags.json').symlink_to(other)
    assert allocate(tmp_path) == api().Refusal('STATE_UNAVAILABLE')
    assert other.read_text() == ''


@pytest.mark.parametrize('changes', [
    {'service_name': ' uppercase'}, {'preferred_tag': ''}, {'preferred_tag': '9bad'},
    {'operation_id': ''}, {'candidate_id': ''}, {'region': ''}, {'project': ''},
    {'existing_tags': {'bad tag': None}}, {'existing_tags': {'valid': 7}},
])
def test_invalid_inputs_refused(tmp_path, changes):
    assert allocate(tmp_path, **changes) == api().Refusal('INVALID_INPUT')


def lifecycle(**overrides):
    module = api()
    target = module.Target('<PROJECT_ID>', 'example-region', 'x' * 48)
    options = dict(target=target, action='warmup', operation_id='fake-run', no_traffic=False)
    options.update(overrides)
    return module.lifecycle_for_impossible_tag(**options)


def approval(**overrides):
    module = api()
    options = dict(target=module.Target('<PROJECT_ID>', 'example-region', 'x' * 48), action='warmup', operation_id='fake-run', warmup_traffic_approved=True)
    options.update(overrides)
    return module.WarmupApproval(**options)


def test_impossible_lifecycle_stops_by_default():
    assert lifecycle().kind == 'STOP'
    assert lifecycle().candidate_percent == 0


def test_only_exact_explicit_approval_selects_micro_split():
    result = lifecycle(approval=approval())
    assert result.kind == 'MICRO_SPLIT'
    assert (result.previous_percent, result.candidate_percent) == (99, 1)


@pytest.mark.parametrize('changes', [
    {'target': lambda m: m.Target('<PROJECT_ID>', 'example-region', 'different-service')},
    {'target': lambda m: m.Target('<PROJECT_ID>', 'different-region', 'x' * 48)},
    {'target': lambda m: m.Target('fake-other-project', 'example-region', 'x' * 48)},
    {'action': lambda m: 'promotion'}, {'operation_id': lambda m: 'another-run'},
    {'warmup_traffic_approved': lambda m: False},
    {'warmup_traffic_approved': lambda m: 'true'},
])
def test_approval_does_not_widen(changes):
    assert lifecycle(approval=approval(**{key: value(api()) for key, value in changes.items()})).kind == 'STOP'


@pytest.mark.parametrize('changes', [{'no_traffic': True}, {'image_only': True}, {'no_traffic': None}, {'image_only': None}])
def test_no_traffic_and_image_only_never_select_micro_split(changes):
    result = lifecycle(approval=approval(), **changes)
    assert result.kind == 'STOP'
    assert result.candidate_percent == 0


def test_fitting_service_and_other_action_cannot_use_fallback():
    target = api().Target('<PROJECT_ID>', 'example-region', 'short-service')
    assert lifecycle(target=target, approval=approval(target=target)).kind == 'STOP'
    assert lifecycle(action='promotion', approval=approval(action='promotion')).kind == 'STOP'
