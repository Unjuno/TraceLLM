"""Read-only learning observations. Never load weights or infer a health/ability score."""
import json
import math
from pathlib import Path
import re
import time

DAY = 86400
LIMIT = 200
LINEAGE = """WITH RECURSIVE lineage(id) AS (
 SELECT value FROM meta WHERE key='current'
 UNION
 SELECT c.parent_id FROM checkpoints c JOIN lineage l ON c.id=l.id
 WHERE c.parent_id IS NOT NULL
) """


def finite(value):
    """Legacy/malformed data are missing, not zero. Booleans are not observations."""
    return value if type(value) in (int, float) and math.isfinite(value) else None


def count(value):
    return value if type(value) is int and value >= 0 else None


def metrics_of(row):
    if not row:
        return {}
    try:
        result = json.loads(row['metrics'])
    except (ValueError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}


def retention_of(job, metrics):
    r = metrics.get('retention')
    r = r if isinstance(r, dict) else {}
    before, after = finite(r.get('loss_before')), finite(r.get('loss_after'))
    measured = r.get('status') == 'measured' and before is not None and after is not None
    page_ids = r.get('page_ids')
    sample_pages = len(page_ids) if isinstance(page_ids, list) else 0
    measured = measured and sample_pages > 0
    return {
        'status': 'measured' if measured else r.get('status') if r.get('status') in
                  ('invalidated_by_deletion', 'unavailable') else 'unavailable',
        'loss_before': before if measured else None, 'loss_after': after if measured else None,
        'delta': after-before if measured else None, 'sample_pages': sample_pages,
        'targets': count(r.get('targets')), 'job_id': job['id'] if job else None,
        'observed_at': finite(job['finished']) if job else None,
        'scope': 'same-sampled-trained-text-before-and-after; not held-out',
    }


def observation_of(job):
    m = metrics_of(job)
    first = (m.get('evaluation_scope') == 'first_seen_page_exact_hash_only' and
             finite(m.get('prequential_loss')) is not None)
    return {
        'job_id': job['id'], 'page_id': job['page_id'], 'revision': job['revision'],
        'checkpoint_id': job['after_id'], 'before_checkpoint_id': job['before_id'],
        'observed_at': finite(job['started']), 'published_at': finite(job['finished']),
        'evaluation_scope': m.get('evaluation_scope') if m.get('evaluation_scope') in
            ('first_seen_page_exact_hash_only', 'edited_page', 'repeated_body') else 'legacy_unknown',
        'loss_before': finite(m.get('loss_before')), 'loss_after': finite(m.get('loss_after')),
        'prequential_loss': finite(m.get('prequential_loss')) if first else None,
        'new_targets': count(m.get('new_targets')), 'replay_targets': count(m.get('replay_targets')),
        'requested_new_targets': count(m.get('requested_new_targets')),
        'capped': m.get('capped') is True,
        'stop_reason': m.get('stop_reason') if m.get('stop_reason') in
            ('step_safety_cap', 'time_budget', 'requested_targets_completed') else 'unknown',
        'retention': retention_of(job, m),
    }


def summarize_health(store, now=None):
    """Take one consistent SQLite snapshot. No writes, model lock, or training side effects.

    UNION (not UNION ALL) terminates even if damaged lineage contains a cycle.
    Operations are global; published learning observations follow parent_id only.
    Expired checkpoint metadata remain useful; availability does not erase history.
    """
    now = time.time() if now is None else now
    cutoff = now - 7*DAY
    with store.connect() as db:
        current_row = db.execute("SELECT value FROM meta WHERE key='current'").fetchone()
        current = current_row['value'] if current_row else None
        ancestors = [dict(r) for r in db.execute(LINEAGE +
            'SELECT c.* FROM checkpoints c JOIN lineage l ON c.id=l.id')]
        accepted = " FROM jobs j JOIN versions v ON j.version_id=v.id WHERE j.status='succeeded' " \
                   'AND j.after_id IN (SELECT id FROM lineage) '
        rows = [dict(r) for r in db.execute(LINEAGE +
            'SELECT j.*,v.page_id,v.revision' + accepted +
            'AND j.finished>=? ORDER BY j.finished DESC,j.id DESC LIMIT ?', (cutoff, LIMIT))]
        total = db.execute(LINEAGE + 'SELECT COUNT(*)' + accepted +
                           'AND j.finished>=?', (cutoff,)).fetchone()[0]
        latest_row = db.execute(LINEAGE + 'SELECT j.*,v.page_id,v.revision' + accepted +
                               'ORDER BY j.finished DESC,j.id DESC LIMIT 1').fetchone()
        latest = dict(latest_row) if latest_row else None
        recent = {r['status']: r['n'] for r in db.execute(
            'SELECT status,COUNT(*) n FROM jobs WHERE COALESCE(finished,created)>=? '
            'GROUP BY status', (cutoff,))}
        active = {r['status']: r['n'] for r in db.execute(
            "SELECT status,COUNT(*) n FROM jobs WHERE status IN ('queued','running') GROUP BY status")}
        recovery_count = db.execute('SELECT COUNT(*) FROM checkpoints WHERE available=1 '
                                   'AND expires>? AND id<>?', (now, current)).fetchone()[0]
    by_id = {c['id']: c for c in ancestors}
    visited, pointer, lineage_complete = set(), current, False
    while pointer in by_id and pointer not in visited:
        visited.add(pointer)
        parent = by_id[pointer]['parent_id']
        if parent is None:
            lineage_complete = True
            break
        pointer = parent
    current_cp = by_id.get(current, {})
    valid_id = isinstance(current, str) and re.fullmatch(r'[0-9a-f]{32}', current)
    present = bool(valid_id and (Path(store.path).parent/'checkpoints'/f'{current}.pt').is_file())
    preserved = bool(current_cp.get('available') and present)
    # Current checkpoint is exempt from retention pruning. Existence is not a hash verification.
    execution_state = ('attention' if not preserved or not lineage_complete else
                       'running' if active.get('running') else 'queued' if active.get('queued') else
                       'attention' if recent.get('failed') else 'ready' if latest else 'not_started')
    observations = [observation_of(j) for j in reversed(rows)]
    last_metrics = metrics_of(latest)
    exposure = last_metrics.get('exposure_totals')
    if isinstance(exposure, dict):
        exposure = {'new_targets': count(exposure.get('new_targets')),
                    'replay_targets': count(exposure.get('replay_targets')),
                    'complete': exposure.get('complete') is True}
        exposure['complete'] &= (exposure['new_targets'] is not None and
                                 exposure['replay_targets'] is not None)
    elif not latest and lineage_complete and not any(c['kind'] == 'train' for c in ancestors):
        exposure = {'new_targets': 0, 'replay_targets': 0, 'complete': True}
    else:
        exposure = {'new_targets': None, 'replay_targets': None, 'complete': False}
    timing = None
    if finite(last_metrics.get('elapsed_seconds')) is not None:
        timing = {'elapsed_seconds': finite(last_metrics.get('elapsed_seconds')),
                  'process_cpu_seconds': finite(last_metrics.get('process_cpu_seconds')),
                  'cpu_threads': count(last_metrics.get('cpu_threads')),
                  'scope': 'load-evaluate-train; excludes checkpoint publication',
                  'observed_at': finite(latest['finished'])}
    return {
        'schema_version': 1, 'as_of': now, 'checkpoint_id': current,
        'checkpoint_kind': current_cp.get('kind'),
        'scope': {'branch': 'current_ancestry', 'days': 7, 'since': cutoff, 'limit': LIMIT,
                  'total_observations': total, 'truncated': total > LIMIT,
                  'lineage_complete': lineage_complete},
        'execution': {'state': execution_state, 'recent_counts': recent, 'active_counts': active,
                      'scope': 'all-operations-last-7-days; active-queue-any-age'},
        'generalization': {'status': 'unavailable',
                           'reason': 'No independent held-out validation set is configured.'},
        'first_seen': [o for o in observations if o['prequential_loss'] is not None],
        'retention': retention_of(latest, last_metrics), 'observations': observations,
        'exposure': exposure, 'latest_timing': timing,
        'recovery': {'days': 7, 'current_preserved': preserved,
                     'available_previous': recovery_count, 'verification': 'metadata-and-current-file-existence'},
        'loss_unit': 'nats/target-token (UTF-8 bytes and EOS)',
        'warnings': ['First-seen uses exact hash and page identity, not proof of independence.',
                     'Different pages have different difficulty; no growth trend is inferred.'],
    }
