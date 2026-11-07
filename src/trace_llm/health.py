"""Read-only learning observations, not a model-quality score.

Only database metadata is read: no checkpoint loads, training lock, note bodies,
network calls or writes. Missing measurements remain null. Each request uses one
SQLite snapshot; chart rows are bounded while selected-scope totals are streamed.
"""
from collections import deque
from dataclasses import asdict
import json
import math
import time

import torch


SCOPES = {'first_seen_page_exact_hash_only', 'repeated_body', 'edited_page'}
LOSS_UNIT = 'nats/target-token (bytes and EOS)'


def number(value, *, count=False):
    """Accept only finite, nonnegative JSON numbers (not booleans/strings)."""
    if type(value) not in (int, float):
        return None
    try:
        numeric = float(value)
    except OverflowError:
        return None
    if not math.isfinite(numeric) or numeric < 0:
        return None
    if count:
        # Browser JSON numbers cannot exactly represent larger integers.
        return int(value) if numeric <= 9007199254740991 and value == int(value) else None
    return numeric


def observation(job):
    try:
        metrics = json.loads(job['metrics'])
        valid = isinstance(metrics, dict)
    except (TypeError, ValueError):
        valid = False
        metrics = {}
    if not valid:
        metrics = {}
    scope = metrics.get('evaluation_scope')
    scope = scope if isinstance(scope, str) and scope in SCOPES else 'legacy_unknown'
    retention = metrics.get('retention')
    retention = retention if isinstance(retention, dict) else {}
    before = number(retention.get('loss_before'))
    after = number(retention.get('loss_after'))
    targets = number(retention.get('targets'), count=True)
    measured = (retention.get('status') == 'measured' and before is not None and
                after is not None and targets is not None and targets > 0)
    loss_before, loss_after = number(metrics.get('loss_before')), number(metrics.get('loss_after'))
    new, replay = number(metrics.get('new_targets'), count=True), number(metrics.get('replay_targets'), count=True)
    elapsed = number(metrics.get('elapsed_seconds'))
    row = {
        'job_id': job['id'], 'page_id': job['page_id'], 'revision': job['revision'],
        'created': job['created'], 'status': job['status'], 'repeats': job['repeats'],
        'before_id': job['before_id'], 'after_id': job['after_id'],
        'scope': scope, 'metrics_valid': valid,
        'loss_before': loss_before, 'loss_after': loss_after,
        'prequential_loss': number(metrics.get('prequential_loss')) if scope.startswith('first_seen') else None,
        'retention_before': before if measured else None,
        'retention_after': after if measured else None,
        'retention_delta': after-before if measured else None,
        'retention_targets': targets if measured else None,
        'retention_pages': len(retention.get('page_ids', [])) if measured and isinstance(retention.get('page_ids'), list) else None,
        'retention_status': 'measured' if measured else 'invalidated_by_deletion' if retention.get('status') == 'invalidated_by_deletion' else 'unavailable',
        'new_targets': new, 'replay_targets': replay,
        'requested_new_targets': number(metrics.get('requested_new_targets'), count=True),
        'first_seen_body_bytes': number(metrics.get('first_seen_body_bytes'), count=True),
        'updates': number(metrics.get('updates'), count=True),
        'elapsed_seconds': elapsed, 'process_cpu_seconds': number(metrics.get('process_cpu_seconds')),
        'cpu_threads': number(metrics.get('cpu_threads'), count=True),
        'learning_rate': number(metrics.get('learning_rate')),
        'partial': metrics.get('capped') is True,
        'stop_reason': metrics.get('stop_reason') if isinstance(metrics.get('stop_reason'), str) else None,
        'evaluation_protocol': metrics['evaluation_protocol'] if isinstance(metrics.get('evaluation_protocol'), str) else 'legacy-unspecified',
        'timing_scope': metrics['timing_scope'] if isinstance(metrics.get('timing_scope'), str) else 'legacy-unspecified',
    }
    row['metrics_complete'] = (valid and loss_before is not None and loss_after is not None
                               and new is not None and replay is not None and elapsed is not None
                               and scope != 'legacy_unknown')
    row['replay_fraction'] = replay/(new+replay) if new is not None and replay is not None and new+replay > 0 else None
    return row


def health_snapshot(engine, *, scope='current', days=7, limit=120, now=None):
    if scope not in {'current', 'all'} or days not in (0, 7, 30) or not 1 <= limit <= 500:
        raise ValueError('Invalid health view selection')
    now = time.time() if now is None else now
    since = now-days*86400 if days else 0
    rows = deque(maxlen=limit)
    summary = dict.fromkeys(('total', 'succeeded', 'failed', 'cancelled', 'queued', 'running',
                             'partial', 'incomplete_metrics', 'first_seen', 'repeated', 'edited',
                             'legacy', 'retention_measured', 'retention_increased', 'new_targets',
                             'replay_targets', 'first_seen_body_bytes'), 0)
    summary.update(exposure_complete=True, body_bytes_complete=True)
    # One consistent DB transaction, without taking Engine._lock. A model may be
    # training while this view reads its last atomically published measurements.
    with engine.store.connect() as db:
        current = db.execute("SELECT value FROM meta WHERE key='current'").fetchone()['value']
        lineage_rows = list(db.execute('''
            WITH RECURSIVE lineage(id) AS (
              SELECT ? UNION SELECT c.parent_id FROM checkpoints c
              JOIN lineage l ON c.id=l.id WHERE c.parent_id IS NOT NULL
            ) SELECT l.id,c.kind FROM lineage l LEFT JOIN checkpoints c ON c.id=l.id''', (current,)))
        lineage = {r['id'] for r in lineage_rows}
        has_trained_lineage = any(r['kind'] == 'train' for r in lineage_rows)
        global_counts = {r['status']: r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM jobs GROUP BY status')}
        for job in db.execute('''SELECT j.id,j.metrics,j.status,j.created,j.repeats,
                    j.before_id,j.after_id,v.page_id,v.revision
                    FROM jobs j JOIN versions v ON v.id=j.version_id
                    WHERE j.created>=? AND j.created<=? ORDER BY j.created,j.id''', (since, now)):
            # Unbound queue/cancelled jobs are shared; completed jobs belong only to
            # the checkpoint ancestry. Restore.previous_id is deliberately ignored.
            if scope == 'current':
                if job['status'] == 'succeeded':
                    if job['after_id'] not in lineage:
                        continue
                elif job['before_id'] is not None and job['before_id'] not in lineage:
                    continue
            row = observation(job)
            summary['total'] += 1
            if row['status'] in global_counts and row['status'] in summary:
                summary[row['status']] += 1
            rows.append(row)
            if row['status'] != 'succeeded':
                continue
            summary['partial'] += int(row['partial'])
            summary['incomplete_metrics'] += int(not row['metrics_complete'])
            name = {'first_seen_page_exact_hash_only': 'first_seen', 'repeated_body': 'repeated',
                    'edited_page': 'edited', 'legacy_unknown': 'legacy'}[row['scope']]
            summary[name] += 1
            if row['retention_delta'] is not None:
                summary['retention_measured'] += 1
                summary['retention_increased'] += int(row['retention_delta'] > 0)
            for field in ('new_targets', 'replay_targets'):
                if row[field] is None:
                    summary['exposure_complete'] = False
                else:
                    summary[field] += row[field]
            if row['first_seen_body_bytes'] is None:
                summary['body_bytes_complete'] = False
            else:
                summary['first_seen_body_bytes'] += row['first_seen_body_bytes']
        cp_count = db.execute('''SELECT COUNT(*) n FROM checkpoints
                               WHERE available=1 AND (expires>? OR id=?)''', (now, current)).fetchone()['n']
        # Include the current checkpoint even when it is older than the option limit.
        options = [dict(r) for r in db.execute('''SELECT id,parent_id,kind,created,expires,available
                     FROM checkpoints ORDER BY (id=?) DESC,created DESC LIMIT 200''', (current,))]
    for cp in options:
        cp['current'] = cp['id'] == current
        cp['restorable'] = bool(cp.pop('available') and (cp['current'] or cp['expires'] > now))
    rows = list(rows)
    published = [r for r in rows if r['status'] == 'succeeded']
    queue = global_counts.get('queued', 0)
    running = global_counts.get('running', 0)
    state = ('attention' if summary['failed'] else 'incomplete_metrics' if summary['incomplete_metrics']
             else 'running' if running else 'queued' if queue else 'published' if summary['succeeded']
             else 'idle' if has_trained_lineage else 'not_started')
    return {
        'schema': 1, 'as_of': now, 'current_checkpoint': current,
        'selection': {'scope': scope, 'days': days, 'since': since, 'limit': limit,
                      'total': summary['total'], 'returned': len(rows),
                      'truncated': summary['total'] > len(rows)},
        'summary': summary,
        'operation': {'state': state, 'queued': queue, 'running': running,
                      'has_trained_lineage': has_trained_lineage,
                      'note': 'Execution status only; not evidence of generalization or file-integrity audit.'},
        'quality': {'independent_validation': 'unavailable',
                    'warning': 'First-seen uses exact hashes and page identity; near-duplicates and page difficulty are uncontrolled.'},
        'runtime': {'device': 'cpu', 'precision': 'float32', 'threads': torch.get_num_threads(),
                    'config': asdict(engine.config), 'training': asdict(engine.training)},
        'checkpoints': {'restorable': cp_count, 'retention_days': 7,
                        'note': 'Registry candidates; integrity is checked when loaded.'},
        'checkpoint_options': options,
        'series': {'first_seen': [r for r in published if r['prequential_loss'] is not None],
                   'retention': [r for r in published if r['retention_delta'] is not None]},
        'rows': rows,
        'units': {'loss': LOSS_UNIT, 'targets': 'target-tokens including EOS; padding excluded',
                  'elapsed_seconds': 'seconds, load/evaluate/train; checkpoint publication excluded',
                  'process_cpu_seconds': 'process CPU seconds, not CPU utilization percent'},
    }
