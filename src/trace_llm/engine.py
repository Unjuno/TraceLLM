"""Single-writer training engine with immutable snapshots and branch-aware replay."""
from dataclasses import asdict
import hashlib
from functools import wraps
import json
import math
import os
from pathlib import Path
import random
import threading
import time
from uuid import uuid4

from filelock import FileLock
import torch

from .data import BOS, EOS, decode, encode, windows, replay_window
from .model import Config, GPT, optimizer
from .store import Store
from .evaluation import score_examples, retention_probes, target_count
from .training import TrainingConfig, TOKENIZER, backward_examples, validate_tokenizer

DAY = 86400
MAX_BYTES = 65536


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


def uid():
    return uuid4().hex


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def validate_text(title, body):
    if not isinstance(title, str) or not isinstance(body, str):
        raise ValueError('Text must be a string')
    if len(title) > 200 or len(body.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Title exceeds 200 characters or body exceeds 64 KiB')


def control_locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._control_lock:
            return method(self, *args, **kwargs)
    return wrapped


class Engine:
    def __init__(self, root: Path, config=None, max_steps=None, training=None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lockfile = FileLock(str(self.root / 'engine.lock'))
        self.lockfile.acquire(timeout=0)
        self._control_lock = threading.RLock()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        if max_steps is not None and (type(max_steps) is not int or max_steps < 1):
            self.lockfile.release()
            raise ValueError('max_steps must be positive')
        self._max_steps_override = max_steps
        try:
            self.checkpoint_dir = self.root / 'checkpoints'
            self.checkpoint_dir.mkdir(exist_ok=True, mode=0o700)
            self.store = Store(self.root / 'trace.sqlite3')
            with self.store.connect(True) as db:
                db.execute("UPDATE jobs SET status='failed', error='Interrupted before publication', "
                           "finished=? WHERE status='running'", (time.time(),))
            if not self.store.rows("SELECT value FROM meta WHERE key='current'"):
                config = config or Config()
                policy = training or TrainingConfig()
                if policy.context_prefix >= config.context:
                    raise ValueError('Context prefix must be shorter than model context')
                with torch.random.fork_rng(devices=[]):
                    torch.manual_seed(42)
                    model = GPT(config)
                    payload = {'format': 1, 'config': asdict(config),
                               'model': model.state_dict(),
                               'optimizer': optimizer(model, policy.learning_rate).state_dict(),
                               'training': asdict(policy), 'tokenizer': dict(TOKENIZER),
                               'exposure_totals': {'new_targets': 0, 'replay_targets': 0,
                                                   'complete': True},
                               'torch_rng': torch.get_rng_state(),
                               'sampler_rng': random.Random(42).getstate(),
                               'learned': {}, 'seen_hashes': [], 'updates': 0}
                    self._publish(payload, None, None, 'initial')
            payload = self.load_current()
            if config is not None and payload['config'] != asdict(config):
                raise ValueError('Existing model configuration differs; use a separate data directory')
            self.config = Config(**payload['config'])
            self.training = TrainingConfig(**payload.get('training', {}))
            if training is not None and training != self.training:
                raise ValueError('Existing training configuration differs; use a separate data directory')
            if self.training.context_prefix >= self.config.context:
                raise ValueError('Context prefix must be shorter than model context')
            self.max_steps = max_steps if max_steps is not None else self.training.max_steps
            self.prune()
        except BaseException:
            self.lockfile.release()
            raise

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
        self.lockfile.release()

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._worker, name='trace-trainer', daemon=True)
        self._thread.start()

    def _worker(self):
        last_prune = 0
        while not self._stop.is_set():
            if time.time() - last_prune > 60:
                self.prune()
                last_prune = time.time()
            if not self.train_next():
                self._stop.wait(0.2)

    def _current(self):
        return self.store.rows("SELECT value FROM meta WHERE key='current'")[0]['value']

    def _load(self, checkpoint_id):
        rows = self.store.rows('SELECT * FROM checkpoints WHERE id=?', (checkpoint_id,))
        if not rows or not rows[0]['available']:
            raise Missing('Checkpoint unavailable')
        path = self.checkpoint_dir / (rows[0]['id'] + '.pt')
        if not path.is_file() or digest(path.read_bytes()) != rows[0]['digest']:
            raise RuntimeError('Checkpoint integrity check failed')
        payload = torch.load(path, map_location='cpu', weights_only=True)
        if payload.get('format') != 1:
            raise ValueError('Unsupported checkpoint format')
        validate_tokenizer(payload.get('tokenizer'))
        return payload

    def load_current(self):
        with self._lock:
            return self._load(self._current())

    def _publish(self, payload, parent, previous, kind, job_id=None, metrics=None):
        checkpoint_id, now = uid(), time.time()
        final = self.checkpoint_dir / (checkpoint_id + '.pt')
        temporary = final.with_suffix('.tmp')
        try:
            with temporary.open('wb') as stream:
                torch.save(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, final)
            # Persist the directory rename on POSIX before publishing the pointer.
            if os.name == 'posix':
                descriptor = os.open(self.checkpoint_dir, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            sha = digest(final.read_bytes())
            with self.store.connect(True) as db:
                db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,1)',
                           (checkpoint_id, parent, previous, kind, now, now + 7*DAY, sha))
                if previous:
                    db.execute('UPDATE checkpoints SET expires=MAX(expires,?) WHERE id=?',
                               (now + 7*DAY, previous))
                db.execute("INSERT OR REPLACE INTO meta VALUES('current',?)", (checkpoint_id,))
                if job_id:
                    db.execute("UPDATE jobs SET status='succeeded', after_id=?, finished=?, "
                               "metrics=? WHERE id=?", (checkpoint_id, now,
                                                        json.dumps(metrics), job_id))
        finally:
            temporary.unlink(missing_ok=True)
        return self.store.rows('SELECT * FROM checkpoints WHERE id=?', (checkpoint_id,))[0]

    def create_page(self, title, body):
        validate_text(title, body)
        page_id, now = uid(), time.time()
        with self.store.connect(True) as db:
            db.execute('INSERT INTO pages VALUES(?,?,1,?,0)', (page_id, title, now))
            db.execute('INSERT INTO versions VALUES(?,?,1,?,?,?)',
                       (uid(), page_id, body, digest(body.encode()), now))
        return self.get_page(page_id)

    def get_page(self, page_id):
        rows = self.store.rows('SELECT p.*, v.id AS version_id, v.body, v.sha256 '
                               'FROM pages p JOIN versions v ON p.id=v.page_id '
                               'AND p.revision=v.revision WHERE p.id=? AND p.deleted=0', (page_id,))
        if not rows:
            raise Missing('Page not found')
        return rows[0]

    def pages(self):
        return self.store.rows('SELECT p.*, v.body, v.id AS version_id FROM pages p '
                               'JOIN versions v ON p.id=v.page_id AND p.revision=v.revision '
                               'WHERE deleted=0 ORDER BY updated DESC')

    def update_page(self, page_id, title, body, revision):
        validate_text(title, body)
        now = time.time()
        with self.store.connect(True) as db:
            row = db.execute('SELECT * FROM pages WHERE id=? AND deleted=0', (page_id,)).fetchone()
            if row is None:
                raise Missing('Page not found')
            if row['revision'] != revision:
                raise Conflict('Page changed elsewhere; reload before saving')
            current = db.execute('SELECT body FROM versions WHERE page_id=? AND revision=?',
                                 (page_id, revision)).fetchone()
            if row['title'] != title or current['body'] != body:
                db.execute('UPDATE pages SET title=?, revision=revision+1, updated=? WHERE id=?',
                           (title, now, page_id))
                db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?)',
                           (uid(), page_id, revision + 1, body, digest(body.encode()), now))
        return self.get_page(page_id)

    @control_locked
    def delete_page(self, page_id):
        with self.store.connect(True) as db:
            if not db.execute('UPDATE pages SET deleted=1 WHERE id=? AND deleted=0',
                              (page_id,)).rowcount:
                raise Missing('Page not found')
            db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE status='queued' "
                       'AND version_id IN (SELECT id FROM versions WHERE page_id=?)',
                       (time.time(), page_id))

    @control_locked
    def feed(self, page_id, revision, repeats, request_key):
        if not 1 <= repeats <= 10 or not 1 <= len(request_key) <= 128:
            raise ValueError('Invalid feed settings')
        signature = json.dumps([page_id, revision, repeats])
        with self.store.connect(True) as db:
            existing = db.execute('SELECT * FROM jobs WHERE request_key=?',
                                  (request_key,)).fetchone()
            if existing:
                if existing['signature'] != signature:
                    raise Conflict('Request key reused with different content')
                return dict(existing)
            version = db.execute('SELECT v.* FROM versions v JOIN pages p ON p.id=v.page_id '
                                 'WHERE p.id=? AND v.revision=? AND p.deleted=0',
                                 (page_id, revision)).fetchone()
            if version is None:
                raise Missing('Page revision not found')
            if not version['body'].strip():
                raise ValueError('Empty page cannot be trained')
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0] >= 64:
                raise Conflict('Training queue is full')
            job_id = uid()
            db.execute('INSERT INTO jobs(id,request_key,signature,version_id,repeats,status,created) '
                       "VALUES(?,?,?,?,?,'queued',?)",
                       (job_id, request_key, signature, version['id'], repeats, time.time()))
        return self.store.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]

    @control_locked
    def cancel(self, job_id):
        with self.store.connect(True) as db:
            if not db.execute("UPDATE jobs SET status='cancelled', finished=? "
                              "WHERE id=? AND status='queued'", (time.time(), job_id)).rowcount:
                raise Conflict('Only queued jobs can be cancelled')

    @staticmethod
    def _score(model, text):
        # Fixed non-overlapping evaluation protocol, independent of training overlap.
        return score_examples(model, windows(text, model.config.context))

    def train_next(self):
        with self._lock:
            with self.store.connect(True) as db:
                job = db.execute("SELECT j.*,v.body,v.page_id,v.sha256 FROM jobs j "
                                 "JOIN versions v ON v.id=j.version_id WHERE j.status='queued' "
                                 'ORDER BY j.created LIMIT 1').fetchone()
                if job is None:
                    return False
                job = dict(job)
                before = self._current()
                db.execute("UPDATE jobs SET status='running', started=?, before_id=? WHERE id=?",
                           (time.time(), before, job['id']))
            wall_start, cpu_start = time.perf_counter(), time.process_time()
            try:
                with torch.random.fork_rng(devices=[]):
                    payload = self._load(before)
                    policy = TrainingConfig(**payload.get('training', {}))
                    model = GPT(Config(**payload['config']))
                    if policy.context_prefix >= model.config.context:
                        raise ValueError('Invalid context prefix')
                    model.load_state_dict(payload['model'])
                    opt = optimizer(model, policy.learning_rate)
                    opt.load_state_dict(payload['optimizer'])  # do NOT reset moments or LR
                    torch.set_rng_state(payload['torch_rng'])
                    rng = random.Random(); rng.setstate(payload['sampler_rng'])
                    old_versions = []
                    for pid, vid in payload['learned'].items():
                        if pid == job['page_id']:
                            continue
                        rows = self.store.rows('SELECT v.id,v.page_id,v.body,v.sha256 FROM versions v '
                                               'JOIN pages p ON p.id=v.page_id '
                                               'WHERE v.id=? AND p.deleted=0', (vid,))
                        old_versions.extend(rows)
                    before_loss = self._score(model, job['body'])
                    if not math.isfinite(before_loss):
                        raise RuntimeError('Non-finite pretraining evaluation')
                    probes = retention_probes(old_versions, model.config.context, policy)
                    probe_examples = [item[2] for item in probes]
                    retention_before = score_examples(model, probe_examples)
                    seen_before = job['sha256'] in payload.get('seen_hashes', [])
                    scope = ('repeated_body' if seen_before else
                             'edited_page' if job['page_id'] in payload['learned'] else
                             'first_seen_page_exact_hash_only')
                    metrics = {'loss_before': before_loss,
                               'prequential_loss': before_loss if scope.startswith('first_seen') else None,
                               'evaluation_scope': scope, 'quality': 'unverified',
                               'content_seen_before': seen_before,
                               'first_seen_body_bytes': 0 if seen_before else len(job['body'].encode()),
                               'training_policy': asdict(policy),
                               'learning_rate': opt.param_groups[0]['lr'],
                               'effective_max_steps': self.max_steps,
                               'evaluation_protocol': 'page-local-nonoverlap-v1',
                               'loss_unit': 'nats/target-token (bytes and EOS)'}
                    # Preserve the test-before-train observation even if training is interrupted.
                    with self.store.connect(True) as db:
                        db.execute('UPDATE jobs SET metrics=? WHERE id=?',
                                   (json.dumps(metrics), job['id']))
                    examples = windows(job['body'], model.config.context, policy.context_prefix)
                    schedule = []
                    for _ in range(job['repeats']):
                        cycle = examples.copy(); rng.shuffle(cycle); schedule.extend(cycle)
                    requested = (len(job['body'].encode()) + 1) * job['repeats']
                    new_targets = replay_targets = updates = 0
                    replay_ids = set()
                    train_start = time.perf_counter()
                    stop_reason = 'requested_targets_completed'
                    for new_example in schedule:
                        if updates >= self.max_steps:
                            stop_reason = 'step_safety_cap'; break
                        if (updates and policy.max_train_seconds and
                                time.perf_counter() - train_start >= policy.max_train_seconds):
                            stop_reason = 'time_budget'; break
                        if self._stop.is_set():
                            raise RuntimeError('Shutdown before publication')
                        self.get_page(job['page_id'])  # deletion stops future work at an update boundary
                        active = {r['id'] for r in self.store.rows('SELECT id FROM pages WHERE deleted=0')}
                        eligible = [r for r in old_versions if r['page_id'] in active]
                        batch = [new_example]
                        n_new = target_count(batch)
                        desired = math.floor((new_targets+n_new) * policy.replay_ratio /
                                             (1-policy.replay_ratio) + 1e-9)
                        added = 0
                        if eligible and desired > replay_targets:
                            old = rng.choice(eligible)
                            ex = replay_window(old['body'], model.config.context,
                                               desired-replay_targets, rng, policy.context_prefix)
                            batch.append(ex); added = target_count([ex]); replay_ids.add(old['id'])
                        step_start = time.perf_counter()
                        model.train(); opt.zero_grad(set_to_none=True)
                        backward_examples(model, batch, policy.batch_mode)
                        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
                        opt.step()
                        if not all(torch.isfinite(p).all() for p in model.parameters()):
                            raise RuntimeError('Non-finite parameters; update not published')
                        new_targets += n_new; replay_targets += added; updates += 1
                        if policy.duty_cycle < 1:
                            pause = (time.perf_counter()-step_start) * (1/policy.duty_cycle-1)
                            self._stop.wait(pause)
                    if self._stop.is_set():
                        raise RuntimeError('Shutdown before publication; no update applied')
                    self.get_page(job['page_id'])
                    after_loss = self._score(model, job['body'])
                    active = {r['id'] for r in self.store.rows('SELECT id FROM pages WHERE deleted=0')}
                    probe_deleted = any(item[0] not in active for item in probes)
                    retention_after = None if probe_deleted else score_examples(model, probe_examples)
                    if not math.isfinite(after_loss) or (retention_after is not None and
                                                         not math.isfinite(retention_after)):
                        raise RuntimeError('Non-finite evaluation; update not published')
                    if not all(torch.isfinite(v).all() for state in opt.state.values()
                               for v in state.values() if isinstance(v, torch.Tensor)):
                        raise RuntimeError('Non-finite optimizer state')
                    learned = dict(payload['learned']); learned[job['page_id']] = job['version_id']
                    totals = dict(payload.get('exposure_totals',
                                  {'new_targets': 0, 'replay_targets': 0, 'complete': False}))
                    totals['new_targets'] += new_targets; totals['replay_targets'] += replay_targets
                    metrics.update(loss_after=after_loss, new_targets=new_targets,
                                   replay_targets=replay_targets, requested_new_targets=requested,
                                   updates=updates, capped=new_targets < requested,
                                   stop_reason=stop_reason, unique_page_bytes=len(job['body'].encode()),
                                   replay_actual_ratio=replay_targets/(new_targets+replay_targets),
                                   replay_version_ids=sorted(replay_ids), exposure_totals=totals,
                                   retention={'scope': 'sampled-previously-trained-not-held-out',
                                              'page_ids': [item[0] for item in probes],
                                              'version_ids': [item[1] for item in probes],
                                              'targets': target_count(probe_examples),
                                              'loss_before': None if probe_deleted else retention_before,
                                              'loss_after': retention_after,
                                              'status': 'invalidated_by_deletion' if probe_deleted else
                                                        'measured' if probes else 'unavailable'},
                                   elapsed_seconds=time.perf_counter()-wall_start,
                                   process_cpu_seconds=time.process_time()-cpu_start,
                                   timing_scope='load-evaluate-train; excludes checkpoint publication',
                                   cpu_threads=torch.get_num_threads())
                    seen = sorted(set(payload.get('seen_hashes', [])) | {job['sha256']})
                    payload.update(seen_hashes=seen, model=model.state_dict(), optimizer=opt.state_dict(),
                                   torch_rng=torch.get_rng_state(), sampler_rng=rng.getstate(),
                                   learned=learned, updates=payload['updates']+updates,
                                   training=asdict(policy), tokenizer=dict(TOKENIZER), exposure_totals=totals)
                    self._publish(payload, before, before, 'train', job['id'], metrics)
            except Exception as exc:
                with self.store.connect(True) as db:
                    db.execute("UPDATE jobs SET status='failed',finished=?,error=? WHERE id=? "
                               "AND status='running'", (time.time(), type(exc).__name__ +
                               ': training failed; previous checkpoint preserved', job['id']))
            return True

    def evaluations(self):
        observations = []
        for job in self.history()['jobs']:
            if 'evaluation_scope' in job['metrics']:
                observations.append({k: job[k] for k in
                                     ('id', 'page_id', 'version_id', 'before_id', 'after_id',
                                      'created', 'status', 'metrics')})
        return {'observations': observations, 'limit': 200, 'includes_multiple_branches': True,
                'independent_validation': 'unavailable',
                'warning': 'First-seen is exact-hash/page identity only, not independence or novelty.'}

    def respond(self, page_id, revision, max_new_tokens=64, temperature=0.8, seed=42,
                checkpoint_id=None):
        if not 1 <= max_new_tokens <= 128 or not 0 <= temperature <= 2:
            raise ValueError('Invalid generation settings')
        p = self.get_page(page_id)
        if p['revision'] != revision:
            raise Conflict('Save/reload the page before generating')
        if not p['body'].strip():
            raise ValueError('Empty page')
        with self._lock, torch.random.fork_rng(devices=[]):
            cp = checkpoint_id or self._current()
            if checkpoint_id and cp != self._current():
                rows = self.store.rows('SELECT expires FROM checkpoints WHERE id=? AND available=1',
                                       (cp,))
                if not rows or rows[0]['expires'] <= time.time():
                    raise Missing('Checkpoint expired or unavailable')
            payload = self._load(cp)
            model = GPT(Config(**payload['config'])); model.load_state_dict(payload['model'])
            model.eval(); torch.manual_seed(seed)
            prefix = encode(p['body'])[:-1]
            context = prefix[-model.config.context:]
            tokens = []
            with torch.no_grad():
                for _ in range(max_new_tokens):
                    logits = model(torch.tensor([context]))[0, -1].clone()
                    logits[BOS] = -float('inf')
                    token = (int(logits.argmax()) if temperature == 0 else
                             int(torch.multinomial((logits/temperature).softmax(-1), 1)))
                    tokens.append(token)
                    if token == EOS:
                        break
                    context = (context + [token])[-model.config.context:]
            meta = {'seed': seed, 'temperature': temperature, 'context_used': min(len(prefix),
                    model.config.context), 'context_truncated': len(prefix)>model.config.context,
                    'max_new_tokens': max_new_tokens, 'untrained': payload['updates'] == 0}
            response_id = uid()
            with self.store.connect(True) as db:
                db.execute('INSERT INTO responses VALUES(?,?,?,?,?,?,?)',
                           (response_id, p['version_id'], cp, time.time(), json.dumps(tokens),
                            decode(tokens), json.dumps(meta)))
        return next(r for r in self.responses(page_id) if r['id'] == response_id)

    def responses(self, page_id):
        self.get_page(page_id)
        rows = self.store.rows('SELECT r.*,v.revision FROM responses r JOIN versions v '
                               'ON v.id=r.version_id WHERE v.page_id=? '
                               'ORDER BY r.created DESC LIMIT 100', (page_id,))
        for row in rows:
            row['raw_tokens'] = json.loads(row['raw_tokens'])
            row['metadata'] = json.loads(row['metadata'])
        return rows

    def status(self):
        current = self._current()
        counts = self.store.rows("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status")
        return {'checkpoint_id': current, 'config': asdict(self.config),
                'parameters': 12*self.config.layers*self.config.width**2 +
                (258+2*self.config.layers+1)*self.config.width,
                'jobs': {row['status']: row['count'] for row in counts},
                'retention_days': 7, 'device': 'cpu', 'precision': 'float32',
                'training': asdict(self.training), 'cpu_threads': torch.get_num_threads()}

    def history(self):
        jobs = self.store.rows('SELECT j.*,p.title,v.revision,v.page_id FROM jobs j '
                              'JOIN versions v ON j.version_id=v.id JOIN pages p ON p.id=v.page_id '
                              'ORDER BY j.created DESC LIMIT 200')
        for job in jobs:
            job['metrics'] = json.loads(job['metrics'])
        checkpoints = self.store.rows('SELECT * FROM checkpoints ORDER BY created DESC LIMIT 200')
        current = self._current()
        for cp in checkpoints:
            cp['current'] = cp['id'] == current
            cp['restorable'] = bool(cp['available'] and
                                    (cp['current'] or cp['expires'] > time.time()))
        return {'jobs': jobs, 'checkpoints': checkpoints, 'current': current}

    @control_locked
    def restore(self, checkpoint_id):
        with self._lock:
            # Queue mutation and restore are serialized across the publication below.
            rows = self.store.rows('SELECT * FROM checkpoints WHERE id=?', (checkpoint_id,))
            if not rows or not rows[0]['available']:
                raise Missing('Checkpoint unavailable')
            current = self._current()
            if rows[0]['expires'] <= time.time() and checkpoint_id != current:
                raise Missing('Checkpoint expired')
            if self.store.rows("SELECT id FROM jobs WHERE status IN ('queued','running')"):
                raise Conflict('Cancel queued jobs and wait for training before restoring')
            payload = self._load(checkpoint_id)
            restored = self._publish(payload, checkpoint_id, current, 'restore')
            self.training = TrainingConfig(**payload.get('training', {}))
            if self._max_steps_override is None:
                self.max_steps = self.training.max_steps
            return restored

    def prune(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            current = self._current()
            with self.store.connect(True) as db:
                db.execute('UPDATE checkpoints SET available=0 WHERE expires<=? AND id<>?',
                           (now, current))
            # Also collect orphaned files left by a crash before database publication.
            available = {r['id'] for r in self.store.rows(
                'SELECT id FROM checkpoints WHERE available=1')}
            for path in self.checkpoint_dir.glob('*.pt'):
                if path.stem not in available:
                    path.unlink(missing_ok=True)
            for path in self.checkpoint_dir.glob('*.tmp'):
                path.unlink(missing_ok=True)
